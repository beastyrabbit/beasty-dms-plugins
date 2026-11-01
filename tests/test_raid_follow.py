"""Following raids: the plugin's own login, the shared raid watcher, and a
stream taking its window over to the raided channel."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import ClassVar, Self
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
# The plugin's modules import each other by name, as when DMS runs them.
sys.path.insert(0, str(ROOT / "TwitchFollows"))

import raid_login
import raid_watch
import twitch_follows

TOKEN = {
    "client_id": "abcdefghij0123456789",
    "user_id": "42",
    "login": "viewer",
    "access_token": "access",
    "refresh_token": "refresh",
}


class TempDirs(unittest.TestCase):
    def setUp(self) -> None:
        state = tempfile.TemporaryDirectory()
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.addCleanup(runtime.cleanup)
        self.runtime = Path(runtime.name)
        env = patch.dict(
            os.environ, {"XDG_STATE_HOME": state.name, "XDG_RUNTIME_DIR": runtime.name}
        )
        env.start()
        self.addCleanup(env.stop)


class LoginTests(TempDirs):
    def test_the_token_file_is_private_from_the_start(self) -> None:
        with raid_login.token_lock():
            raid_login.write_token(TOKEN)
        mode = stat.S_IMODE(raid_login.token_path().stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(raid_login.read_token(), TOKEN)

    def test_a_fresh_token_is_used_as_is(self) -> None:
        raid_login.write_token({**TOKEN, "expires_at": time.time() + 3600})
        with patch.object(raid_login, "post_form") as post:
            self.assertEqual(raid_login.current_token()["access_token"], "access")
        post.assert_not_called()

    def test_an_expiring_token_is_refreshed_and_both_new_tokens_kept(self) -> None:
        raid_login.write_token({**TOKEN, "expires_at": time.time() + 60})
        reply = {
            "access_token": "new",
            "refresh_token": "new-refresh",
            "expires_in": 14000,
        }
        with patch.object(raid_login, "post_form", return_value=reply) as post:
            token = raid_login.current_token()
        fields = post.call_args.args[1]
        self.assertEqual(fields["grant_type"], "refresh_token")
        self.assertEqual(fields["refresh_token"], "refresh")
        self.assertNotIn("client_secret", fields)
        self.assertEqual(token["access_token"], "new")
        stored = raid_login.read_token()
        self.assertEqual(stored["refresh_token"], "new-refresh")
        self.assertGreater(stored["expires_at"], time.time() + 13000)

    def test_a_refused_refresh_logs_out(self) -> None:
        raid_login.write_token(TOKEN)
        reply = {"status": 400, "message": "Invalid refresh token"}
        with (
            patch.object(raid_login, "post_form", return_value=reply),
            self.assertRaises(raid_login.LoginError),
        ):
            raid_login.current_token()
        self.assertFalse(raid_login.has_token())

    def test_an_unreachable_twitch_keeps_the_login(self) -> None:
        raid_login.write_token(TOKEN)
        with (
            patch.object(
                raid_login, "post_form", side_effect=raid_login.TransientError("down")
            ),
            self.assertRaises(raid_login.TransientError),
        ):
            raid_login.current_token()
        self.assertTrue(raid_login.has_token())

    def test_device_login_waits_for_the_user_and_stores_the_token(self) -> None:
        replies = iter(
            [
                {
                    "device_code": "dev",
                    "user_code": "ABCD",
                    "verification_uri": "https://www.twitch.tv/activate?device-code=ABCD",
                    "interval": 5,
                    "expires_in": 1800,
                },
                {"status": 400, "message": "authorization_pending"},
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 100,
                },
            ]
        )
        shown = []
        with (
            patch.object(raid_login, "post_form", side_effect=lambda *_: next(replies)),
            patch.object(
                raid_login,
                "validate",
                return_value={"user_id": "42", "login": "viewer"},
            ),
        ):
            token = raid_login.device_login(
                TOKEN["client_id"],
                lambda *args: shown.append(args),
                sleep=lambda _: None,
            )
        self.assertEqual(
            shown, [("ABCD", "https://www.twitch.tv/activate?device-code=ABCD")]
        )
        self.assertEqual(token["user_id"], "42")
        self.assertEqual(raid_login.read_token()["access_token"], "access")

    def test_the_login_command_rejects_what_is_not_a_client_id(self) -> None:
        with (
            patch.object(twitch_follows, "notify") as notify,
            patch.object(raid_login, "device_login") as login,
        ):
            self.assertEqual(twitch_follows.main(["--raid-login", "a b;c"]), 2)
        login.assert_not_called()
        notify.assert_called_once()


class FakeSocket:
    """A WebSocket that hands out queued messages, then times out."""

    def __init__(self, messages: list) -> None:
        self.messages = list(messages)
        self.closed = False

    def recv(self, timeout=None):
        if self.closed:
            raise ConnectionError("closed")
        if self.messages:
            item = self.messages.pop(0)
            if isinstance(item, BaseException):
                raise item
            return json.dumps(item)
        time.sleep(min(timeout or 0, 0.01))
        raise TimeoutError

    def __enter__(self) -> Self:
        return self

    def close(self) -> None:
        self.closed = True


def welcome(session: str, keepalive: int = 10) -> dict:
    return {
        "metadata": {"message_type": "session_welcome"},
        "payload": {"session": {"id": session, "keepalive_timeout_seconds": keepalive}},
    }


def raid(message_id: str, source: str = "big", target: str = "small") -> dict:
    return {
        "metadata": {
            "message_type": "notification",
            "message_id": message_id,
            "message_timestamp": "2026-11-01T12:00:00.123456789Z",
        },
        "payload": {
            "subscription": {"type": "channel.raid"},
            "event": {
                "from_broadcaster_user_login": source,
                "to_broadcaster_user_login": target,
                "to_broadcaster_user_name": "Small",
            },
        },
    }


def save_groups(*streams: dict) -> None:
    twitch_follows.save_groups(
        [{"id": "aaaaaaaa", "shared": True, "chat": "", "streams": list(streams)}]
    )


class WatcherTests(TempDirs):
    def watcher(self, *sockets: FakeSocket):
        connect = MagicMock(side_effect=list(sockets))
        helix = MagicMock()
        helix.user_ids.side_effect = lambda logins: {
            login: f"id-{login}" for login in logins
        }
        helix.subscribe.side_effect = lambda broadcaster, _: {
            "data": [{"id": f"sub-{broadcaster}"}]
        }
        return raid_watch.Watcher(helix, connect=connect), helix, connect

    def test_connects_without_client_pings(self) -> None:
        watcher, _, connect = self.watcher(FakeSocket([welcome("s1")]))
        watcher.open("wss://example", keep_subscriptions=False)
        self.assertIsNone(connect.call_args.kwargs["ping_interval"])
        self.assertEqual(watcher.session_id, "s1")

    def test_subscribes_to_raids_of_streams_that_follow_them(self) -> None:
        save_groups(
            {"login": "big", "pid": os.getpid(), "window": "", "followRaids": True},
            {"login": "other", "pid": os.getpid(), "window": ""},
        )
        watcher, helix, _ = self.watcher(FakeSocket([welcome("s1")]))
        watcher.open("wss://example", keep_subscriptions=False)
        watcher.reconcile(0)
        helix.subscribe.assert_called_once_with("id-big", "s1")
        self.assertEqual(watcher.subscriptions, {"id-big": "sub-id-big"})

    def test_drops_a_channel_nobody_watches_any_more_before_adding_one(self) -> None:
        save_groups(
            {"login": "new", "pid": os.getpid(), "window": "", "followRaids": True}
        )
        watcher, helix, _ = self.watcher(FakeSocket([welcome("s1")]))
        watcher.open("wss://example", keep_subscriptions=False)
        watcher.subscriptions = {"id-old": "sub-old"}
        calls = []
        helix.unsubscribe.side_effect = lambda sub: calls.append(("delete", sub))
        helix.subscribe.side_effect = lambda broadcaster, _: (
            calls.append(("add", broadcaster)) or {"data": [{"id": "sub-new"}]}
        )
        watcher.reconcile(0)
        self.assertEqual(calls, [("delete", "sub-old"), ("add", "id-new")])
        self.assertEqual(watcher.subscriptions, {"id-new": "sub-new"})

    def test_a_refused_subscription_waits_before_trying_again(self) -> None:
        save_groups(
            {"login": "big", "pid": os.getpid(), "window": "", "followRaids": True}
        )
        watcher, helix, _ = self.watcher(FakeSocket([welcome("s1")]))
        watcher.open("wss://example", keep_subscriptions=False)
        helix.subscribe.side_effect = None
        helix.subscribe.return_value = {"status": 429, "message": "cost exceeded"}
        watcher.reconcile(0)
        watcher.reconcile(10)
        self.assertEqual(helix.subscribe.call_count, 1)
        watcher.reconcile(raid_watch.RETRY_SECONDS + 1)
        self.assertEqual(helix.subscribe.call_count, 2)

    def test_writes_each_raid_once_for_the_streams(self) -> None:
        watcher, _, _ = self.watcher()
        watcher.handle(raid("m1"))
        watcher.handle(raid("m1"))  # Twitch sent it twice
        raids = twitch_follows.read_raids()
        self.assertEqual(raids["big"]["to"], "small")
        self.assertEqual(raids["big"]["name"], "Small")
        self.assertEqual(raids["big"]["id"], "m1")
        self.assertAlmostEqual(raids["big"]["at"], 1793534400.123456, places=3)

    def test_old_raids_drop_out_of_the_file(self) -> None:
        path = twitch_follows.runtime_path(twitch_follows.RAIDS_FILE)
        path.write_text(json.dumps({"gone": {"to": "x", "id": "old", "at": 0}}))
        watcher, _, _ = self.watcher()
        watcher.handle(raid("m1"))
        self.assertEqual(set(twitch_follows.read_raids()), {"big"})

    def test_a_reconnect_keeps_the_subscriptions(self) -> None:
        reconnect = {
            "metadata": {"message_type": "session_reconnect"},
            "payload": {"session": {"reconnect_url": "wss://edge-2"}},
        }
        first = FakeSocket([welcome("s1"), reconnect])
        second = FakeSocket([welcome("s2"), raid("m1")])
        watcher, _, connect = self.watcher(first, second)
        ws = watcher.open("wss://example", keep_subscriptions=False)
        watcher.subscriptions = {"id-big": "sub-big"}
        threading.Timer(0.3, watcher.stopping.set).start()
        watcher.receive(ws)
        self.assertEqual(connect.call_args.args[0], "wss://edge-2")
        self.assertTrue(first.closed)
        self.assertEqual(watcher.subscriptions, {"id-big": "sub-big"})
        self.assertIn("big", twitch_follows.read_raids())

    def test_silence_past_the_keepalive_means_a_new_connection(self) -> None:
        watcher, _, _ = self.watcher(FakeSocket([welcome("s1")]))
        ws = watcher.open("wss://example", keep_subscriptions=False)
        watcher.keepalive = 0
        with patch.object(raid_watch, "KEEPALIVE_SLACK_SECONDS", 0.05):
            watcher.receive(ws)  # returns instead of waiting forever
        self.assertTrue(ws.closed)
        self.assertEqual(watcher.session_id, "")
        # The next connection starts without subscriptions.
        watcher.subscriptions = {"id-big": "sub-big"}
        watcher.connect.side_effect = [FakeSocket([welcome("s2")])]
        watcher.open("wss://example", keep_subscriptions=False)
        self.assertEqual(watcher.subscriptions, {})

    def test_does_not_connect_while_no_stream_follows_raids(self) -> None:
        watcher, _, connect = self.watcher()
        with (
            patch.object(raid_watch, "RECONCILE_SECONDS", 0.05),
            patch.object(raid_watch, "IDLE_EXIT_SECONDS", 0.2),
        ):
            runner = threading.Thread(target=watcher.run, daemon=True)
            runner.start()
            runner.join(3)
        self.assertFalse(runner.is_alive())
        connect.assert_not_called()

    def test_exits_once_no_stream_follows_raids_for_a_while(self) -> None:
        watcher, _, _ = self.watcher()
        watcher.reconcile(0)
        self.assertFalse(watcher.stopping.is_set())
        watcher.reconcile(raid_watch.IDLE_EXIT_SECONDS)
        self.assertTrue(watcher.stopping.is_set())


class FollowTests(TempDirs):
    def playback(self, login: str = "big") -> twitch_follows.Playback:
        playback = twitch_follows.Playback(
            "streamlink", "best", self.runtime / "mpv.sock", True, False
        )
        relay = MagicMock(source_ended=False)
        playback.channel = twitch_follows.Channel(
            login, MagicMock(), relay, MagicMock(), started=100.0
        )
        playback.joined = ({"id": "aaaaaaaa", "shared": True}, None)
        return playback

    def write_raid(self, at: float, message_id: str = "m1", to: str = "small") -> None:
        twitch_follows.runtime_path(twitch_follows.RAIDS_FILE).write_text(
            json.dumps({"big": {"to": to, "name": "Small", "id": message_id, "at": at}})
        )

    def test_takes_a_raid_once_and_only_one_newer_than_the_channel(self) -> None:
        playback = self.playback()
        self.write_raid(at=50.0)
        self.assertIsNone(playback.new_raid())
        self.write_raid(at=150.0)
        self.assertEqual(playback.new_raid()["to"], "small")
        self.assertIsNone(playback.new_raid())

    def test_a_raid_to_a_channel_already_playing_just_ends_this_one(self) -> None:
        playback = self.playback()
        with (
            twitch_follows.file_lock(
                twitch_follows.watch_lock("small"), blocking=False
            ),
            patch.object(playback, "switch_to") as switch,
        ):
            playback.follow_raid({"to": "small", "name": "Small"}, None)
        switch.assert_not_called()
        self.assertTrue(playback.raided_away)

    def test_a_failed_follow_says_so_and_frees_the_raided_channel(self) -> None:
        playback = self.playback()
        with (
            patch.object(
                playback, "switch_to", side_effect=twitch_follows.FollowError("no")
            ),
            patch.object(twitch_follows, "notify") as notify,
        ):
            playback.follow_raid({"to": "small", "name": "Small"}, None)
        notify.assert_called_once_with("Could not follow the raid to small", "no")
        lock = twitch_follows.try_lock(twitch_follows.watch_lock("small"))
        self.assertIsNotNone(lock)
        lock.close()

    def test_the_stream_keeps_its_place_and_the_chat_swaps_its_tab_at_once(
        self,
    ) -> None:
        playback = self.playback()
        save_groups({"login": "big", "pid": os.getpid(), "window": "0x1"})
        with (
            patch.object(twitch_follows, "chatterino_command", return_value=["chat"]),
            patch.object(twitch_follows.subprocess, "run") as run,
        ):
            playback.rename("big", "small")
        streams = twitch_follows.load_groups()[0]["streams"]
        self.assertEqual(
            streams, [{"login": "small", "pid": os.getpid(), "window": "0x1"}]
        )
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            [
                "chat",
                "--instance",
                "twitchfollows-aaaaaaaa",
                "--remove",
                "t:big",
                "-c",
                "t:small",
            ],
        )

    def test_the_raided_channel_keeps_the_quality_setting_and_the_audio_mode(
        self,
    ) -> None:
        import stream_quality

        available = ["audio_only", "160p", "480p", "720p60", "1080p60", "best"]

        def follow(quality: str, audio_only: bool) -> tuple[str, str]:
            selection, preferred = twitch_follows.raid_selection(quality, audio_only)
            policy = stream_quality.QualityPolicy(preferred, audio_only=audio_only)
            policy.learn(available, 0.0)
            playing = policy.current
            policy.set_audio(False)
            return selection, f"{playing}->{policy.target()}"

        # Audio only, then video back on: still capped at the setting.
        self.assertEqual(follow("480p", True), ("audio_only,worst", "audio_only->480p"))
        # The audio-only setting with video switched on starts with video.
        self.assertEqual(follow("audio_only", False), ("best", "1080p60->1080p60"))
        self.assertEqual(
            follow("audio_only", True), ("audio_only,worst", "audio_only->1080p60")
        )

    def test_flatpak_chat_does_not_follow_raids(self) -> None:
        raid_login.write_token(TOKEN)
        with (
            patch.object(twitch_follows.shutil, "which", return_value="/usr/bin/uv"),
            patch.object(
                twitch_follows,
                "chatterino_command",
                return_value=["/usr/bin/flatpak", "run", "com.chatterino.chatterino"],
            ),
        ):
            self.assertFalse(twitch_follows.raids_followable())
        with (
            patch.object(twitch_follows.shutil, "which", return_value="/usr/bin/uv"),
            patch.object(twitch_follows, "chatterino_command", return_value=None),
        ):
            self.assertTrue(twitch_follows.raids_followable())

    def test_no_login_no_following(self) -> None:
        with (
            patch.object(twitch_follows.shutil, "which", return_value="/usr/bin/uv"),
            patch.object(twitch_follows, "chatterino_command", return_value=None),
        ):
            self.assertFalse(twitch_follows.raids_followable())

    def test_starts_the_watcher_only_when_none_runs(self) -> None:
        playback = self.playback()
        with (
            patch.object(twitch_follows.shutil, "which", return_value="/usr/bin/uv"),
            patch.object(twitch_follows.subprocess, "Popen") as popen,
            twitch_follows.file_lock(twitch_follows.RAIDS_LOCK, blocking=False),
        ):
            playback.ensure_watcher()
        popen.assert_not_called()
        playback.watcher_checked = -1e9
        with (
            patch.object(twitch_follows.shutil, "which", return_value="/usr/bin/uv"),
            patch.object(twitch_follows.subprocess, "Popen") as popen,
        ):
            playback.ensure_watcher()
            playback.ensure_watcher()  # checked again only after a while
        popen.assert_called_once()
        self.assertTrue(popen.call_args.args[0][-1].endswith("raid_watch.py"))
        self.assertTrue(popen.call_args.kwargs["start_new_session"])


@unittest.skipUnless(
    shutil.which("mpv") and shutil.which("ffmpeg"), "needs mpv and ffmpeg"
)
class RealMpvTests(TempDirs):
    """Whole raids with a real (headless) mpv: the first channel plays from
    stdin, raided ones from FIFOs, and the stream ends as usual after."""

    SIZES: ClassVar = {"big": (320, 1000, 0x100), "small": (640, 50000, 0x300)}

    def setUp(self) -> None:
        super().setUp()
        for name, (width, offset, pid) in self.SIZES.items():
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc=size={width}x{width * 3 // 4}:rate=30",
                    "-f", "lavfi", "-i", "sine=f=440",
                    "-t", "4", "-c:v", "libx264", "-c:a", "aac",
                    "-output_ts_offset", str(offset), "-mpegts_start_pid", str(pid),
                    str(self.runtime / f"{name}.ts"),
                ],
                check=True,
            )  # fmt: skip
        bin_dir = self.runtime / "bin"
        bin_dir.mkdir()
        # streamlink writes the channel's file slowly, like a live stream, and
        # leaves a mark when it has ended.
        self.streamlink = bin_dir / "streamlink"
        self.streamlink.write_text(
            f"""#!{sys.executable}
