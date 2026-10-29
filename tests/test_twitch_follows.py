from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TwitchFollows" / "twitch_follows.py"
SPEC = importlib.util.spec_from_file_location("twitch_follows", MODULE_PATH)
assert SPEC and SPEC.loader
twitch_follows = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = twitch_follows
SPEC.loader.exec_module(twitch_follows)

ACCOUNT = {
    "username": "viewer",
    "userID": "42",
    "clientID": "client",
    "oauthToken": "token",
}


def write_settings(directory: str, accounts: dict) -> Path:
    path = Path(directory) / "settings.json"
    path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    return path


class AccountTests(unittest.TestCase):
    def test_prefers_current_account(self) -> None:
        other = {**ACCOUNT, "username": "other", "userID": "7"}
        with tempfile.TemporaryDirectory() as directory:
            path = write_settings(
                directory,
                {"uid7": other, "uid42": ACCOUNT, "current": "viewer"},
            )
            self.assertEqual(twitch_follows.load_account([path])["userID"], "42")

    def test_skips_missing_files_and_incomplete_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_settings(directory, {"uid1": {"username": "x"}})
            with self.assertRaises(twitch_follows.TwitchError):
                twitch_follows.load_account([Path(directory) / "missing.json", path])


class FetchTests(unittest.TestCase):
    def test_paginates_and_splits_live_and_offline(self) -> None:
        pages = {
            ("streams/followed", ""): {
                "data": [
                    {
                        "user_login": "small",
                        "user_name": "Small",
                        "title": "t1",
                        "game_name": "Chess",
                        "viewer_count": 10,
                        "started_at": "2026-10-29T10:00:00Z",
                    },
                    {"user_login": "big", "user_name": "Big", "viewer_count": 5000},
                ],
                "pagination": {},
            },
            ("channels/followed", ""): {
                "data": [
                    {"broadcaster_login": "zed", "broadcaster_name": "Zed"},
                    {"broadcaster_login": "big", "broadcaster_name": "Big"},
                ],
                "pagination": {"cursor": "next"},
            },
            ("channels/followed", "next"): {
                "data": [
                    {"broadcaster_login": "alpha", "broadcaster_name": "alpha"},
                    {"broadcaster_login": "small", "broadcaster_name": "Small"},
                ],
                "pagination": {},
            },
        }
        calls = []

        def fake_get(account, endpoint, params):
            calls.append((endpoint, params))
            self.assertEqual(params["user_id"], "42")
            return pages[(endpoint, params.get("after", ""))]

        with patch.object(twitch_follows, "helix_get", fake_get):
            result = twitch_follows.fetch_follows(ACCOUNT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["user"], "viewer")
        self.assertEqual([c["login"] for c in result["live"]], ["big", "small"])
        self.assertEqual(result["live"][1]["game"], "Chess")
        self.assertEqual([c["login"] for c in result["offline"]], ["alpha", "zed"])
        self.assertEqual(len(calls), 3)

    def test_expired_login_has_clear_message(self) -> None:
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        with (
            patch.object(twitch_follows.urllib.request, "urlopen", side_effect=error),
            self.assertRaisesRegex(twitch_follows.TwitchError, "expired"),
        ):
            twitch_follows.helix_get(ACCOUNT, "streams/followed", {})

    def test_list_prints_error_json_without_token(self) -> None:
        output = io.StringIO()
        with (
            patch.object(twitch_follows, "load_account", return_value=ACCOUNT),
            patch.object(
                twitch_follows.urllib.request,
                "urlopen",
                side_effect=urllib.error.URLError("offline"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(twitch_follows.main(["--list"]), 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["ok"])
        self.assertNotIn("token", output.getvalue())


class LaunchTests(unittest.TestCase):
    def test_rejects_invalid_channel_names(self) -> None:
        with patch.object(twitch_follows.subprocess, "run") as run:
            self.assertEqual(twitch_follows.main(["--watch", "a;rm -rf"]), 2)
        run.assert_not_called()

    def test_watch_runs_streamlink_with_quality_fallback(self) -> None:
        completed = twitch_follows.subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(twitch_follows.shutil, "which", return_value="/usr/bin/x"),
            patch.object(
                twitch_follows.subprocess, "run", return_value=completed
            ) as run,
        ):
            self.assertEqual(
                twitch_follows.main(["--watch", "Big", "--quality", "720p60"]), 0
            )
        command = run.call_args.args[0]
        self.assertIn("https://twitch.tv/big", command)
        self.assertEqual(command[-1], "720p60,best")

    def test_chat_activates_channel_in_chatterino(self) -> None:
        with (
            patch.object(
                twitch_follows, "chatterino_command", return_value=["chatterino"]
            ),
            patch.object(twitch_follows.subprocess, "Popen") as popen,
        ):
            self.assertEqual(twitch_follows.main(["--chat", "big"]), 0)
        self.assertEqual(popen.call_args.args[0], ["chatterino", "--activate", "t:big"])


if __name__ == "__main__":
    unittest.main()
