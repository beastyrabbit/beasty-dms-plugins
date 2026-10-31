#!/usr/bin/env python3
"""Model adapter for the Quick AI DMS plugin.

Fast answers come from OpenRouter. "Better answer" reruns the request on
GPT-6.1 Sol through the CLIProxyAPI provider configured for Codex.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
# Benchmarked on 2026-10-31 as the fastest OpenRouter models for short answers.
FAST_MODELS = (
    "deepseek/deepseek-v4.1-flash",
    "deepseek/deepseek-v4.1-flash-1017",
    "xiaomi/mimo-v2.6-flash",
    "z-ai/glm-5.3-flash",
    "google/gemini-3.5-flash-lite",
    "anthropic/claude-haiku-5.5",
)
EFFORTS = ("off", "low", "medium", "high")
DEFAULT_EFFORT = "off"
# These reject reasoning {enabled: false}; "off" runs them at "low" instead.
THINKING_REQUIRED = ("z-ai/glm-5.3-flash", "google/gemini-3.5-flash-lite")
BETTER_MODEL = "gpt-6.1-sol"
BETTER_EFFORT = "medium"
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
FAST_TIMEOUT_SECONDS = 30
BETTER_TIMEOUT_SECONDS = 90
SEARCH_MAX_RESULTS = 3
MAX_ASK_CHARS = 4000
MAX_TRANSLATE_CHARS = 1200
MAX_SUMMARY_CHARS = 24000
SCHEMA_PATH = Path(__file__).with_name("translation.schema.json")


class RequestError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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


def translation_schema() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema.pop("$schema", None)
    return schema


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


def proxy_provider() -> dict[str, Any] | None:
    """Return the custom model provider (CLIProxyAPI) from the user's Codex config."""
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        config = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    provider = config.get("model_providers", {}).get(config.get("model_provider"))
    if not isinstance(provider, dict) or not provider.get("base_url"):
        return None
    return provider


def provider_token(provider: dict[str, Any]) -> str | None:
    if provider.get("env_key"):
        return os.environ.get(provider["env_key"])
    return provider.get("experimental_bearer_token")


def openrouter_base() -> str:
    return os.environ.get("QUICK_AI_OPENROUTER_URL", OPENROUTER_URL).rstrip("/")


def fast_request(mode: str, prompt: str, model: str, effort: str) -> dict[str, Any]:
    """OpenRouter chat completions body for the fast path."""
    if effort == "off" and model in THINKING_REQUIRED:
        effort = "low"
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "reasoning": {"enabled": False} if effort == "off" else {"effort": effort},
        "provider": {"sort": "latency"},
    }
    if mode == "ask":
        # The model decides when to search; OpenRouter bills only actual searches.
        body["tools"] = [
            {
                "type": "openrouter:web_search",
                "parameters": {"max_results": SEARCH_MAX_RESULTS},
            }
        ]
    elif mode == "translate":
        body["provider"]["require_parameters"] = True
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "translation",
                "strict": True,
                "schema": translation_schema(),
            },
        }
    return body


def better_request(mode: str, prompt: str) -> dict[str, Any]:
    """CLIProxyAPI Responses body for the better-answer path."""
    body: dict[str, Any] = {
        "model": BETTER_MODEL,
        "input": prompt,
        "reasoning": {"effort": BETTER_EFFORT},
        "store": False,
    }
    if mode == "ask":
        body["tools"] = [{"type": "web_search"}]
    elif mode == "translate":
        body["text"] = {
            "format": {
                "type": "json_schema",
                "name": "translation",
                "strict": True,
                "schema": translation_schema(),
            }
        }
    return body


def friendly_http_error(status: int, details: str, service: str) -> RequestError:
    lowered = details.lower()
    if status in (401, 403):
        return RequestError("auth", f"{service} rejected Quick AI's API key.")
    if status == 402:
        return RequestError("credits", f"{service} has no credits left.")
    if status == 429:
        return RequestError(
            "rate_limit", "The model is temporarily rate-limited. Try again shortly."
        )
    if "reasoning" in lowered and any(
        word in lowered
        for word in ("mandatory", "unsupported", "not supported", "invalid value")
    ):
        return RequestError(
            "effort",
            "This model does not support the chosen thinking level. "
            "Pick another in the Quick AI settings.",
        )
    if status == 404 or (
        "model" in lowered
        and any(word in lowered for word in ("not found", "unavailable", "invalid"))
    ):
        return RequestError(
            "model",
            "The configured model is unavailable. Pick another in the settings.",
        )
    return RequestError("http", f"{service} could not complete the request. Try again.")


def _deadline_reached(_signum: int, _frame: Any) -> None:
    raise TimeoutError


def post_json(
    url: str, body: dict[str, Any], token: str, timeout: int, service: str
) -> dict[str, Any]:
    # urlopen's timeout only bounds socket inactivity; the alarm bounds the
    # whole request so a trickling response cannot keep the popup loading.
    signal.signal(signal.SIGALRM, _deadline_reached)
    signal.alarm(timeout)
    try:
        return _post_json(url, body, token, timeout, service)
    finally:
        signal.alarm(0)