import sys, time
runtime = {str(self.runtime)!r}
channel = sys.argv[3].rsplit("/", 1)[1]
open(runtime + "/streamlink.args", "a").write(channel + "\\n")
data = open(runtime + f"/{{channel}}.ts", "rb").read()
for i in range(0, len(data), 32768):
    sys.stdout.buffer.write(data[i:i + 32768]); sys.stdout.buffer.flush()
    time.sleep(0.05)
open(runtime + f"/{{channel}}.done", "a").close()
"""
        )
        self.mpv = bin_dir / "mpv"
        self.mpv.write_text(
            f'#!/bin/sh\nexec {shutil.which("mpv")} --vo=null --ao=null "$@"\n'
        )
        for script in (self.streamlink, self.mpv):
            script.chmod(0o755)

    def raid(self, source: str, target: str, number: int) -> None:
        raids = twitch_follows.read_raids()
        raids[source] = {
            "to": target,
            "name": target,
            "id": f"m{number}",
            "at": time.time(),
        }
        twitch_follows.runtime_path(twitch_follows.RAIDS_FILE).write_text(
            json.dumps(raids)
        )

    def play(self, raids: list[tuple[str, str, str]], grace: float) -> list[int]:
        """Watches big; each raid (when, source, target) is sent once mpv shows
        that width ("320") or once that channel's streamlink ended ("done:big").
        Returns the widths mpv showed, in order."""
        widths: list[int] = []
        pending = list(raids)
        finished = threading.Event()

        def watch_mpv() -> None:
            deadline = time.monotonic() + 30
            number = 0
            while not finished.is_set() and time.monotonic() < deadline:
                sockets = list(self.runtime.glob("twitch-follows-big-*.sock"))
                try:
                    connection = twitch_follows.connect_ipc(sockets[0])
                    with connection.sock:
                        reply = twitch_follows.ipc_request(
                            connection, ["get_property", "width"], timeout=1
                        )
                except (IndexError, OSError):
                    time.sleep(0.1)
                    continue
                width = reply.get("data")
                if width and (not widths or widths[-1] != width):
                    widths.append(width)
                if not pending:
                    time.sleep(0.1)
                    continue
                when, source, target = pending[0]
                if when.startswith("done:"):
                    ready = (self.runtime / f"{when[5:]}.done").exists()
                else:
                    ready = width == int(when)
                if ready:
                    number += 1
                    (self.runtime / f"{source}.done").unlink(missing_ok=True)
                    self.raid(source, target, number)
                    pending.pop(0)
                time.sleep(0.1)

        watcher = threading.Thread(target=watch_mpv, daemon=True)
        tools = {"streamlink": str(self.streamlink), "mpv": str(self.mpv)}
        with (
            patch.object(twitch_follows.shutil, "which", side_effect=tools.get),
            patch.object(twitch_follows, "chatterino_command", return_value=None),
            patch.object(twitch_follows, "raids_followable", return_value=True),
            patch.object(twitch_follows.Playback, "ensure_watcher"),
            # No chat over the network in a test.
            patch.object(
                twitch_follows.Playback,
                "start_overlay",
                lambda self, *a, **k: setattr(
                    self, "overlay", subprocess.Popen(["sleep", "30"])
                ),
            ),
            patch.object(twitch_follows, "RAID_GRACE_SECONDS", grace),
            patch.object(twitch_follows, "notify") as notify,
        ):
            watcher.start()
            started = time.monotonic()
            code = twitch_follows.main(["--watch", "big", "--follow-raids"])
        finished.set()
        watcher.join(5)
        self.assertEqual(code, 0)
        notify.assert_not_called()
        self.assertEqual(pending, [])
        self.assertLess(time.monotonic() - started, 30)
        # Nothing left behind: no FIFO, no group, every channel free again.
        self.assertEqual(
            sorted(p.name for p in self.runtime.glob("*.ts")), ["big.ts", "small.ts"]
        )
        self.assertEqual(twitch_follows.load_groups(), [])
        for login in self.SIZES:
            lock = twitch_follows.try_lock(twitch_follows.watch_lock(login))
            self.assertIsNotNone(lock)
            lock.close()
        return widths

    def streamlink_runs(self) -> list[str]:
        return (self.runtime / "streamlink.args").read_text().split()

    def test_a_raid_moves_the_window_to_the_raided_channel(self) -> None:
        widths = self.play([("320", "big", "small")], grace=1)
        self.assertEqual(widths[0], 320)
        self.assertIn(640, widths)
        self.assertEqual(self.streamlink_runs(), ["big", "small"])

    def test_a_raid_announced_after_the_stream_ended_is_still_followed(self) -> None:
        widths = self.play([("done:big", "big", "small")], grace=8)
        self.assertEqual(widths[0], 320)
        self.assertIn(640, widths)
        self.assertEqual(self.streamlink_runs(), ["big", "small"])

    def test_raids_can_lead_back_to_the_first_channel(self) -> None:
        widths = self.play([("320", "big", "small"), ("640", "small", "big")], grace=1)
        self.assertEqual(widths[:3], [320, 640, 320])
        self.assertEqual(self.streamlink_runs(), ["big", "small", "big"])


if __name__ == "__main__":
    unittest.main()
