#!/usr/bin/env python3
"""Isolated Codex CLI adapter for the Quick AI DMS plugin."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

ASK_MODEL = "gpt-5.6-terra"
TRANSLATE_MODEL = "gpt-5.6-terra"
SUMMARY_MODEL = "gpt-5.6-terra"
ASK_TIMEOUT_SECONDS = 30
TRANSLATE_TIMEOUT_SECONDS = 30
SUMMARY_TIMEOUT_SECONDS = 30
MAX_ASK_CHARS = 4000
MAX_TRANSLATE_CHARS = 1200
MAX_SUMMARY_CHARS = 24000
SCHEMA_PATH = Path(__file__).with_name("translation.schema.json")

_child: subprocess.Popen[str] | None = None


def ask_prompt(query: str) -> str:
    return f"""Answer this quick question directly and concisely, in the same language as the question.
Use web search only when current or changing information matters. If you use the web, include compact source links.
Prefer a useful answer under 120 words. Do not add a greeting, preamble, or follow-up offer.

Question:
{query}"""


def translate_prompt(query: str, target: str) -> str:
    direction = {
        "auto": (
            "Detect English versus German. Translate clearly German input to English and "
            "clearly English input to German. If detection is uncertain, choose the most "
            "useful direction and mark it ambiguous."
        ),
        "en": "Translate into English, regardless of the source language.",
        "de": "Translate into German, regardless of the source language.",
    }[target]
    return f"""You are a compact English-German dictionary and translation assistant.
{direction}
Return the most natural, common translation first. Give at most three genuinely distinct alternatives with short nuance notes.
Keep grammar and usage concise. Mention ambiguity or a false-friend risk only when useful; otherwise use an empty ambiguityNote.
For a phrase or sentence, translate it naturally rather than word-for-word. Follow the supplied JSON schema exactly.

Text:
{query}"""


def summary_prompt(query: str) -> str:
    return f"""Summarize the following text in the same language as the source.
Start with a one- or two-sentence overview, then use compact bullets for the key points.
Preserve important facts, numbers, dates, decisions, and action items. Do not add information.
Do not add a greeting, preamble, or follow-up offer.

