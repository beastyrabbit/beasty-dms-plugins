from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import time
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

    def test_native_chatterino_settings_come_first_when_it_is_installed(self) -> None:
        with patch.object(
            twitch_follows.shutil,
            "which",
            side_effect=lambda name: (
                "/usr/bin/chatterino" if name == "chatterino" else None
            ),
        ):
            paths = twitch_follows.chatterino_settings_paths()
        self.assertNotIn(".var", str(paths[0]))
        self.assertIn(".var", str(paths[1]))


class FetchTests(unittest.TestCase):
    def test_paginates_live_streams_and_adds_avatars(self) -> None:
        pages = {
            "": {
                "data": [
                    {
                        "user_id": "2",
                        "user_login": "small",
                        "user_name": "Small",
                        "title": "t1",
                        "viewer_count": 10,
                    },
                ],
                "pagination": {"cursor": "next"},
            },
            "next": {
                "data": [
                    {
                        "user_id": "1",
                        "user_login": "big",
                        "user_name": "Big",
                        "viewer_count": 5000,
                    },
                ],
                "pagination": {},
            },
        }
        calls = []

        def fake_get(account, endpoint, params):
            calls.append((endpoint, params))
            if endpoint == "users":
                return {
                    "data": [
                        {"id": i, "profile_image_url": f"https://cdn/{i}-300x300.png"}
                        for i in params["id"]
                    ]
                }
            self.assertEqual(endpoint, "streams/followed")
            self.assertEqual(params["user_id"], "42")
            return pages[params.get("after", "")]

        with patch.object(twitch_follows, "helix_get", fake_get):
            result = twitch_follows.fetch_follows(ACCOUNT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["user"], "viewer")
        self.assertEqual([c["login"] for c in result["live"]], ["big", "small"])
        self.assertEqual(result["live"][1]["title"], "t1")
        self.assertEqual(result["live"][0]["avatar"], "https://cdn/1-70x70.png")
        self.assertNotIn("offline", result)
        self.assertEqual(calls[-1], ("users", {"id": ["2", "1"]}))
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
    def setUp(self) -> None:
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(runtime.cleanup)
        self.runtime = runtime.name
        env = patch.dict(twitch_follows.os.environ, {"XDG_RUNTIME_DIR": runtime.name})
        env.start()
        self.addCleanup(env.stop)
        no_chat = patch.object(twitch_follows, "chatterino_command", return_value=None)
        no_chat.start()
        self.addCleanup(no_chat.stop)

    def test_rejects_invalid_channel_names(self) -> None:
        with patch.object(twitch_follows.subprocess, "run") as run:
            self.assertEqual(twitch_follows.main(["--watch", "a;rm -rf"]), 2)
        run.assert_not_called()

    def fake_tools(self, streamlink_body: str, mpv_body: str) -> Path:
        bin_dir = Path(self.runtime) / "bin"
        bin_dir.mkdir(exist_ok=True)
        for name, body in (("streamlink", streamlink_body), ("mpv", mpv_body)):
            script = bin_dir / name
            script.write_text(
                f'#!/bin/sh\necho "$@" > "{bin_dir}/{name}.args"\n{body}\n'
            )
            script.chmod(0o755)
        which = patch.object(
            twitch_follows.shutil, "which", side_effect=lambda name: str(bin_dir / name)
        )
        which.start()
        self.addCleanup(which.stop)
        return bin_dir

    def test_watch_pipes_streamlink_into_mpv(self) -> None:
        bin_dir = self.fake_tools("printf video", f'cat > "{self.runtime}/bin/seen"')
        with patch.object(twitch_follows, "notify") as notify:
            code = twitch_follows.main(
                [
                    "--watch",
                    "Big",
                    "--quality",
                    "720p60",
                    "--no-chat-overlay",
                    "--media-title",
                    "Big - hi",
                ]
            )
        self.assertEqual(code, 0)
        notify.assert_not_called()
        self.assertEqual((bin_dir / "seen").read_text(), "video")
        self.assertEqual(
            (bin_dir / "streamlink.args").read_text().split(),
            [
                "--stdout",
                "--twitch-low-latency",
                "https://twitch.tv/big",
                "720p60,720p,720p30,480p,480p30,360p,360p30,160p,160p30,best",
            ],
        )
        mpv_args = (bin_dir / "mpv.args").read_text()
        self.assertIn("--force-window=immediate --volume=25", mpv_args)
        self.assertIn("--force-media-title=Big - hi -", mpv_args)

    def test_watch_starts_chat_overlay_on_the_mpv_socket(self) -> None:
        bin_dir = self.fake_tools("printf video", "cat > /dev/null")
        with patch.object(
            twitch_follows.subprocess, "Popen", wraps=subprocess.Popen
        ) as popen:
            twitch_follows.main(["--watch", "big", "--volume", "250"])
        overlay = popen.call_args_list[0].args[0]
        self.assertTrue(overlay[1].endswith("chat_overlay.py"))
        self.assertEqual(overlay[2], "big")
        mpv_args = (bin_dir / "mpv.args").read_text()
        self.assertIn("--volume=100", mpv_args)
        self.assertIn(f"--input-ipc-server={overlay[3]}", mpv_args)

    def test_chat_setting_off_still_starts_relay_hidden_for_jump_to_live(self) -> None:
        self.fake_tools("printf video", "cat > /dev/null")
        with patch.object(
            twitch_follows.subprocess, "Popen", wraps=subprocess.Popen
        ) as popen:
            twitch_follows.main(["--watch", "big", "--no-chat-overlay"])
        overlay = popen.call_args_list[0].args[0]
        self.assertTrue(overlay[1].endswith("chat_overlay.py"))
        self.assertEqual(overlay[4:], ["--hidden"])

    def test_failed_stream_notifies_with_streamlink_error(self) -> None:
        self.fake_tools(
            "echo 'error: No playable streams found' >&2; exit 1",
            "sleep 0.5; cat > /dev/null",
        )
        with patch.object(twitch_follows, "notify") as notify:
            code = twitch_follows.main(["--watch", "big", "--no-chat-overlay"])
        self.assertEqual(code, 1)
        notify.assert_called_once_with(
            "Could not play big", "error: No playable streams found"
        )

    def test_failed_stream_notifies_when_mpv_exits_in_the_same_poll(self) -> None:
        self.fake_tools("echo 'error: offline' >&2; exit 1", "cat > /dev/null; exit 2")
        with patch.object(twitch_follows, "notify") as notify:
            code = twitch_follows.main(["--watch", "big", "--no-chat-overlay"])
        self.assertEqual(code, 1)
        notify.assert_called_once_with("Could not play big", "error: offline")

    def test_closing_mpv_stops_streamlink_without_notifying(self) -> None:
        self.fake_tools("trap 'exit 1' TERM; sleep 5 & wait", "exit 0")
        started = time.monotonic()
        with patch.object(twitch_follows, "notify") as notify:
            code = twitch_follows.main(["--watch", "big", "--no-chat-overlay"])
        self.assertEqual(code, 0)
        notify.assert_not_called()
        self.assertLess(time.monotonic() - started, 3)

    def test_second_watch_of_same_channel_is_ignored(self) -> None:
        self.fake_tools("printf video", "cat > /dev/null")
        with (
            patch.object(twitch_follows.subprocess, "Popen") as popen,
            twitch_follows.file_lock("twitch-follows-watch-big.lock", blocking=False),
        ):
            self.assertEqual(twitch_follows.main(["--watch", "big"]), 0)
        popen.assert_not_called()

    def test_watch_tabs_chatterino_behind_the_stream_and_closes_it_after(
        self,
    ) -> None:
        bin_dir = self.fake_tools(
            "printf video", f'echo $$ > "{self.runtime}/mpv.pid"; sleep 1; cat'
        )
        chat = bin_dir / "chatterino"
        chat.write_text(
            f'#!/bin/sh\necho "$@" > "{bin_dir}/chatterino.args"\n'
            f'echo $$ > "{bin_dir}/chat.pid"\ntrap "exit 0" TERM\nsleep 5 & wait\n'
        )
        chat.chmod(0o755)
        # A fake hyprctl: lists an older Chatterino window, the mpv window by
        # its pid, and the new Chatterino window once it started; records Lua.
        hyprctl = bin_dir / "hyprctl"
        hyprctl.write_text(
            f"""#!{sys.executable}
import json, os, signal, sys
d = {str(bin_dir)!r}
if sys.argv[1] == "repl":
    open(os.path.join(d, "repl"), "a").write(sys.argv[2] + "\\n----\\n")
    if "window.close" in sys.argv[2]:
        os.kill(int(open(os.path.join(d, "chat.pid")).read()), signal.SIGTERM)
    sys.exit(0)
clients = [{{"address": "0xdead", "class": "com.chatterino.chatterino", "pid": 2}}]
mpv_pid = os.path.join({self.runtime!r}, "mpv.pid")
if os.path.exists(mpv_pid):
    pid = int(open(mpv_pid).read())
    clients.append({{"address": "0xa1", "class": "mpv", "pid": pid}})
if os.path.exists(os.path.join(d, "chat.pid")):
    clients.append({{"address": "0xb2", "class": "chatterino", "pid": 3}})
print(json.dumps(clients))
"""
        )
        hyprctl.chmod(0o755)
        with patch.object(
            twitch_follows, "chatterino_command", return_value=[str(chat)]
        ):
            code = twitch_follows.main(["--watch", "big", "--no-chat-overlay"])
        self.assertEqual(code, 0)
        self.assertEqual(
            (bin_dir / "chatterino.args").read_text().split(), ["-c", "t:big"]
        )
        group, close = (bin_dir / "repl").read_text().split("\n----\n")[:2]
        self.assertIn('local S, C = "0xa1", "0xb2"', group)
        self.assertIn("lock_active", group)
        self.assertIn('window = "address:0xb2"', close)


if __name__ == "__main__":
    unittest.main()
