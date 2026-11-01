"""Choose and switch the quality of a playing Twitch stream.

Two uosc buttons drive it through mpv user-data properties: "highest quality"
(on by default) keeps the best rendition even while it buffers; switched off,
quality drops a step when the stream buffers and climbs back once it has played
smoothly for a while. "Audio only" plays Twitch's audio rendition instead and
keeps a still of the last picture on screen.

mpv reads one continuous transport stream from the relay here. streamlink
can't change renditions within a stream, so a switch starts a second streamlink
for the new rendition in the background and splices it in at a segment
boundary. Twitch's renditions share their timestamps and 2-second segment
boundaries, so mpv plays on without reloading and keeps its rewind buffer.
"""

from __future__ import annotations

import contextlib
import os
import re
import select
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

# "yes" or "no"; the uosc buttons set them through twitch_buttons.lua.
MAX_PROPERTY = "user-data/twitch-max-quality"
AUDIO_PROPERTY = "user-data/twitch-audio-only"
BUFFERING_PROPERTY = "paused-for-cache"
AUDIO_ONLY = "audio_only"
# streamlink's names that point at another stream.
ALIASES = {"best", "worst"}
# Buffering right after a switch counts as settling in, not as a slow link.
GRACE_SECONDS = 10.0
# This much buffering within the window drops a step...
STALL_LIMIT_SECONDS = 3.0
STALL_WINDOW_SECONDS = 60.0
# ...and this long without any climbs one back up.
STABLE_SECONDS = 120.0
# A step up that buffers within this long is undone, and the next try waits
# twice as long (up to the maximum).
PROBE_SECONDS = 60.0
MAX_STABLE_SECONDS = 960.0
SWITCH_TIMEOUT_SECONDS = 30.0
RETRY_SECONDS = 30.0
AVAILABLE = re.compile(r"Available streams: (.+)")
PACKET_SIZE = 188
SYNC_BYTE = 0x47
# Segments of two renditions start at the same timestamp; allow for rounding.
SPLICE_TOLERANCE_SECONDS = 0.1
# A switch gives up rather than hold more than this of the new rendition.
MAX_PENDING_BYTES = 64 << 20
# How long the current rendition may be held back at a segment start for a
# new video rendition to bring that segment: mpv has under 2 seconds buffered
# at the live edge. And how far behind the new one may be for that to be worth
# it.
HOLD_SECONDS = 1.2
CATCH_UP_SECONDS = 10.0
# Twitch publishes the audio rendition in chunks about 5 seconds apart, a little
# behind the video. To play it without gaps, mpv needs more of it buffered than
# the live edge allows, so the switch to audio waits until the audio rendition
# has this much beyond the splice point and hands it over at once: mpv pauses
# once while it waits.
AUDIO_LEAD_SECONDS = 4.0
# mpv drops the packets of a track that is off, so video only comes back where
# mpv has nothing buffered: the switch back lets mpv play out its audio first,
# down to this much.
DRAINED_SECONDS = 0.7
# Under the chat image (overlay 1): mpv draws overlays in order of their id.
FRAME_OVERLAY_ID = 0
SCREENSHOT_REQUEST_ID = 7
BUFFERED_REQUEST_ID = 8
OSD_PROPERTY = "osd-dimensions"


def available_streams(log: str) -> list[str]:
    """streamlink's list of a channel's streams, lowest quality first."""
    match = AVAILABLE.search(log)
    if not match:
        return []
    return [re.sub(r" \(.*\)$", "", name) for name in match.group(1).split(", ")]


def label(quality: str) -> str:
    return "Audio only" if quality == AUDIO_ONLY else quality


def pes_timestamp(packet: bytes) -> float | None:
    """The PTS, in seconds, of a packet that starts a PES packet."""
    if not packet[1] & 0x40:
        return None
    control = (packet[3] >> 4) & 3
    if not control & 1:
        return None
    offset = 4 + (1 + packet[4] if control & 2 else 0)
    pes = packet[offset:]
    if len(pes) < 14 or pes[:3] != b"\x00\x00\x01" or not pes[7] & 0x80:
        return None
    b = pes[9:14]
    ticks = (
        ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1
    )
    return ticks / 90000


