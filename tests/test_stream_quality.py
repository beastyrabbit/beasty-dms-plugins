from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TwitchFollows" / "stream_quality.py"
# Its sibling modules (chat_overlay, chat_image), as when DMS runs the plugin.
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("stream_quality", MODULE_PATH)
assert SPEC and SPEC.loader
stream_quality = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stream_quality
SPEC.loader.exec_module(stream_quality)

LOG = (
    "[cli][info] Found matching plugin twitch for URL https://twitch.tv/big\n"
    "[cli][info] Available streams: audio_only, 160p30 (worst), 360p30, 480p30, "
    "720p60, 1080p60 (best)\n"
)
AVAILABLE = stream_quality.available_streams(LOG)
BEST = ["best"]
GRACE = stream_quality.GRACE_SECONDS


def stall(policy, start: float, seconds: float) -> None:
    policy.buffering(True, start)
    policy.buffering(False, start + seconds)


class PolicyTests(unittest.TestCase):
    def auto(self, preferred=BEST) -> stream_quality.QualityPolicy:
        policy = stream_quality.QualityPolicy(preferred, audio_only=False)
        policy.learn(AVAILABLE, 0)
        policy.set_max(False)
        return policy

    def test_reads_streamlinks_stream_list(self) -> None:
        self.assertEqual(
            AVAILABLE,
            ["audio_only", "160p30", "360p30", "480p30", "720p60", "1080p60"],
        )
        self.assertEqual(stream_quality.available_streams("starting"), [])

    def test_quality_setting_caps_the_renditions(self) -> None:
        preferred = "720p60,720p,720p30,480p,480p30,360p,360p30,160p,160p30,best"
        policy = stream_quality.QualityPolicy(preferred.split(","), audio_only=False)
        self.assertEqual(policy.target(), "")
        policy.learn(AVAILABLE, 0)
        self.assertEqual(policy.renditions, ["720p60", "480p30", "360p30", "160p30"])
        self.assertEqual(policy.current, "720p60")
        self.assertEqual(policy.target(), "720p60")

    def test_audio_only_setting_plays_audio_and_video_comes_back_at_best(self) -> None:
        policy = stream_quality.QualityPolicy(["audio_only", "worst"], audio_only=True)
        policy.learn(AVAILABLE, 0)
        self.assertEqual(policy.current, "audio_only")
        self.assertEqual(policy.target(), "audio_only")
        self.assertTrue(policy.set_audio(False))
        self.assertEqual(policy.target(), "1080p60")

    def test_highest_quality_stays_put_while_buffering(self) -> None:
        policy = stream_quality.QualityPolicy(BEST, audio_only=False)
        policy.learn(AVAILABLE, 0)
        stall(policy, 20, 30)
        policy.tick(50)
        self.assertEqual(policy.target(), "1080p60")

    def test_auto_starts_from_what_plays(self) -> None:
        policy = self.auto()
        self.assertEqual(policy.target(), "1080p60")

    def test_buffering_before_turning_auto_on_counts(self) -> None:
        policy = stream_quality.QualityPolicy(BEST, audio_only=False)
        policy.learn(AVAILABLE, 0)
        policy.buffering(True, 20)
        policy.set_max(False)
        policy.tick(23)
        self.assertEqual(policy.target(), "720p60")

    def test_auto_steps_down_after_buffering(self) -> None:
        policy = self.auto()
        stall(policy, 20, 1)
        policy.tick(21)
        self.assertEqual(policy.target(), "1080p60")
        stall(policy, 30, 2)
        policy.tick(32)
        self.assertEqual(policy.target(), "720p60")

    def test_ongoing_buffering_counts(self) -> None:
        policy = self.auto()
        policy.buffering(True, 20)
        policy.tick(24)
        self.assertEqual(policy.target(), "720p60")

    def test_buffering_that_goes_on_after_a_step_down_still_counts(self) -> None:
        policy = self.auto()
        policy.buffering(True, 20)
        policy.tick(24)
        self.assertEqual(policy.target(), "720p60")
        policy.loaded("720p60", 25)
        policy.tick(25 + GRACE + 2)
        self.assertEqual(policy.target(), "720p60")
        policy.tick(25 + GRACE + 3)
        self.assertEqual(policy.target(), "480p30")

    def test_buffering_right_after_loading_is_ignored(self) -> None:
        policy = self.auto()
        stall(policy, 1, GRACE - 2)
        policy.tick(GRACE)
        self.assertEqual(policy.target(), "1080p60")

    def test_old_buffering_falls_out_of_the_window(self) -> None:
        policy = self.auto()
        stall(policy, 20, 2)
        stall(policy, 100, 2)
        policy.tick(102)
        self.assertEqual(policy.target(), "1080p60")

    def test_auto_never_drops_below_the_lowest_rendition(self) -> None:
        policy = self.auto()
        policy.rung = len(policy.renditions) - 1
        stall(policy, 20, 10)
        policy.tick(30)
        self.assertEqual(policy.target(), "160p30")

    def test_auto_climbs_back_after_smooth_playback(self) -> None:
        policy = self.auto()
        policy.buffering(True, 20)
        policy.tick(24)
        policy.buffering(False, 26)
        policy.loaded("720p60", 27)
        policy.tick(27 + stream_quality.STABLE_SECONDS - 1)
        self.assertEqual(policy.target(), "720p60")
        policy.tick(27 + stream_quality.STABLE_SECONDS)
        self.assertEqual(policy.target(), "1080p60")

    def test_failed_climb_waits_twice_as_long(self) -> None:
        policy = self.auto()
        policy.rung = 1
        policy.loaded("720p60", 0)
        up = stream_quality.STABLE_SECONDS
        policy.tick(up)
        policy.loaded("1080p60", up + 2)
        policy.buffering(True, up + 20)
        policy.tick(up + 24)
        self.assertEqual(policy.target(), "720p60")
        self.assertEqual(policy.up_wait, 2 * up)

    def test_audio_only_overrides_both_modes(self) -> None:
        policy = self.auto()
        policy.set_audio(True)
        stall(policy, 20, 10)
        policy.tick(30)
        self.assertEqual(policy.target(), "audio_only")
        policy.set_audio(False)
        self.assertEqual(policy.target(), "1080p60")


