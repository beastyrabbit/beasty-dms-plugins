from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "QuickAI" / "quick_ai.py"
DAEMON_PATH = ROOT / "QuickAI" / "QuickAIDaemon.qml"
SPEC = importlib.util.spec_from_file_location("quick_ai", MODULE_PATH)
assert SPEC and SPEC.loader
quick_ai = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = quick_ai
SPEC.loader.exec_module(quick_ai)


class FakeProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 12345

    def communicate(
        self, input: str | None = None, timeout: int = 0
    ) -> tuple[str, str]:
        self.input = input
        self.timeout = timeout
        return self._stdout, self._stderr

    def poll(self) -> int:
        return self.returncode


class CommandTests(unittest.TestCase):
    def test_ask_uses_terra_search_low_and_standard_speed(self) -> None:
        command = quick_ai.build_command("ask", "auto", "/tmp/work")
        self.assertIn("gpt-5.6-terra", command)
        self.assertIn("--search", command)
        self.assertLess(command.index("--search"), command.index("exec"))
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertIn("fast_mode", command)
        self.assertNotIn("--output-schema", command)
        self.assertEqual(command[-1], "-")

    def test_translation_uses_terra_schema_without_search(self) -> None:
        command = quick_ai.build_command("translate", "auto", "/tmp/work")
        self.assertIn("gpt-5.6-terra", command)
        self.assertNotIn("--search", command)
        self.assertEqual(
            command[command.index("--output-schema") + 1], str(quick_ai.SCHEMA_PATH)
        )
        self.assertNotIn("apfelbaum", command)
        self.assertEqual(command[-1], "-")
        self.assertEqual(quick_ai.TRANSLATE_TIMEOUT_SECONDS, 30)

    def test_summary_uses_terra_without_search_or_schema(self) -> None:
        command = quick_ai.build_command("summarize", "auto", "/tmp/work")
        self.assertIn("gpt-5.6-terra", command)
        self.assertNotIn("--search", command)
        self.assertNotIn("--output-schema", command)
        self.assertEqual(command[-1], "-")

    def test_query_is_not_exposed_in_process_arguments(self) -> None:
        query = 'hello; echo "$HOME"'
        command = quick_ai.build_command("ask", "auto", "/tmp/work")
        self.assertNotIn(query, command)
        self.assertNotIn("sh", command)

    @patch.object(quick_ai.shutil, "which", return_value=None)
    @patch.object(quick_ai.Path, "home")
    def test_resolves_npm_global_codex_for_dms_path(self, home, _which) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home.return_value = Path(directory)
            executable = Path(directory) / ".npm-global" / "bin" / "codex"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
            self.assertEqual(quick_ai.resolve_codex(), str(executable))


class StructuredOutputTests(unittest.TestCase):
    def test_schema_requires_a_copyable_translation_string(self) -> None:
        schema = json.loads(quick_ai.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertIn("translation", schema["required"])
        self.assertEqual(schema["properties"]["translation"]["type"], "string")


class OutputOnlyUiTests(unittest.TestCase):
    def test_popup_is_read_only_and_escape_closes_it(self) -> None:
        qml = DAEMON_PATH.read_text(encoding="utf-8")
        self.assertNotIn("TextArea {", qml)
        self.assertNotIn('text: root.status === "error" ? "Retry" : "Run"', qml)
        self.assertIn("event.key === Qt.Key_Escape", qml)
        self.assertIn("quickModal.close()", qml)


class ResponseTests(unittest.TestCase):
    @patch.object(quick_ai.subprocess, "Popen")
    @patch.object(quick_ai, "resolve_codex", return_value="/usr/bin/codex")
    def test_ask_success(self, _resolve, popen) -> None:
        popen.return_value = FakeProcess("A short answer")
        result = quick_ai.execute("ask", "Question", "auto")
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "A short answer")
        self.assertEqual(result["model"], "gpt-5.6-terra")
        self.assertIs(popen.call_args.kwargs["stdin"], quick_ai.subprocess.PIPE)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertIn("Question", popen.return_value.input)

    @patch.object(quick_ai.subprocess, "Popen")
    @patch.object(quick_ai, "resolve_codex", return_value="/usr/bin/codex")
    def test_translation_success(self, _resolve, popen) -> None:
        payload = {
            "translation": "apple tree",
            "sourceLanguage": "de",
            "targetLanguage": "en",
            "alternatives": [],
            "grammar": "noun",
            "usage": "common",
            "ambiguityNote": "",
            "ambiguous": False,
        }
        popen.return_value = FakeProcess(json.dumps(payload))
        result = quick_ai.execute("translate", "apfelbaum", "auto")
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["translation"], "apple tree")
        self.assertIn("apfelbaum", popen.return_value.input)

    @patch.object(quick_ai.subprocess, "Popen")
    @patch.object(quick_ai, "resolve_codex", return_value="/usr/bin/codex")
    def test_summary_success(self, _resolve, popen) -> None:
        popen.return_value = FakeProcess("Overview.\n\n- First point")
        result = quick_ai.execute("summarize", "A long source", "auto")
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "Overview.\n\n- First point")
        self.assertIn("A long source", popen.return_value.input)
        self.assertNotIn("--search", popen.call_args.args[0])

    @patch.object(quick_ai, "terminate_child")
    @patch.object(quick_ai.subprocess, "Popen")
    @patch.object(quick_ai, "resolve_codex", return_value="/usr/bin/codex")
    def test_translation_timeout_is_friendly(self, _resolve, popen, terminate) -> None:
        process = FakeProcess("")
        process.communicate = unittest.mock.Mock(
            side_effect=quick_ai.subprocess.TimeoutExpired([], 30)
        )
        popen.return_value = process
        result = quick_ai.execute("translate", "apfelbaum", "auto")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "timeout")
        self.assertIn("30 seconds", result["message"])
        terminate.assert_called_once()

    @patch.object(quick_ai.subprocess, "Popen")
    @patch.object(quick_ai, "resolve_codex", return_value="/usr/bin/codex")
    def test_malformed_translation_is_friendly(self, _resolve, popen) -> None:
        popen.return_value = FakeProcess("not json")
        result = quick_ai.execute("translate", "Entwurf", "auto")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_response")

    def test_error_mapping(self) -> None:
        self.assertEqual(
            quick_ai.friendly_error("rate limit exceeded", 1)[0], "rate_limit"
        )
        self.assertEqual(
            quick_ai.friendly_error("network connection failed", 1)[0], "network"
        )
        self.assertEqual(quick_ai.friendly_error("login required", 1)[0], "auth")

    @patch.object(quick_ai, "resolve_codex", return_value="/usr/bin/codex")
    @patch.object(quick_ai.subprocess, "run")
    def test_installation_check_accepts_login_on_stderr(self, run, _resolve) -> None:
        run.side_effect = [
            quick_ai.subprocess.CompletedProcess([], 0, "", "Logged in using ChatGPT"),
            quick_ai.subprocess.CompletedProcess([], 0, "gpt-5.6-terra\n", ""),
        ]
        self.assertEqual(quick_ai.check_installation(), {"ok": True})