class Segmenter:
    """Cuts a transport stream into Twitch's segments as it arrives.

    Each segment starts with a PAT; its start time is the timestamp of the
    first PES packet after it. `feed` returns pieces of the stream in order, as
    (timestamp, bytes): the timestamp is set on a piece that starts a segment
    and None on the rest.
    """

    # A segment start whose timestamp doesn't follow this soon is passed on as is.
    MAX_HELD_PACKETS = 64

    def __init__(self) -> None:
        self.rest = b""
        self.held: bytearray | None = None

    def feed(self, data: bytes) -> list[tuple[float | None, bytes]]:
        data = self.rest + data
        pieces: list[tuple[float | None, bytes]] = []
        run = bytearray()
        position = 0
        while len(data) - position >= PACKET_SIZE:
            if data[position] != SYNC_BYTE:
                # Out of step (not expected from streamlink): pass bytes on
                # until the next sync byte.
                found = data.find(bytes([SYNC_BYTE]), position + 1)
                stop = found if found >= 0 else len(data)
                run += data[position:stop]
                position = stop
                continue
            packet = data[position : position + PACKET_SIZE]
            position += PACKET_SIZE
            pid = (packet[1] & 0x1F) << 8 | packet[2]
            if pid == 0 and packet[1] & 0x40:
                if run:
                    pieces.append((None, bytes(run)))
                    run = bytearray()
                if self.held:
                    pieces.append((None, bytes(self.held)))
                self.held = bytearray(packet)
                continue
            if self.held is None:
                run += packet
                continue
            self.held += packet
            timestamp = pes_timestamp(packet)
            if timestamp is not None:
                pieces.append((timestamp, bytes(self.held)))
                self.held = None
            elif len(self.held) >= self.MAX_HELD_PACKETS * PACKET_SIZE:
                pieces.append((None, bytes(self.held)))
                self.held = None
        self.rest = data[position:]
        if run:
            pieces.append((None, bytes(run)))
        return pieces

    def flush(self) -> bytes:
        """What is still held back once the stream has ended."""
        rest = bytes(self.held or b"") + self.rest
        self.held, self.rest = None, b""
        return rest