def ts_packet(pid: int, payload: bytes = b"", start: bool = False) -> bytes:
    header = bytes([0x47, (0x40 if start else 0) | pid >> 8, pid & 0xFF, 0x10])
    return (header + payload).ljust(188, b"\xff")


def timestamp_bytes(marker: int, seconds: float) -> bytes:
    ticks = round(seconds * 90000)
    return bytes(
        [
            marker | (ticks >> 29) & 0x0E,
            (ticks >> 22) & 0xFF,
            (ticks >> 14) & 0xFE | 1,
            (ticks >> 7) & 0xFF,
            (ticks << 1) & 0xFE | 1,
        ]
    )


def pes_start(pid: int, seconds: float) -> bytes:
    header = b"\x00\x00\x01\xbd\x00\x00\x80\x80\x05"
    return ts_packet(pid, header + timestamp_bytes(0x21, seconds), start=True)


def segment(seconds: float, mark: bytes, packets: int = 3) -> bytes:
    """A Twitch-like segment: PAT, timed metadata, then marked video packets."""
    return (
        ts_packet(0, start=True)
        + pes_start(258, seconds)
        + b"".join(ts_packet(257, mark * 8) for _ in range(packets))
    )


class SegmenterTests(unittest.TestCase):
    def test_reads_the_timestamp_back(self) -> None:
        self.assertAlmostEqual(
            stream_quality.pes_timestamp(pes_start(258, 10002.033)), 10002.033, 3
        )
        self.assertIsNone(stream_quality.pes_timestamp(ts_packet(257, b"x")))

    def test_cuts_at_segment_starts_whatever_the_chunk_size(self) -> None:
        stream = segment(10, b"a") + segment(12, b"b") + segment(14, b"c")
        for size in (len(stream), 188, 100, 7):
            segmenter = stream_quality.Segmenter()
            pieces = []
            for start in range(0, len(stream), size):
                pieces += segmenter.feed(stream[start : start + size])
            self.assertEqual(b"".join(p for _, p in pieces), stream, size)
            starts = [round(t, 3) for t, _ in pieces if t is not None]
            self.assertEqual(starts, [10, 12, 14], size)

    def test_other_data_passes_through(self) -> None:
        segmenter = stream_quality.Segmenter()
        self.assertEqual(segmenter.feed(b"video"), [])
        self.assertEqual(segmenter.flush(), b"video")