class ArgumentTests(unittest.TestCase):
    def test_blank_query_is_rejected(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                quick_ai.parse_args(["--mode", "ask", "--query", "   "])

    def test_translation_limit_is_rejected(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                quick_ai.parse_args(["--mode", "translate", "--query", "x" * 1201])

    def test_clipboard_summary_does_not_require_query(self) -> None:
        args = quick_ai.parse_args(["--mode", "summarize", "--clipboard"])
        self.assertTrue(args.clipboard)
        self.assertIsNone(args.query)

    def test_clipboard_is_rejected_for_ask(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                quick_ai.parse_args(["--mode", "ask", "--clipboard"])

    def test_summary_limit_is_rejected(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                quick_ai.parse_args(["--mode", "summarize", "--query", "x" * 24001])


class ClipboardTests(unittest.TestCase):
    @patch.object(quick_ai, "resolve_wl_paste", return_value="/usr/bin/wl-paste")
    @patch.object(quick_ai.subprocess, "run")
    def test_reads_plain_text_clipboard(self, run, _resolve) -> None:
        run.return_value = quick_ai.subprocess.CompletedProcess(
            [], 0, "Private clipboard text", ""
        )
        text, error = quick_ai.read_clipboard_text()
        self.assertEqual(text, "Private clipboard text")
        self.assertIsNone(error)
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/wl-paste", "--no-newline", "--type", "text"],
        )

    @patch.object(quick_ai, "resolve_wl_paste", return_value="/usr/bin/wl-paste")
    @patch.object(quick_ai.subprocess, "run")
    def test_empty_clipboard_is_friendly(self, run, _resolve) -> None:
        run.return_value = quick_ai.subprocess.CompletedProcess([], 1, "", "empty")
        text, error = quick_ai.read_clipboard_text()
        self.assertIsNone(text)
        self.assertIn("does not contain plain text", error)


class DmsProcessIntegrationTests(unittest.TestCase):
    def test_open_parent_stdin_is_not_forwarded_to_codex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_codex = Path(directory) / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "sys.stdin.read()\n"
                "print('A short answer')\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o700)
            environment = dict(os.environ)
            environment["QUICK_AI_CODEX"] = str(fake_codex)
            process = quick_ai.subprocess.Popen(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--mode",
                    "ask",
                    "--query",
                    "Question",
                ],
                stdin=quick_ai.subprocess.PIPE,
                stdout=quick_ai.subprocess.PIPE,
                stderr=quick_ai.subprocess.PIPE,
                text=True,
                env=environment,
            )
            try:
                return_code = process.wait(timeout=4)
                output = process.stdout.read() if process.stdout else ""
                if process.stderr:
                    process.stderr.read()
            except quick_ai.subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
                self.fail("helper forwarded DMS's open stdin pipe to Codex")
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        stream.close()

            self.assertEqual(return_code, 0)
            self.assertEqual(json.loads(output)["answer"], "A short answer")


if __name__ == "__main__":
    unittest.main()