class QualityPolicy:
    """Decides which rendition should play. Knows nothing about processes, so
    it can be driven with made-up times."""

    def __init__(self, preferred: list[str], audio_only: bool) -> None:
        # The quality setting's renditions, in streamlink's order of preference.
        self.preferred = preferred
        self.max_quality = True
        self.audio_only = audio_only
        # What the first streamlink was asked for; the toggle may change first.
        self.started_audio_only = audio_only
        self.has_audio = False
        # Video renditions at or below the setting, highest first.
        self.renditions: list[str] = []
        self.rung = 0
        self.current = ""
        self.loaded_at = 0.0
        self.stalls: list[tuple[float, float]] = []
        self.stall_start: float | None = None
        self.up_wait = STABLE_SECONDS
        self.probe_until = 0.0

    @property
    def known(self) -> bool:
        return bool(self.current)

    def learn(self, available: list[str], now: float) -> None:
        """Takes the channel's streams and works out what the first streamlink
        picked from the setting, the same way it does."""
        video = [name for name in available if name not in ALIASES | {AUDIO_ONLY}]
        video.reverse()
        start = next((i for i, name in enumerate(video) if name in self.preferred), 0)
        self.renditions = video[start:]
        self.has_audio = AUDIO_ONLY in available
        if self.started_audio_only and self.has_audio or not self.renditions:
            self.current = AUDIO_ONLY
        elif self.started_audio_only:
            self.current = self.renditions[-1]  # streamlink fell back to worst
        else:
            self.current = self.renditions[0]
        self.loaded(self.current, now)

    def target(self) -> str:
        if not self.known:
            return ""
        if self.audio_only and self.has_audio or not self.renditions:
            return AUDIO_ONLY if self.has_audio else self.current
        if self.max_quality:
            return self.renditions[0]
        return self.renditions[self.rung]

    def set_max(self, enabled: bool) -> bool:
        if enabled == self.max_quality:
            return False
        self.max_quality = enabled
        if not enabled:
            # Auto starts from what plays now, and buffering seen just before
            # still counts, so turning it on mid-stall steps down right away.
            self.rung = (
                self.renditions.index(self.current)
                if self.current in self.renditions
                else 0
            )
        return True

    def set_audio(self, enabled: bool) -> bool:
        if enabled == self.audio_only:
            return False
        self.audio_only = enabled
        return True

    def loaded(self, quality: str, now: float) -> None:
        self.current = quality
        self.reset(now)

    def switch_failed(self) -> None:
        if self.current in self.renditions:
            self.rung = self.renditions.index(self.current)

    def reset(self, now: float) -> None:
        self.loaded_at = now
        self.stalls = []
        # mpv only reports changes, so buffering that goes on counts again
        # once the grace period is over.
        if self.stall_start is not None:
            self.stall_start = now + GRACE_SECONDS

    def buffering(self, active: bool, now: float) -> None:
        if active and self.stall_start is None:
            self.stall_start = max(now, self.loaded_at + GRACE_SECONDS)
        elif not active and self.stall_start is not None:
            if now > self.stall_start:
                self.stalls.append((self.stall_start, now))
            self.stall_start = None

    def buffered(self, now: float) -> float:
        since = now - STALL_WINDOW_SECONDS
        total = sum(
            end - max(start, since) for start, end in self.stalls if end > since
        )
        if self.stall_start is not None and now > self.stall_start:
            total += now - max(self.stall_start, since)
        return total

    def tick(self, now: float) -> None:
        """Moves auto quality a step down after buffering, or a step up after
        a long smooth stretch."""
        if self.max_quality or self.target() == AUDIO_ONLY or not self.renditions:
            return
        if self.buffered(now) >= STALL_LIMIT_SECONDS:
            if self.rung < len(self.renditions) - 1:
                if now < self.probe_until:
                    self.up_wait = min(self.up_wait * 2, MAX_STABLE_SECONDS)
                self.rung += 1
                self.reset(now)
            return
        if self.stall_start is not None or self.rung == 0:
            return
        smooth_since = max([self.loaded_at, *(end for _, end in self.stalls)])
        if now - smooth_since >= self.up_wait:
            self.rung -= 1
            self.probe_until = now + PROBE_SECONDS
            self.reset(now)