class Collector:
    """Stands in for mpv's stdin."""

    def __init__(self) -> None:
        self.data = b""
        self.closed = threading.Event()

    def write(self, data: bytes) -> None:
        self.data += data

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed.set()


def wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


class RelayTests(unittest.TestCase):
    def pipe_source(self, quality: str):
        read, write = os.pipe()
        process = MagicMock()
        process.stdout = os.fdopen(read, "rb")
        self.addCleanup(process.stdout.close)
        writer = os.fdopen(write, "wb", buffering=0)
        self.addCleanup(lambda: writer.closed or writer.close())
        return stream_quality.Source(process, Path("unused.log"), quality), writer

    def test_splices_the_new_rendition_at_a_shared_segment(self) -> None:
        old, old_in = self.pipe_source("1080p60")
        new, new_in = self.pipe_source("480p30")
        output = Collector()
        relay = stream_quality.Relay(output, old)
        relay.start()
        old_in.write(segment(10, b"A") + segment(12, b"A"))
        relay.offer(new)
        # The new streamlink starts a segment behind; that one is never used.
        new_in.write(segment(12, b"B") + segment(14, b"B"))
        wait_until(lambda: len(relay.segments) == 2)
        old_in.write(segment(14, b"A") + segment(16, b"A"))
        wait_until(lambda: relay.spliced is not None)
        self.assertEqual(relay.take_spliced(), (new, old))
        self.assertIs(relay.current, new)
        new_in.write(segment(16, b"B"))
        expected = segment(10, b"A") + segment(12, b"A")
        expected += segment(14, b"B") + segment(16, b"B")
        wait_until(lambda: len(output.data) >= len(expected))
        self.assertEqual(output.data, expected)
        new_in.close()
        self.assertTrue(output.closed.wait(2))
        self.assertTrue(relay.source_ended)

    def test_waits_for_a_new_rendition_that_is_ahead(self) -> None:
        old, old_in = self.pipe_source("1080p60")
        new, new_in = self.pipe_source("720p60")
        output = Collector()
        relay = stream_quality.Relay(output, old)
        relay.start()
        relay.offer(new)
        new_in.write(segment(14, b"B"))
        wait_until(lambda: len(relay.segments) == 1)
        old_in.write(segment(12, b"A"))
        wait_until(lambda: len(output.data) == len(segment(12, b"A")))
        self.assertIsNone(relay.spliced)
        old_in.write(segment(14, b"A"))
        wait_until(lambda: relay.spliced is not None)
        wait_until(lambda: len(output.data) == 2 * len(segment(12, b"A")))
        self.assertEqual(output.data, segment(12, b"A") + segment(14, b"B"))
        old_in.close()
        new_in.close()

    def test_holds_the_current_rendition_until_a_slower_one_catches_up(self) -> None:
        old, old_in = self.pipe_source("1080p60")
        new, new_in = self.pipe_source("720p60")
        output = Collector()
        relay = stream_quality.Relay(output, old)
        relay.start()
        old_in.write(segment(10, b"A"))
        wait_until(lambda: output.data == segment(10, b"A"))
        relay.offer(new)
        new_in.write(segment(10, b"B"))
        wait_until(lambda: relay.pending_reached is not None)
        old_in.write(segment(12, b"A") + segment(14, b"A"))
        time.sleep(0.3)
        self.assertEqual(output.data, segment(10, b"A"))
        new_in.write(segment(12, b"B"))
        wait_until(lambda: relay.spliced is not None)
        wait_until(lambda: len(output.data) == 2 * len(segment(10, b"A")))
        self.assertEqual(output.data, segment(10, b"A") + segment(12, b"B"))
        old_in.close()
        new_in.close()

    def test_gives_up_holding_after_a_while(self) -> None:
        old, old_in = self.pipe_source("1080p60")
        new, new_in = self.pipe_source("720p60")
        output = Collector()
        relay = stream_quality.Relay(output, old)
        with patch.object(stream_quality, "HOLD_SECONDS", 0.3):
            relay.start()
            relay.offer(new)
            new_in.write(segment(10, b"B"))
            wait_until(lambda: relay.pending_reached is not None)
            old_in.write(segment(12, b"A"))
            time.sleep(0.1)
            self.assertEqual(output.data, b"")
            wait_until(lambda: output.data == segment(12, b"A"))
        self.assertIsNone(relay.spliced)
        old_in.close()
        new_in.close()

    def test_holds_as_long_as_it_takes_for_audio(self) -> None:
        old, old_in = self.pipe_source("1080p60")
        new, new_in = self.pipe_source("audio_only")
        output = Collector()
        relay = stream_quality.Relay(output, old)
        with patch.object(stream_quality, "HOLD_SECONDS", 0.1):
            relay.start()
            relay.offer(new)
            new_in.write(segment(10, b"B"))
            wait_until(lambda: relay.pending_reached is not None)
            old_in.write(segment(12, b"A"))
            time.sleep(0.5)
            self.assertEqual(output.data, b"")
            # The splice point alone isn't enough: mpv gets a few seconds at once.
            new_in.write(segment(12, b"B") + segment(14, b"B"))
            time.sleep(0.3)
            self.assertIsNone(relay.spliced)
            new_in.write(segment(16, b"B"))
            wait_until(lambda: relay.spliced is not None)
        expected = segment(12, b"B") + segment(14, b"B") + segment(16, b"B")
        wait_until(lambda: output.data == expected)
        old_in.close()
        new_in.close()

    def test_video_after_audio_waits_to_be_released(self) -> None:
        old, old_in = self.pipe_source("audio_only")
        new, new_in = self.pipe_source("1080p60")
        output = Collector()
        relay = stream_quality.Relay(output, old)
        relay.start()
        relay.offer(new)
        new_in.write(segment(12, b"B") + segment(14, b"B"))
        wait_until(lambda: len(relay.segments) == 2)
        old_in.write(segment(10, b"A") + segment(12, b"A"))
        wait_until(lambda: relay.awaiting_release)
        self.assertEqual(output.data, segment(10, b"A"))
        relay.release()
        wait_until(lambda: relay.spliced is not None)
        expected = segment(10, b"A") + segment(12, b"B") + segment(14, b"B")
        wait_until(lambda: output.data == expected)
        self.assertFalse(relay.awaiting_release)
        old_in.close()
        new_in.close()

    def test_a_spliced_source_can_no_longer_be_withdrawn(self) -> None:
        old, _ = self.pipe_source("1080p60")
        new, _ = self.pipe_source("480p30")
        relay = stream_quality.Relay(Collector(), old)
        relay.offer(new)
        relay.queue = [(12, segment(12, b"A"))]
        relay.hold([(12, segment(12, b"B"))])
        relay.advance(0)
        self.assertFalse(relay.withdraw(new))
        self.assertIs(relay.current, new)

    def test_mpv_closing_ends_the_relay_without_the_stream_ending(self) -> None:
        old, old_in = self.pipe_source("1080p60")
        output = Collector()
        output.write = MagicMock(side_effect=BrokenPipeError)
        relay = stream_quality.Relay(output, old)
        relay.start()
        old_in.write(segment(10, b"A"))
        self.assertTrue(relay.finished.wait(2))
        self.assertFalse(relay.source_ended)


class SwitcherTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(runtime.cleanup)
        self.dir = Path(runtime.name)
        first_log = self.dir / "twitch-follows-big.log"
        first_log.write_text(LOG)
        self.first = self.source("best", first_log)
        self.relay = MagicMock()
        self.relay.current = self.first
        self.relay.take_spliced.return_value = None
        self.relay.awaiting_release = False
        self.relay.released = False
        policy = stream_quality.QualityPolicy(BEST, audio_only=False)
        self.switcher = stream_quality.Switcher(
            "streamlink", "big", self.dir / "mpv.sock", policy, self.relay
        )
        self.mpv = MagicMock()
        self.mpv.events.return_value = []
        self.switcher.mpv = self.mpv
        start = patch.object(stream_quality.Source, "start", side_effect=self.start)
        start.start()
        self.addCleanup(start.stop)
        self.started: list = []
        self.switcher.step(0)

    def source(self, quality: str, log_path: Path):
        process = MagicMock()
        process.poll.return_value = None
        return stream_quality.Source(process, log_path, quality, started=0)

    def start(self, streamlink, login, quality, log_path):
        source = self.source(quality, log_path)
        self.started.append(source)
        return source

    def sent(self) -> list:
        return [call.args[0] for call in self.mpv.send.call_args_list]

    def set_property(self, name: str, value) -> None:
        self.switcher.handle(
            {"event": "property-change", "name": name, "data": value}, 1
        )

    def splice(self, now: float) -> stream_quality.Source:
        new = self.started[-1]
        old = self.relay.current
        self.relay.take_spliced.return_value = (new, old)
        self.relay.current = new
        self.switcher.update(now)
        self.relay.take_spliced.return_value = None
        return old

    def test_learns_what_plays_from_the_log(self) -> None:
        self.assertEqual(self.first.quality, "1080p60")
        self.assertEqual(self.started, [])

    def test_audio_only_and_back(self) -> None:
        self.set_property(stream_quality.AUDIO_PROPERTY, "yes")
        self.switcher.update(1)
        self.assertEqual([s.quality for s in self.started], ["audio_only"])
        self.relay.offer.assert_called_once_with(self.started[0])
        self.switcher.update(2)
        self.assertNotIn(["set_property", "vid", "no"], self.sent())
        old = self.splice(3)
        old.process.kill.assert_called_once()
        self.assertEqual(self.switcher.policy.current, "audio_only")
        frame = str(self.switcher.frame_path)
        self.assertIn(["screenshot-to-file", frame, "video"], self.sent())
        self.assertIn(["set_property", "vid", "no"], self.sent())
        self.set_property(stream_quality.AUDIO_PROPERTY, "no")
        self.relay.awaiting_release = False
        self.switcher.update(4)
        self.assertEqual(self.started[-1].quality, "1080p60")
        self.switcher.update(5)
        self.assertNotIn(["get_property", "demuxer-cache-duration"], self.sent())
        # Once the relay holds the video back, mpv plays out its audio first.
        self.relay.awaiting_release = True
        self.switcher.update(6)
        self.assertEqual(self.sent()[-1], ["get_property", "demuxer-cache-duration"])
        reply = {"request_id": stream_quality.BUFFERED_REQUEST_ID, "error": "success"}
        self.switcher.handle({**reply, "data": 3.0}, 7)
        self.relay.release.assert_not_called()
        self.switcher.handle({**reply, "data": 0.5}, 8)
        self.assertIn(["set_property", "vid", "auto"], self.sent())
        self.relay.release.assert_called_once()
        self.splice(9)
        self.assertEqual(self.sent()[-1], ["show-text", "1080p60", 2000])

    def test_still_fits_the_window(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        self.switcher.policy.set_audio(True)
        self.switcher.update(1)
        self.splice(2)
        Image.new("RGB", (160, 90), "red").save(self.switcher.frame_path)
        self.set_property(stream_quality.OSD_PROPERTY, {"w": 320, "h": 360})
        self.switcher.handle(
            {"request_id": stream_quality.SCREENSHOT_REQUEST_ID, "error": "success"},
            3,
        )
        overlay = self.sent()[-1]
        self.assertEqual((overlay["name"], overlay["id"]), ("overlay-add", 0))
        self.assertEqual((overlay["x"], overlay["y"]), (0, 90))
        self.assertEqual((overlay["w"], overlay["h"]), (320, 180))
        self.switcher.hide_frame()
        self.assertEqual(self.sent()[-1], ["overlay-remove", 0])

    def test_video_switches_say_what_plays(self) -> None:
        self.switcher.policy.set_max(False)
        self.switcher.policy.rung = 1
        self.switcher.update(1)
        self.assertEqual(self.started[-1].quality, "720p60")
        self.splice(2)
        self.assertIn(["show-text", "720p60", 2000], self.sent())

    def test_failed_switch_keeps_playing_and_waits_before_retrying(self) -> None:
        self.switcher.policy.set_audio(True)
        self.switcher.update(1)
        self.started[0].process.poll.return_value = 1
        self.switcher.update(2)
        self.relay.withdraw.assert_called_once()
        self.assertIsNone(self.switcher.pending)
        self.assertEqual(self.switcher.source.quality, "1080p60")
        self.assertIn(["show-text", "Couldn't switch to Audio only", 2000], self.sent())
        self.switcher.update(3)
        self.assertEqual(len(self.started), 1)
        self.switcher.update(2 + stream_quality.RETRY_SECONDS)
        self.assertEqual(len(self.started), 2)

    def test_video_after_audio_waits_past_the_timeout_for_mpv_to_catch_up(
        self,
    ) -> None:
        self.switcher.policy.set_audio(True)
        self.switcher.update(1)
        self.splice(2)
        self.switcher.policy.set_audio(False)
        self.switcher.update(3)
        self.relay.awaiting_release = True
        self.switcher.update(3 + 2 * stream_quality.SWITCH_TIMEOUT_SECONDS)
        self.relay.withdraw.assert_not_called()
        self.assertIs(self.switcher.pending, self.started[-1])
        # Released, it no longer waits, but it isn't spliced in yet either.
        self.relay.awaiting_release, self.relay.released = False, True
        self.switcher.update(4 + 2 * stream_quality.SWITCH_TIMEOUT_SECONDS)
        self.relay.withdraw.assert_not_called()
        self.assertIs(self.switcher.pending, self.started[-1])

    def test_a_cancel_that_loses_to_a_splice_leaves_the_new_source_playing(
        self,
    ) -> None:
        self.switcher.policy.set_audio(True)
        self.switcher.update(1)
        self.relay.withdraw.return_value = False
        self.switcher.policy.set_audio(False)
        self.switcher.update(2)
        self.started[0].process.kill.assert_not_called()
        old = self.splice(3)
        old.process.kill.assert_called_once()

    def test_closing_stops_the_source_a_last_splice_replaced(self) -> None:
        self.switcher.policy.set_audio(True)
        self.switcher.update(1)
        self.relay.withdraw.return_value = False
        self.relay.take_spliced.return_value = (self.started[0], self.first)
        self.switcher.close()
        self.first.process.kill.assert_called_once()
        self.started[0].process.kill.assert_not_called()

    def test_changing_mind_mid_switch_cancels_it(self) -> None:
        self.switcher.policy.set_audio(True)
        self.switcher.update(1)
        self.switcher.policy.set_audio(False)
        self.switcher.update(2)
        self.assertIsNone(self.switcher.pending)
        self.relay.withdraw.assert_called_once()
        self.started[0].process.kill.assert_called_once()

    def test_mode_toggles_show_what_they_did(self) -> None:
        self.set_property(stream_quality.MAX_PROPERTY, "yes")
        self.assertEqual(self.sent(), [])
        self.set_property(stream_quality.MAX_PROPERTY, "no")
        self.assertEqual(self.sent(), [["show-text", "Auto quality", 2000]])


if __name__ == "__main__":
    unittest.main()