Text:
{query}"""


def prompt_for(mode: str, query: str, target: str) -> str:
    if mode == "ask":
        return ask_prompt(query)
    if mode == "summarize":
        return summary_prompt(query)
    return translate_prompt(query, target)


def resolve_codex() -> str | None:
    candidates = [
        os.environ.get("QUICK_AI_CODEX"),
        shutil.which("codex"),
        str(Path.home() / ".npm-global" / "bin" / "codex"),
        str(Path.home() / ".local" / "bin" / "codex"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_wl_paste() -> str | None:
    candidates = [
        os.environ.get("QUICK_AI_WL_PASTE"),
        shutil.which("wl-paste"),
        "/usr/bin/wl-paste",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def read_clipboard_text() -> tuple[str | None, str | None]:
    wl_paste = resolve_wl_paste()
    if wl_paste is None:
        return None, "wl-paste is not installed, so the clipboard cannot be read."
    try:
        result = subprocess.run(
            [wl_paste, "--no-newline", "--type", "text"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "Could not read plain text from the clipboard."
    text = result.stdout.strip()
    if result.returncode != 0 or not text:
        return None, "The clipboard does not contain plain text to summarize."
    if len(text) > MAX_SUMMARY_CHARS:
        return (
            None,
            f"Clipboard text is too long (maximum {MAX_SUMMARY_CHARS} characters).",
        )
    return text, None


def build_command(
    mode: str,
    target: str,
    workdir: str,
    codex_path: str = "codex",
) -> list[str]:
    del target
    model = {
        "ask": ASK_MODEL,
        "translate": TRANSLATE_MODEL,
        "summarize": SUMMARY_MODEL,
    }[mode]
    command = [
        codex_path,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--disable",
        "fast_mode",
        "--disable",
        "apps",
        "--disable",
        "goals",
        "--disable",
        "hooks",
        "--disable",
        "memories",
        "--disable",
        "multi_agent",
        "--disable",
        "plugins",
        "--disable",
        "remote_plugin",
        "--disable",
        "shell_tool",
        "--color",
        "never",
        "--model",
        model,
        "-c",
        'model_reasoning_effort="low"',
        "-C",
        workdir,
    ]
    if mode == "ask":
        command.insert(1, "--search")
    elif mode == "translate":
        command.extend(["--output-schema", str(SCHEMA_PATH)])
    # A lone dash tells `codex exec` to read the prompt from stdin. This keeps
    # questions and clipboard contents out of process listings.
    command.append("-")
    return command


def friendly_error(stderr: str, return_code: int) -> tuple[str, str]:
    lowered = stderr.lower()
    if "not logged in" in lowered or "login" in lowered and "required" in lowered:
        return "auth", "Codex is not signed in. Run `codex login`, then try again."
    if (
        "rate limit" in lowered
        or "too many requests" in lowered
        or "usage limit" in lowered
    ):
        return "rate_limit", "The model is temporarily rate-limited. Try again shortly."
    if any(word in lowered for word in ("network", "connection", "dns", "timed out")):
        return (
            "network",
            "Could not reach Codex. Check the network connection and retry.",
        )
    if "model" in lowered and any(
        word in lowered for word in ("not found", "unsupported", "unavailable")
    ):
        return "model", "The configured model is unavailable. Update Codex and retry."
    if return_code < 0:
        return "cancelled", "The request was cancelled."
    return "codex", "Codex could not complete the request. Try again."


def terminate_child() -> None:
    global _child
    if _child is None or _child.poll() is not None:
        return
    try:
        os.killpg(_child.pid, signal.SIGTERM)
        _child.wait(timeout=1.5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(_child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _signal_handler(_signum: int, _frame: Any) -> None:
    terminate_child()
    raise SystemExit(130)


def execute(mode: str, query: str, target: str) -> dict[str, Any]:
    global _child
    started = time.monotonic()
    model = {
        "ask": ASK_MODEL,
        "translate": TRANSLATE_MODEL,
        "summarize": SUMMARY_MODEL,
    }[mode]
    timeout = {
        "ask": ASK_TIMEOUT_SECONDS,
        "translate": TRANSLATE_TIMEOUT_SECONDS,
        "summarize": SUMMARY_TIMEOUT_SECONDS,
    }[mode]
    prompt = prompt_for(mode, query, target)

    codex_path = resolve_codex()
    if codex_path is None:
        return _error(
            mode, "missing_codex", "Codex CLI is not installed or not on PATH.", started
        )

    with tempfile.TemporaryDirectory(prefix="dms-quick-ai-") as workdir:
        command = build_command(mode, target, workdir, codex_path)
        try:
            _child = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            stdout, stderr = _child.communicate(input=prompt, timeout=timeout)
        except FileNotFoundError:
            return _error(
                mode,
                "missing_codex",
                "Codex CLI is not installed or not on PATH.",
                started,
            )
        except subprocess.TimeoutExpired:
            terminate_child()
            return _error(
                mode,
                "timeout",
                f"No answer after {timeout} seconds. Try again.",
                started,
            )
        finally:
            process = _child
            _child = None

    if process.returncode != 0:
        code, message = friendly_error(stderr, process.returncode)
        return _error(mode, code, message, started)

    output = stdout.strip()
    if not output:
        return _error(
            mode, "empty", "Codex returned an empty response. Try again.", started
        )

    result: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "model": model,
        "elapsedMs": round((time.monotonic() - started) * 1000),
    }
    if mode != "translate":
        result["answer"] = output
        return result

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return _error(
            mode,
            "invalid_response",
            "The translation response was malformed. Try again.",
            started,
        )
    if not isinstance(parsed, dict):
        return _error(
            mode,
            "invalid_response",
            "The translation response was malformed. Try again.",
            started,
        )
    result["result"] = parsed
    return result


def _error(mode: str, code: str, message: str, started: float) -> dict[str, Any]:
    return {
        "ok": False,
        "mode": mode,
        "code": code,
        "message": message,
        "elapsedMs": round((time.monotonic() - started) * 1000),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="check Codex login and model availability"
    )
    parser.add_argument("--mode", choices=("ask", "translate", "summarize"))
    parser.add_argument("--target", choices=("auto", "en", "de"), default="auto")
    parser.add_argument("--query")
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="summarize the current plain-text Wayland clipboard",
    )
    args = parser.parse_args(argv)
    if args.check:
        return args
    if not args.mode:
        parser.error("--mode is required unless --check is used")
    if args.clipboard and args.mode != "summarize":
        parser.error("--clipboard is only valid with --mode summarize")
    if not args.clipboard and args.query is None:
        parser.error("--query is required unless --check or --clipboard is used")
    if args.clipboard:
        return args
    args.query = args.query.strip()
    limit = {
        "ask": MAX_ASK_CHARS,
        "translate": MAX_TRANSLATE_CHARS,
        "summarize": MAX_SUMMARY_CHARS,
    }[args.mode]
    if not args.query:
        parser.error("query must not be empty")
    if len(args.query) > limit:
        parser.error(f"query must be at most {limit} characters")
    return args


def check_installation() -> dict[str, Any]:
    codex_path = resolve_codex()
    if codex_path is None:
        return {"ok": False, "message": "Codex CLI is not installed."}
    try:
        login = subprocess.run(
            [codex_path, "login", "status"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        login_output = login.stdout + login.stderr
        if login.returncode != 0 or "logged in" not in login_output.lower():
            return {
                "ok": False,
                "message": "Codex is not signed in. Run `codex login`.",
            }
        models = subprocess.run(
            [codex_path, "debug", "models"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "message": "Could not inspect the Codex installation."}
    output = models.stdout + models.stderr
    if (
        models.returncode != 0
        or ASK_MODEL not in output
        or TRANSLATE_MODEL not in output
        or SUMMARY_MODEL not in output
    ):
        return {
            "ok": False,
            "message": "The configured Quick AI model is unavailable. Update Codex and retry.",
        }
    return {"ok": True}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        print(json.dumps(check_installation()))
        return 0
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _signal_handler)
    if args.clipboard:
        clipboard_text, clipboard_error = read_clipboard_text()
        if clipboard_error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mode": "summarize",
                        "code": "clipboard",
                        "message": clipboard_error,
                        "elapsedMs": 0,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        args.query = clipboard_text
    print(json.dumps(execute(args.mode, args.query, args.target), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