@dataclass
class Source:
    """One streamlink writing a rendition to a pipe, and its log."""

    process: subprocess.Popen
    log_path: Path
    quality: str
    started: float = field(default_factory=time.monotonic)

    @classmethod
    def start(cls, streamlink: str, login: str, quality: str, log_path: Path) -> Source:
        # The log goes to a file: a pipe nobody reads could fill up and stall
        # a long stream.
        with open(log_path, "w") as log:
            process = subprocess.Popen(
                [
                    streamlink,
                    "--stdout",
                    "--twitch-low-latency",
                    f"https://twitch.tv/{login}",
                    quality,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
            )
        return cls(process, log_path, quality)

    def log(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:
            return ""

    def stop(self) -> None:
        # Killed outright: streamlink ignores SIGTERM while it is stuck writing
        # into a pipe nobody reads any more.
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.process.stdout.close()
        self.log_path.unlink(missing_ok=True)


class Stopped(Exception):
    """Relay.stop() interrupted a write."""


class Relay:
    """Copies the current source into mpv, and splices in a pending source at
    the first segment both have.

    A new streamlink tends to run a little behind the current one, so when the
    pending source is close behind, the current one is held back at a segment
    start until the pending one brings that segment: for at most HOLD_SECONDS
    for video, which mpv's buffer covers, and as long as it takes for audio
    (mpv pauses once, see AUDIO_LEAD_SECONDS). Video after audio is also held
    until the other thread releases it (see DRAINED_SECONDS). Runs on its own
    thread. The other thread offers, releases or withdraws a pending source and
    collects finished splices.

    When the current source ends, the relay can keep mpv's input open for
    `hold_seconds` instead of closing it at once: mpv waits on the last picture
    meanwhile, so a raid announced just after the broadcast ended can still
    take the window over. `stop()` ends the relay at any time, even while mpv
    has stopped reading (writes to a pipe or FIFO don't block).
    """

    def __init__(
        self, output: BinaryIO, source: Source, hold_seconds: float = 0
    ) -> None:
        self.output = output
        # A real pipe is written without blocking, so stop() can't hang on a
        # reader that went away (as stdin does once mpv loads another file).
        try:
            self.fd: int | None = output.fileno()
            os.set_blocking(self.fd, False)
        except (AttributeError, OSError, ValueError):
            self.fd = None
        self.hold_seconds = hold_seconds
        # Only a stream that played is held: one that fails to start ends
        # (and says why) at once.
        self.delivered = False
        self.stopping = threading.Event()
        self.current = source
        self.pending: Source | None = None
        self.pending_ended = False
        # The pending source's segments read so far, as [start, bytes], and
        # the latest start it has reached.
        self.segments: list[list] = []
        self.pending_reached: float | None = None
        # Video after audio waits for this.
        self.released = False
        # The current source's pieces not yet written, while a splice waits.
        self.queue: list[tuple[float | None, bytes]] = []
        # The segment start the queue waits at, and since when.
        self.waiting: tuple[float, float] | None = None
        self.segmenters = {id(source): Segmenter()}
        # (new, old) once a splice has happened, until taken.
        self.spliced: tuple[Source, Source] | None = None
        self.lock = threading.Lock()
        self.finished = threading.Event()
        # Set when the current source ended, rather than mpv going away.
        self.source_ended = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self, timeout: float = 2) -> None:
        """Ends the relay and closes mpv's input; the sources keep running."""
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout)

    def offer(self, source: Source) -> None:
        with self.lock:
            self.pending = source
            self.pending_ended = False
            self.segments = []
            self.pending_reached = None
            self.released = False
            self.segmenters[id(source)] = Segmenter()

    def release(self) -> None:
        with self.lock:
            self.released = True

    def withdraw(self, source: Source) -> bool:
        """Drops a pending source; False if it has been spliced in already."""
        with self.lock:
            if self.pending is not source:
                return False
            self.segmenters.pop(id(source), None)
            self.pending = None
            self.segments = []
            return True

    def take_spliced(self) -> tuple[Source, Source] | None:
        with self.lock:
            spliced, self.spliced = self.spliced, None
            return spliced

    @property
    def awaiting_release(self) -> bool:
        """True while video after audio is ready and held for release."""
        with self.lock:
            return bool(
                self.pending
                and not self.released
                and self.queue
                and self.queue[0][0] is not None
                and self.has_segment(self.queue[0][0])
                and self.needs_release()
            )

    def run(self) -> None:
        try:
            while not self.stopping.is_set():
                with self.lock:
                    sources = [self.current]
                    if self.pending and not self.pending_ended:
                        sources.append(self.pending)
                pipes = {source.process.stdout: source for source in sources}
                try:
                    ready, _, _ = select.select(list(pipes), [], [], 0.1)
                except (OSError, ValueError):
                    continue  # a withdrawn source's pipe closed under us
                for pipe in ready:
                    source = pipes[pipe]
                    try:
                        data = os.read(pipe.fileno(), 1 << 16)
                    except (OSError, ValueError):
                        if source is self.current:
                            raise
                        data = b""  # a withdrawn source's pipe closed under us
                    with self.lock:
                        out = self.take(source, data)
                    self.write(out)
                    if self.source_ended:
                        if self.delivered:
                            self.stopping.wait(self.hold_seconds)
                        return
                # A held segment start may have waited long enough.
                with self.lock:
                    out = self.advance(time.monotonic())
                self.write(out)
        except (OSError, ValueError, Stopped):
            pass  # mpv has gone, or stop()
        finally:
            self.finished.set()
            try:
                self.output.close()
            except OSError:
                pass

    def write(self, out: list[bytes]) -> None:
        # Called without the lock: mpv can be slow to take it.
        if not out:
            return
        if any(out):
            self.delivered = True
        if self.fd is None:
            for piece in out:
                self.output.write(piece)
            self.output.flush()
            return
        for piece in out:
            view = memoryview(piece)
            while view:
                if self.stopping.is_set():
                    raise Stopped
                _, ready, _ = select.select([], [self.fd], [], 0.1)
                if ready:
                    with contextlib.suppress(BlockingIOError):
                        view = view[os.write(self.fd, view) :]

    def take(self, source: Source, data: bytes) -> list[bytes]:
        """Takes what a source has written and returns what mpv gets now."""
        segmenter = self.segmenters.get(id(source))
        if segmenter is None:
            return []  # withdrawn meanwhile
        if source is self.pending:
            if data:
                self.hold(segmenter.feed(data))
            else:
                self.pending_ended = True
        elif source is self.current:
            if not data:
                self.source_ended = True
                return [piece for _, piece in self.queue] + [segmenter.flush()]
            self.queue += segmenter.feed(data)
        return self.advance(time.monotonic())

    def hold(self, pieces: list[tuple[float | None, bytes]]) -> None:
        for start, piece in pieces:
            if start is not None:
                self.segments.append([start, bytearray(piece)])
                self.pending_reached = start
            elif self.segments:
                self.segments[-1][1] += piece
        while sum(len(s[1]) for s in self.segments) > MAX_PENDING_BYTES:
            self.segments.pop(0)

    def advance(self, now: float) -> list[bytes]:
        """Moves queued pieces of the current source to mpv up to the next
        segment start worth waiting at, or splices there."""
        out = []
        while self.queue:
            start, piece = self.queue[0]
            if start is not None and self.pending:
                # Segments the current source has already passed can't be used.
                self.segments = [
                    s for s in self.segments if s[0] > start - SPLICE_TOLERANCE_SECONDS
                ]
                if self.has_segment(start):
                    if not self.ready(start):
                        return out
                    # The rest of the old source's data is dropped.
                    out.extend(bytes(s[1]) for s in self.segments)
                    self.splice()
                    return out
                if self.worth_waiting(start, now):
                    return out
            out.append(piece)
            self.queue.pop(0)
        return out

    def has_segment(self, start: float) -> bool:
        return bool(self.segments) and (
            abs(self.segments[0][0] - start) < SPLICE_TOLERANCE_SECONDS
        )

    def needs_release(self) -> bool:
        return self.current.quality == AUDIO_ONLY and self.pending.quality != AUDIO_ONLY

    def ready(self, start: float) -> bool:
        """True once the pending source can take over at this segment start."""
        if self.pending.quality == AUDIO_ONLY:
            lead = start + AUDIO_LEAD_SECONDS - SPLICE_TOLERANCE_SECONDS
            return self.pending_reached >= lead
        return self.released or not self.needs_release()

    def worth_waiting(self, start: float, now: float) -> bool:
        """True while the pending source is close behind this segment start
        and it hasn't been held back for long."""
        reached = self.pending_reached
        if reached is None or reached > start:
            return False  # nothing read yet, or already ahead
        if self.pending.quality == AUDIO_ONLY:
            return True  # however long it takes; the switch has a timeout
        if start - reached > CATCH_UP_SECONDS:
            return False
        if not self.waiting or self.waiting[0] != start:
            self.waiting = (start, now)
        return now - self.waiting[1] < HOLD_SECONDS

    def splice(self) -> None:
        self.segmenters.pop(id(self.current), None)
        self.spliced = (self.pending, self.current)
        self.current, self.pending = self.pending, None
        self.segments = []
        self.queue = []
        self.waiting = None


