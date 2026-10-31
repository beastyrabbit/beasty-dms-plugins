from __future__ import annotations

import http.client
import importlib.util
import io
import json
import os
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
# Its sibling modules, as when DMS runs the script.
sys.path.insert(0, str(MODULE_PATH.parent))
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
                        "game_name": "Art",
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

        with (
            patch.object(twitch_follows, "helix_get", fake_get),
            patch.object(twitch_follows, "cache_avatars", lambda urls: urls),
        ):
            result = twitch_follows.fetch_follows(ACCOUNT)

        self.assertTrue(result["ok"])
        self.assertEqual(result["user"], "viewer")
        self.assertEqual([c["login"] for c in result["live"]], ["big", "small"])
        self.assertEqual(result["live"][1]["title"], "t1")
        self.assertEqual(result["live"][1]["game"], "Art")
        self.assertEqual(result["live"][0]["game"], "")
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


class AvatarCacheTests(unittest.TestCase):
    URL = "https://cdn/a-70x70.png"

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)
        self.downloads = 0

    def urlopen(self, url, timeout):
        self.downloads += 1
        return io.BytesIO(f"image {self.downloads}".encode())

    def cached(self, now: float) -> str:
        with patch.object(twitch_follows.urllib.request, "urlopen", self.urlopen):
            return twitch_follows.cached_avatar(self.URL, self.dir, now)

    def test_downloads_once_and_again_after_a_week(self) -> None:
        now = time.time()
        uri = self.cached(now)
        self.assertTrue(uri.startswith("file://") and uri.endswith(".png"))
        self.assertEqual(self.cached(now + 60), uri)
        self.assertEqual(self.downloads, 1)
        self.cached(now + twitch_follows.AVATAR_MAX_AGE_SECONDS + 1)
        self.assertEqual(self.downloads, 2)
        self.assertEqual(Path(uri[len("file://") :]).read_bytes(), b"image 2")

    def test_keeps_the_old_copy_or_the_url_when_downloading_fails(self) -> None:
        failing = patch.object(
            twitch_follows.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        )
        with failing:
            self.assertEqual(
                twitch_follows.cached_avatar(self.URL, self.dir, time.time()), self.URL
            )
        uri = self.cached(time.time())
        truncated = patch.object(
            twitch_follows.urllib.request,
            "urlopen",
            side_effect=http.client.IncompleteRead(b"half"),
        )
        with failing, truncated:
            later = time.time() + twitch_follows.AVATAR_MAX_AGE_SECONDS + 1
            self.assertEqual(
                twitch_follows.cached_avatar(self.URL, self.dir, later), uri
            )

    def test_slow_downloads_fall_back_to_the_url(self) -> None:
        def slow(url, timeout):
            time.sleep(0.5)
            return io.BytesIO(b"late")

        with (
            patch.object(twitch_follows, "avatar_dir", lambda: self.dir),
            patch.object(twitch_follows, "AVATAR_DEADLINE_SECONDS", 0.05),
            patch.object(twitch_follows.urllib.request, "urlopen", slow),
        ):
            self.assertEqual(
                twitch_follows.cache_avatars({"1": self.URL}), {"1": self.URL}
            )

    def test_drops_avatars_nobody_live_has_used_for_a_week(self) -> None:
        old, recent = self.dir / "old.png", self.dir / "recent.png"
        old.write_bytes(b"x")
        recent.write_bytes(b"x")
        week_ago = time.time() - twitch_follows.AVATAR_MAX_AGE_SECONDS - 1
        os.utime(old, (week_ago, week_ago))
        with (
            patch.object(twitch_follows, "avatar_dir", lambda: self.dir),
            patch.object(twitch_follows.urllib.request, "urlopen", self.urlopen),
        ):
            avatars = twitch_follows.cache_avatars({"1": self.URL, "2": self.URL})
        self.assertTrue(avatars["1"].startswith("file://"))
        self.assertEqual(avatars["2"], avatars["1"])
        self.assertEqual(self.downloads, 1)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


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
        # Tools a test adds later (hyprctl, chatterino) count once they exist.
        which = patch.object(
            twitch_follows.shutil,
            "which",
            side_effect=lambda name: (
                str(bin_dir / name) if (bin_dir / name).exists() else None
            ),
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
        self.assertIn("--force-window=immediate --volume=30 --mute=no", mpv_args)
        self.assertIn("--force-media-title=Big - hi", mpv_args)
        self.assertIn("--cache=yes --demuxer-max-back-bytes=2300MiB", mpv_args)

    def test_muted_watch_keeps_the_volume_for_unmuting(self) -> None:
        bin_dir = self.fake_tools("printf video", "cat > /dev/null")
        twitch_follows.main(["--watch", "big", "--muted", "--no-chat-overlay"])
        self.assertIn("--volume=30 --mute=yes", (bin_dir / "mpv.args").read_text())

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

    def test_watch_runs_chat_overlay_through_uv_when_present(self) -> None:
        bin_dir = self.fake_tools("printf video", "cat > /dev/null")
        uv = bin_dir / "uv"
        uv.write_text("#!/bin/sh\n")
        uv.chmod(0o755)
        with patch.object(
            twitch_follows.subprocess, "Popen", wraps=subprocess.Popen
        ) as popen:
            twitch_follows.main(["--watch", "big", "--no-chat-overlay"])
        overlay = popen.call_args_list[0].args[0]
        self.assertEqual(overlay[:3], [str(uv), "run", "--script"])
        self.assertTrue(overlay[3].endswith("chat_overlay.py"))

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

    def fake_chat(self, bin_dir: Path, name: str = "chatterino") -> Path:
        """A fake Chatterino: hands -c to a running instance (chat.pid) or
        becomes it for 5 s; --remove quits it only if remove-quits exists.
        A fake hyprctl lists an older Chatterino window, the mpv window by its
        pid, another group's stream 0xc3, and the chat window 0xb2 while
        chat.pid exists; it records Lua and closes the chat on window.close.
        """
        chat = bin_dir / name
        chat.write_text(
            f"""#!/bin/sh
d="{bin_dir}"
echo "$@" >> "$d/chat.args"
case "$*" in
*--remove*) [ -f "$d/remove-quits" ] && kill "$(cat "$d/chat.pid")"; exit 0 ;;
esac
[ -f "$d/chat.pid" ] && kill -0 "$(cat "$d/chat.pid")" 2>/dev/null && exit 0
echo $$ > "$d/chat.pid"
trap "exit 0" TERM
sleep "${{CHAT_LIFETIME:-5}}" & wait
"""
        )
        chat.chmod(0o755)
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
clients = [
    {{"address": "0xdead", "class": "com.chatterino.chatterino", "pid": 2}},
    {{"address": "0xc3", "class": "mpv", "pid": 4}},
]
mpv_pid = os.path.join({self.runtime!r}, "mpv.pid")
if os.path.exists(mpv_pid):
    pid = int(open(mpv_pid).read())
    clients.append({{"address": "0xa1", "class": "mpv", "pid": pid}})
chat_pid = os.path.join(d, "chat.pid")
if os.path.exists(chat_pid):
    pid = int(open(chat_pid).read())
    clients.append({{"address": "0xb2", "class": "chatterino", "pid": pid}})
print(json.dumps(clients))
"""
        )
        hyprctl.chmod(0o755)
        return chat

    def groups(self) -> list[dict]:
        return json.loads(
            (Path(self.runtime) / "twitch-follows-groups.json").read_text()
        )["groups"]

    def test_grid_puts_streams_side_by_side_and_chat_below(self) -> None:
        rows = [
            [len(row) for row in twitch_follows.grid_rows(["0x1"] * n)]
            for n in range(1, 7)
        ]
        self.assertEqual(rows, [[1], [2], [2, 1], [2, 2], [3, 2], [3, 3]])
        spec = twitch_follows.weave_spec(["0xa", "0xb", "0xc"], "0xf")
        self.assertEqual(spec["lock"], "none")
        video, chat = spec["children"]
        self.assertEqual(chat, {"window": "address:0xf", "size": 25})
        self.assertEqual(video["size"], 75)
        self.assertEqual(
            video["children"],
            [
                {
                    "direction": "row",
                    "children": [
                        {"window": "address:0xa"},
                        {"window": "address:0xb"},
                    ],
                },
                {"window": "address:0xc"},
            ],
        )

    def test_first_stream_starts_a_group_with_its_own_chat_instance(self) -> None:
        bin_dir = self.fake_tools(
            "printf video", f'echo $$ > "{self.runtime}/mpv.pid"; sleep 1; cat'
        )
        chat = self.fake_chat(bin_dir)
        (bin_dir / "remove-quits").touch()
        with patch.object(
            twitch_follows, "chatterino_command", return_value=[str(chat)]
        ):
            started = time.monotonic()
            code = twitch_follows.main(["--watch", "big", "--no-chat-overlay"])
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - started, 4)
        launch, remove = (bin_dir / "chat.args").read_text().splitlines()
        self.assertRegex(
            launch, r"^--slim --instance twitchfollows-[0-9a-f]{8} -c t:big$"
        )
        self.assertEqual(remove, launch.split(" -c")[0][7:] + " --remove t:big")
        lua = (bin_dir / "repl").read_text().split("\n----\n")[0]
        self.assertIn(' }, "0xa1"\n', lua)  # focus
        self.assertIn("weave.create", lua)
        self.assertIn('{ window = "address:0xa1", size = 75 }', lua)
        self.assertIn('{ window = "address:0xb2", size = 25 }', lua)
        self.assertIn('lock = "none"', lua)
        self.assertEqual(self.groups(), [])

    def test_flatpak_chat_gets_a_group_per_stream_and_closes_with_it(self) -> None:
        bin_dir = self.fake_tools(
            "printf video", f'echo $$ > "{self.runtime}/mpv.pid"; sleep 1; cat'
        )
        flatpak = self.fake_chat(bin_dir, "flatpak")
        command = [str(flatpak), "run", "com.chatterino.chatterino"]
        with patch.object(twitch_follows, "chatterino_command", return_value=command):
            code = twitch_follows.main(["--watch", "big", "--group", "0123abcd"])
        self.assertEqual(code, 0)
        self.assertEqual(
            (bin_dir / "chat.args").read_text(),
            "run com.chatterino.chatterino -c t:big\n",
        )
        arranged, close = (bin_dir / "repl").read_text().split("\n----\n")[:2]
        self.assertIn('{ window = "address:0xb2", size = 25 }', arranged)
        self.assertIn('window = "address:0xb2"', close)

    def test_join_adds_a_tab_to_the_group_chat_and_rearranges(self) -> None:
        bin_dir = self.fake_tools(
            "printf video", f'echo $$ > "{self.runtime}/mpv.pid"; sleep 1; cat'
        )
        chat = self.fake_chat(bin_dir)
        # The other stream's helper and the running chat instance.
        other = subprocess.Popen(["sleep", "30"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        (bin_dir / "chat.pid").write_text(str(other.pid))
        twitch_follows.save_groups(
            [
                {
                    "id": "0123abcd",
                    "shared": True,
                    "chat": "0xb2",
                    "chatPid": other.pid,
                    "streams": [{"login": "other", "pid": other.pid, "window": "0xc3"}],
                }
            ]
        )
        with patch.object(
            twitch_follows, "chatterino_command", return_value=[str(chat)]
        ):
            code = twitch_follows.main(["--watch", "big", "--group", "0123abcd"])
        self.assertEqual(code, 0)
        self.assertEqual(
            (bin_dir / "chat.args").read_text().splitlines(),
            [
                "--slim --instance twitchfollows-0123abcd -c t:big",
                "--instance twitchfollows-0123abcd --remove t:big",
            ],
        )
        joined, left = (bin_dir / "repl").read_text().split("\n----\n")[:2]
        self.assertIn(
            '{ direction = "row", children = { { window = "address:0xc3" }, '
            '{ window = "address:0xa1" } }, size = 75 }',
            joined,
        )
        self.assertIn(' }, "0xa1"\n', joined)  # focus
        self.assertIn('{ window = "address:0xc3", size = 75 }', left)
        self.assertIn(' }, ""\n', left)  # no focus
        self.assertIsNone(other.poll())
        self.assertEqual(
            [s["login"] for g in self.groups() for s in g["streams"]], ["other"]
        )

    def test_closing_the_group_chat_closes_the_stream(self) -> None:
        bin_dir = self.fake_tools(
            "trap 'exit 1' TERM; sleep 10 & wait",
            f'echo $$ > "{self.runtime}/mpv.pid"; exec sleep 10',
        )
        chat = self.fake_chat(bin_dir)
        with (
            patch.object(
                twitch_follows, "chatterino_command", return_value=[str(chat)]
            ),
            patch.dict(twitch_follows.os.environ, {"CHAT_LIFETIME": "1"}),
            patch.object(twitch_follows, "notify") as notify,
        ):
            started = time.monotonic()
            code = twitch_follows.main(["--watch", "big", "--no-chat-overlay"])
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - started, 5)
        notify.assert_not_called()
        self.assertEqual(self.groups(), [])


if __name__ == "__main__":
    unittest.main()