def _post_json(
    url: str, body: dict[str, Any], token: str, timeout: int, service: str
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            details = error.read().decode("utf-8", "replace")
        raise friendly_http_error(error.code, details, service) from None
    except TimeoutError:
        raise RequestError(
            "timeout", f"No answer after {timeout} seconds. Try again."
        ) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise RequestError(
                "timeout", f"No answer after {timeout} seconds. Try again."
            ) from None
        raise RequestError(
            "network", f"Could not reach {service}. Check the network connection."
        ) from None
    except ValueError:
        raise RequestError(
            "invalid_response", f"{service} returned a malformed response."
        ) from None


def chat_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or [{}]
    content = (choices[0].get("message") or {}).get("content")
    return content if isinstance(content, str) else ""


def responses_text(payload: dict[str, Any]) -> str:
    return "".join(
        part.get("text", "")
        for item in payload.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def run_fast(mode: str, prompt: str, model: str, effort: str) -> str:
    token = os.environ.get(OPENROUTER_KEY_ENV)
    if not token:
        raise RequestError(
            "auth", f"{OPENROUTER_KEY_ENV} is not set in the DMS environment."
        )
    payload = post_json(
        openrouter_base() + "/chat/completions",
        fast_request(mode, prompt, model, effort),
        token,
        FAST_TIMEOUT_SECONDS,
        "OpenRouter",
    )
    return chat_text(payload)


def run_better(mode: str, prompt: str) -> str:
    provider = proxy_provider()
    if provider is None:
        raise RequestError(
            "proxy",
            "Better answer needs the CLIProxyAPI provider in ~/.codex/config.toml.",
        )
    payload = post_json(
        provider["base_url"].rstrip("/") + "/responses",
        better_request(mode, prompt),
        provider_token(provider) or "",
        BETTER_TIMEOUT_SECONDS,
        provider.get("name", "CLIProxyAPI"),
    )
    return responses_text(payload)


def execute(
    mode: str,
    query: str,
    target: str,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    better: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    prompt = prompt_for(mode, query, target)
    try:
        if better:
            output = run_better(mode, prompt).strip()
        else:
            output = run_fast(mode, prompt, model, effort).strip()
    except RequestError as error:
        return _error(mode, error.code, error.message, started)

    if not output:
        return _error(
            mode, "empty", "The model returned an empty response. Try again.", started
        )

    result: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "model": BETTER_MODEL if better else model,
        "better": better,
        "elapsedMs": round((time.monotonic() - started) * 1000),
    }
    if mode != "translate":
        result["answer"] = output
        return result

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        parsed = None
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
        "--check", action="store_true", help="check that the OpenRouter key works"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model")
    parser.add_argument(
        "--effort",
        choices=("", *EFFORTS),
        default=DEFAULT_EFFORT,
        help="thinking level for the fast model",
    )
    parser.add_argument(
        "--better",
        action="store_true",
        help=f"answer with {BETTER_MODEL} at {BETTER_EFFORT} thinking instead",
    )
    parser.add_argument("--mode", choices=("ask", "translate", "summarize"))
    parser.add_argument("--target", choices=("auto", "en", "de"), default="auto")
    parser.add_argument("--query")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="read the query from stdin (keeps clipboard text out of arguments)",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="summarize the current plain-text Wayland clipboard",
    )
    args = parser.parse_args(argv)
    # Empty values (no setting saved yet) mean the defaults.
    args.model = args.model or DEFAULT_MODEL
    args.effort = args.effort or DEFAULT_EFFORT
    if not MODEL_PATTERN.match(args.model):
        parser.error("invalid --model")
    if args.check:
        return args
    if not args.mode:
        parser.error("--mode is required unless --check is used")
    if args.clipboard and args.mode != "summarize":
        parser.error("--clipboard is only valid with --mode summarize")
    if sum((args.clipboard, args.stdin, args.query is not None)) > 1:
        parser.error("use only one of --query, --stdin, and --clipboard")
    if args.stdin:
        args.query = sys.stdin.read()
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
    token = os.environ.get(OPENROUTER_KEY_ENV)
    if not token:
        return {
            "ok": False,
            "message": f"{OPENROUTER_KEY_ENV} is not set in the DMS environment.",
        }
    request = urllib.request.Request(
        openrouter_base() + "/key", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=8):
            pass
    except urllib.error.HTTPError as error:
        error.close()
        return {
            "ok": False,
            "message": f"OpenRouter rejected {OPENROUTER_KEY_ENV} (HTTP {error.code}).",
        }
    except (OSError, ValueError):
        return {"ok": False, "message": "Could not reach OpenRouter."}
    return {"ok": True}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        print(json.dumps(check_installation()))
        return 0
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
    result = execute(
        args.mode, args.query, args.target, args.model, args.effort, args.better
    )
    if args.clipboard:
        # Better answer resends this text over stdin instead of rereading a
        # clipboard that may have changed (for example after Copy).
        result["input"] = clipboard_text
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