class Switcher:
    """Keeps the mpv window playing what the policy asks for."""

    def __init__(
        self,
        streamlink: str,
        login: str,
        ipc_path: Path,
        policy: QualityPolicy,
        relay: Relay,
    ) -> None:
        self.streamlink = streamlink
        self.login = login
        self.ipc_path = ipc_path
        self.policy = policy
        self.relay = relay
        self.pending: Source | None = None
        self.retry_at = 0.0
        self.switches = 0
        self.mpv = None
        self.osd = (0, 0)
        # A still of the last picture, shown while video is off for audio.
        self.frame_path = ipc_path.with_suffix(".frame.png")
        self.frame = None

    @property
    def source(self) -> Source:
        return self.relay.current

    def connect(self) -> None:
        # Imported here: chat_overlay pulls in Pillow, which listing channels
        # doesn't need.
        from chat_overlay import Mpv

        try:
            self.mpv = Mpv(str(self.ipc_path))
        except OSError:
            return
        max_value = "yes" if self.policy.max_quality else "no"
        audio_value = "yes" if self.policy.audio_only else "no"
        self.mpv.send(["set_property", MAX_PROPERTY, max_value])
        self.mpv.send(["set_property", AUDIO_PROPERTY, audio_value])
        for number, name in enumerate(
            (BUFFERING_PROPERTY, MAX_PROPERTY, AUDIO_PROPERTY, OSD_PROPERTY), start=1
        ):
            self.mpv.send(["observe_property", number, name])

    def step(self, timeout: float) -> None:
        """Waits up to `timeout` for mpv events and acts on them."""
        if self.mpv is None:
            self.connect()
        if self.mpv is None:
            time.sleep(timeout)
            return
        try:
            events = self.mpv.events(timeout)
        except (EOFError, OSError):
            # mpv is closing; the caller notices it exit.
            self.mpv.sock.close()
            self.mpv = None
            time.sleep(timeout)
            return
        now = time.monotonic()
        for event in events:
            self.handle(event, now)
        if not self.policy.known:
            available = available_streams(self.source.log())
            if available:
                self.policy.learn(available, now)
                self.source.quality = self.policy.current
        self.update(now)

    def handle(self, event: dict, now: float) -> None:
        request = event.get("request_id")
        if request == SCREENSHOT_REQUEST_ID:
            if event.get("error") == "success":
                self.load_frame()
            return
        if request == BUFFERED_REQUEST_ID:
            buffered = event.get("data")
            if isinstance(buffered, (int, float)) and buffered <= DRAINED_SECONDS:
                self.bring_video_back()
            return
        if event.get("event") != "property-change":
            return
        name, value = event.get("name"), event.get("data")
        if name == BUFFERING_PROPERTY:
            self.policy.buffering(bool(value), now)
            return
        if name == OSD_PROPERTY and isinstance(value, dict):
            self.osd = (int(value.get("w", 0)), int(value.get("h", 0)))
            self.show_frame()
            return
        if value not in ("yes", "no"):
            return
        # The first notice of each just repeats what connect() set.
        if name == MAX_PROPERTY and self.policy.set_max(value == "yes"):
            self.show("Highest quality" if value == "yes" else "Auto quality")
        if name == AUDIO_PROPERTY and self.policy.set_audio(value == "yes"):
            self.show("Audio only" if value == "yes" else "Video")

    def update(self, now: float) -> None:
        if self.pending:
            self.update_pending(now)
            return
        self.policy.tick(now)
        target = self.policy.target()
        if target and target != self.source.quality and now >= self.retry_at:
            # Numbered, so a source still winding down can't remove its
            # successor's log.
            self.switches += 1
            log_path = self.source.log_path.with_name(
                f"twitch-follows-{self.login}-{self.switches}.log"
            )
            self.pending = Source.start(self.streamlink, self.login, target, log_path)
            self.relay.offer(self.pending)
            self.show(f"Switching to {label(target)}…", 30000)

    def update_pending(self, now: float) -> None:
        pending = self.pending
        assert pending is not None
        spliced = self.relay.take_spliced()
        if spliced:
            new, old = spliced
            old.stop()
            self.pending = None
            self.policy.loaded(new.quality, now)
            if new.quality == AUDIO_ONLY and old.quality != AUDIO_ONLY:
                self.turn_video_off()
            self.show(label(new.quality))
        elif pending.quality != self.policy.target():
            # Asked for something else meanwhile; the next step starts that.
            self.drop_pending()
        elif pending.process.poll() is not None or (
            # Video after audio may wait longer for mpv to play out its audio,
            # which takes as long as playback is behind, and once released it
            # is spliced in at once.
            now - pending.started > SWITCH_TIMEOUT_SECONDS
            and not (self.relay.awaiting_release or self.relay.released)
        ):
            if not self.drop_pending():
                return
            self.retry_at = now + RETRY_SECONDS
            self.policy.switch_failed()
            self.show(f"Couldn't switch to {label(pending.quality)}")
        elif self.relay.awaiting_release and self.mpv:
            # Asks how much audio mpv has left; see bring_video_back.
            self.mpv.send(
                ["get_property", "demuxer-cache-duration"], BUFFERED_REQUEST_ID
            )

    def drop_pending(self) -> bool:
        """Stops the pending source, unless the relay has just spliced it in;
        the next step then finds that splice."""
        if not self.relay.withdraw(self.pending):
            return False
        self.pending.stop()
        self.pending = None
        if self.relay.released and self.source.quality == AUDIO_ONLY:
            # Video was already back on for the handoff; audio needs it off.
            self.turn_video_off()
        return True

    def turn_video_off(self) -> None:
        """mpv would wait for pictures that audio segments don't have, so
        video goes off, leaving a still of the last picture."""
        if not self.mpv:
            return
        self.mpv.send(
            ["screenshot-to-file", str(self.frame_path), "video"],
            SCREENSHOT_REQUEST_ID,
        )
        self.mpv.send(["set_property", "vid", "no"])

    def bring_video_back(self) -> None:
        """Turns video on just before the relay hands it over: mpv has dropped
        the video of what it buffered meanwhile, so video can only start where
        mpv has nothing left (DRAINED_SECONDS)."""
        if not self.mpv or not self.relay.awaiting_release:
            return
        self.mpv.send(["set_property", "vid", "auto"])
        self.hide_frame()
        self.relay.release()

    def load_frame(self) -> None:
        if self.source.quality != AUDIO_ONLY:
            # Video came back before the screenshot did.
            self.frame_path.unlink(missing_ok=True)
            return
        try:
            from PIL import Image

            with Image.open(self.frame_path) as image:
                self.frame = image.convert("RGBA")
        except (ImportError, OSError):
            self.frame = None
        self.frame_path.unlink(missing_ok=True)
        self.show_frame()

    def show_frame(self) -> None:
        """Fits the still into the window, as mpv would show the video."""
        if not self.frame or not self.mpv or min(self.osd) <= 0:
            return
        import chat_image

        scale = min(self.osd[0] / self.frame.width, self.osd[1] / self.frame.height)
        size = (
            max(1, round(self.frame.width * scale)),
            max(1, round(self.frame.height * scale)),
        )
        image = self.frame.resize(size)
        path = self.frame_path.with_suffix(".bgra")
        # mpv reads the file while handling the command; a new file renamed
        # into place means it never sees a half-written one.
        temporary = path.with_name(path.name + ".new")
        temporary.write_bytes(chat_image.to_bgra(image))
        os.replace(temporary, path)
        self.mpv.send(
            {
                "name": "overlay-add",
                "id": FRAME_OVERLAY_ID,
                "x": (self.osd[0] - size[0]) // 2,
                "y": (self.osd[1] - size[1]) // 2,
                "file": str(path),
                "offset": 0,
                "fmt": "bgra",
                "w": size[0],
                "h": size[1],
                "stride": 4 * size[0],
            }
        )

    def hide_frame(self) -> None:
        if self.frame and self.mpv:
            self.mpv.send(["overlay-remove", FRAME_OVERLAY_ID])
        self.frame = None
        self.frame_path.with_suffix(".bgra").unlink(missing_ok=True)

    def show(self, text: str, duration: int = 2000) -> None:
        if self.mpv:
            self.mpv.send(["show-text", text, duration])

    def close(self) -> None:
        """Stops a switch still under way and removes the still."""
        if self.pending:
            self.drop_pending()
        # A splice the last step didn't see leaves its old source running.
        spliced = self.relay.take_spliced()
        if spliced:
            spliced[1].stop()
        if self.mpv:
            self.mpv.sock.close()
            self.mpv = None
        self.frame_path.unlink(missing_ok=True)
        self.frame_path.with_suffix(".bgra").unlink(missing_ok=True)
