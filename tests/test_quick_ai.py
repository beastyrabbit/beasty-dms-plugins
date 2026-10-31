from __future__ import annotations

import http.server
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
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


def fake_response(payload: dict) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def http_error(status: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://openrouter.test", status, "error", {}, io.BytesIO(body.encode())
    )


def chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def responses_payload(text: str) -> dict:
    return {
        "output": [
            {"type": "web_search_call"},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ]
    }


TRANSLATION = {
    "translation": "apple tree",
    "sourceLanguage": "de",
    "targetLanguage": "en",
    "alternatives": [],
    "grammar": "noun",
    "usage": "common",
    "ambiguityNote": "",
    "ambiguous": False,
}

PROXY = {
    "name": "CLIProxyAPI",
    "base_url": "http://proxy.test:8317/v1",
    "experimental_bearer_token": "proxy-token",
    "wire_api": "responses",
}


class FastRequestTests(unittest.TestCase):
    def test_ask_uses_deepseek_without_thinking_and_search_tool(self) -> None:
        body = quick_ai.fast_request("ask", "prompt", quick_ai.DEFAULT_MODEL, "off")
        self.assertEqual(body["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertEqual(body["provider"], {"sort": "latency"})
        self.assertEqual(body["tools"][0]["type"], "openrouter:web_search")
        self.assertNotIn("response_format", body)

    def test_models_that_require_thinking_use_low_when_off(self) -> None:
        for model in quick_ai.THINKING_REQUIRED:
            body = quick_ai.fast_request("ask", "prompt", model, "off")
            self.assertEqual(body["reasoning"], {"effort": "low"})

    def test_thinking_level_is_passed_as_effort(self) -> None:
        body = quick_ai.fast_request("ask", "prompt", "z-ai/glm-5.3-flash", "low")
        self.assertEqual(body["reasoning"], {"effort": "low"})

    def test_translation_requires_strict_schema_without_search(self) -> None:
        body = quick_ai.fast_request(
            "translate", "prompt", quick_ai.DEFAULT_MODEL, "off"
        )
        self.assertNotIn("tools", body)
        self.assertTrue(body["provider"]["require_parameters"])
        schema = body["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertNotIn("$schema", schema["schema"])
        self.assertIn("translation", schema["schema"]["required"])

    def test_summary_has_no_search_or_schema(self) -> None:
        body = quick_ai.fast_request(
            "summarize", "prompt", quick_ai.DEFAULT_MODEL, "off"
        )
        self.assertNotIn("tools", body)
        self.assertNotIn("response_format", body)


class BetterRequestTests(unittest.TestCase):
    def test_ask_uses_sol_medium_with_web_search(self) -> None:
        body = quick_ai.better_request("ask", "prompt")
        self.assertEqual(body["model"], "gpt-6.1-sol")
        self.assertEqual(body["reasoning"], {"effort": "medium"})
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        self.assertFalse(body["store"])

    def test_translation_uses_strict_schema(self) -> None:
        body = quick_ai.better_request("translate", "prompt")
        self.assertNotIn("tools", body)
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])

    def test_summary_has_no_search_or_schema(self) -> None:
        body = quick_ai.better_request("summarize", "prompt")
        self.assertNotIn("tools", body)
        self.assertNotIn("text", body)


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

    def test_better_answer_button_reruns_with_better_flag(self) -> None:
        qml = DAEMON_PATH.read_text(encoding="utf-8")
        self.assertIn('text: "Better answer"', qml)
        self.assertIn("onClicked: root.submit(true)", qml)
        self.assertIn('arguments.push("--better")', qml)


@patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test-key"})
class FastResponseTests(unittest.TestCase):
    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_ask_success(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = fake_response(
            chat_payload("A short answer")
        )
        result = quick_ai.execute("ask", "Question", "auto")
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "A short answer")
        self.assertEqual(result["model"], quick_ai.DEFAULT_MODEL)
        self.assertFalse(result["better"])
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://openrouter.ai/api/v1/chat/completions"
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer or-test-key")
        self.assertIn("Question", json.loads(request.data)["messages"][0]["content"])

    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_translation_success(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = fake_response(
            chat_payload(json.dumps(TRANSLATION))
        )
        result = quick_ai.execute("translate", "apfelbaum", "auto")
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["translation"], "apple tree")

    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_malformed_translation_is_friendly(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = fake_response(
            chat_payload("not json")
        )
        result = quick_ai.execute("translate", "Entwurf", "auto")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_response")

    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_empty_answer_is_friendly(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = fake_response(chat_payload(""))
        result = quick_ai.execute("ask", "Question", "auto")
        self.assertEqual(result["code"], "empty")

    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_timeout_is_friendly(self, urlopen) -> None:
        urlopen.side_effect = TimeoutError()
        result = quick_ai.execute("translate", "apfelbaum", "auto")
        self.assertEqual(result["code"], "timeout")
        self.assertIn("30 seconds", result["message"])

    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_network_error_is_friendly(self, urlopen) -> None:
        urlopen.side_effect = urllib.error.URLError("dns failure")
        result = quick_ai.execute("ask", "Question", "auto")
        self.assertEqual(result["code"], "network")

    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_http_errors_are_friendly(self, urlopen) -> None:
        cases = [
            (401, '{"error":{"message":"User not found."}}', "auth"),
            (402, "", "credits"),
            (429, "", "rate_limit"),
            (
                400,
                '{"error":{"message":"Reasoning is mandatory for this endpoint"}}',
                "effort",
            ),
            (404, "", "model"),
            (500, "", "http"),
        ]
        for status, body, code in cases:
            with self.subTest(status=status):
                urlopen.side_effect = http_error(status, body)
                result = quick_ai.execute("ask", "Question", "auto")
                self.assertEqual(result["code"], code)

    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_overall_deadline_stops_a_trickling_response(self, urlopen) -> None:
        urlopen.side_effect = lambda *_args, **_kwargs: time.sleep(3)
        with patch.object(quick_ai, "FAST_TIMEOUT_SECONDS", 1):
            result = quick_ai.execute("ask", "Question", "auto")
        self.assertEqual(result["code"], "timeout")

    def test_missing_key_is_friendly(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = quick_ai.execute("ask", "Question", "auto")
        self.assertEqual(result["code"], "auth")
        self.assertIn("OPENROUTER_API_KEY", result["message"])


class BetterResponseTests(unittest.TestCase):
    @patch.object(quick_ai, "proxy_provider", return_value=PROXY)
    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_better_answer_uses_proxy_responses(self, urlopen, _provider) -> None:
        urlopen.return_value.__enter__.return_value = fake_response(
            responses_payload("A better answer")
        )
        result = quick_ai.execute("ask", "Question", "auto", better=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "A better answer")
        self.assertEqual(result["model"], "gpt-6.1-sol")
        self.assertTrue(result["better"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://proxy.test:8317/v1/responses")
        self.assertEqual(request.get_header("Authorization"), "Bearer proxy-token")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 90)

    @patch.object(quick_ai, "proxy_provider", return_value=PROXY)
    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_better_translation_parses_schema_output(self, urlopen, _provider) -> None:
        urlopen.return_value.__enter__.return_value = fake_response(
            responses_payload(json.dumps(TRANSLATION))
        )
        result = quick_ai.execute("translate", "apfelbaum", "auto", better=True)
        self.assertEqual(result["result"]["translation"], "apple tree")

    @patch.object(quick_ai, "proxy_provider", return_value=None)
    def test_missing_proxy_is_friendly(self, _provider) -> None:
        result = quick_ai.execute("ask", "Question", "auto", better=True)
        self.assertEqual(result["code"], "proxy")


class ProxyProviderTests(unittest.TestCase):
    def test_reads_configured_custom_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config.toml").write_text(
                'model_provider = "cpa"\n'
                "[model_providers.cpa]\n"
                'base_url = "http://proxy.test:8317/v1"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                provider = quick_ai.proxy_provider()
        self.assertEqual(provider, {"base_url": "http://proxy.test:8317/v1"})

    def test_builtin_provider_has_no_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config.toml").write_text(
                'model = "gpt-6.1-sol"\n', encoding="utf-8"
            )
            with patch.dict(os.environ, {"CODEX_HOME": directory}):
                self.assertIsNone(quick_ai.proxy_provider())


class CheckTests(unittest.TestCase):
    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test-key"})
    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_check_validates_key(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value = fake_response({"data": {}})
        self.assertEqual(quick_ai.check_installation(), {"ok": True})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/key")

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "dead-key"})
    @patch.object(quick_ai.urllib.request, "urlopen")
    def test_check_reports_rejected_key(self, urlopen) -> None:
        urlopen.side_effect = http_error(401)
        result = quick_ai.check_installation()
        self.assertFalse(result["ok"])
        self.assertIn("HTTP 401", result["message"])

    def test_check_reports_missing_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = quick_ai.check_installation()
        self.assertFalse(result["ok"])
        self.assertIn("OPENROUTER_API_KEY", result["message"])


class ModelSelectionTests(unittest.TestCase):
    def test_empty_effort_falls_back_to_default(self) -> None:
        args = quick_ai.parse_args(["--effort", "", "--mode", "ask", "--query", "x"])
        self.assertEqual(args.effort, "off")

    def test_unknown_effort_is_rejected(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            quick_ai.parse_args(["--effort", "ultra", "--mode", "ask", "--query", "x"])

    def test_empty_model_falls_back_to_default(self) -> None:
        args = quick_ai.parse_args(["--model", "", "--mode", "ask", "--query", "x"])
        self.assertEqual(args.model, quick_ai.DEFAULT_MODEL)

    def test_openrouter_model_ids_are_accepted(self) -> None:
        args = quick_ai.parse_args(
            ["--model", "z-ai/glm-5.3-flash", "--mode", "ask", "--query", "x"]
        )
        self.assertEqual(args.model, "z-ai/glm-5.3-flash")

    def test_invalid_model_is_rejected(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            quick_ai.parse_args(["--model", "-c x", "--mode", "ask", "--query", "x"])

    def test_settings_list_matches_helper_list(self) -> None:
        qml = (ROOT / "QuickAI" / "QuickAISettings.qml").read_text(encoding="utf-8")
        for model in quick_ai.FAST_MODELS:
            self.assertIn(f'"{model}"', qml)


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

    def test_stdin_query_is_read_and_validated(self) -> None:
        with patch.object(quick_ai.sys, "stdin", io.StringIO("  Saved text  ")):
            args = quick_ai.parse_args(["--mode", "summarize", "--stdin"])
        self.assertEqual(args.query, "Saved text")

    def test_stdin_and_query_are_exclusive(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            quick_ai.parse_args(["--mode", "ask", "--stdin", "--query", "x"])

    def test_clipboard_is_rejected_for_ask(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                quick_ai.parse_args(["--mode", "ask", "--clipboard"])

    def test_summary_limit_is_rejected(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                quick_ai.parse_args(["--mode", "summarize", "--query", "x" * 24001])


class ClipboardTests(unittest.TestCase):
    @patch.object(quick_ai, "execute")
    @patch.object(quick_ai, "read_clipboard_text", return_value=("Clip", None))
    def test_clipboard_result_returns_the_text_for_better_answer(
        self, _read, execute
    ) -> None:
        execute.return_value = {"ok": True, "mode": "summarize", "answer": "Sum"}
        output = io.StringIO()
        with patch.object(quick_ai.sys, "stdout", output):
            quick_ai.main(["--mode", "summarize", "--clipboard"])
        self.assertEqual(json.loads(output.getvalue())["input"], "Clip")

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


class FakeOpenRouter(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps(chat_payload("A short answer")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


class DmsProcessIntegrationTests(unittest.TestCase):
    def test_helper_answers_with_open_parent_stdin(self) -> None:
        server = http.server.HTTPServer(("127.0.0.1", 0), FakeOpenRouter)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        environment = dict(os.environ)
        environment["OPENROUTER_API_KEY"] = "or-test-key"
        environment["QUICK_AI_OPENROUTER_URL"] = (
            f"http://127.0.0.1:{server.server_port}/api/v1"
        )
        # DMS keeps the helper's stdin open; the helper must not wait on it.
        process = quick_ai.subprocess.Popen(
            [sys.executable, str(MODULE_PATH), "--mode", "ask", "--query", "Question"],
            stdin=quick_ai.subprocess.PIPE,
            stdout=quick_ai.subprocess.PIPE,
            stderr=quick_ai.subprocess.PIPE,
            text=True,
            env=environment,
        )
        try:
            return_code = process.wait(timeout=4)
        except quick_ai.subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            self.fail("helper waited on DMS's open stdin pipe")
        finally:
            output = process.stdout.read() if process.stdout else ""
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
        self.assertEqual(return_code, 0)
        self.assertEqual(json.loads(output)["answer"], "A short answer")


if __name__ == "__main__":
    unittest.main()
