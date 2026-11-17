"""Choose and switch the quality of a playing Twitch stream.

Two uosc buttons drive it through mpv user-data properties: "highest quality"
(on by default) keeps the best rendition even while it buffers; switched off,
quality drops a step when the stream buffers and climbs back once it has played
smoothly for a while. "Audio only" plays Twitch's audio rendition instead and
keeps a still of the last picture on screen.

mpv reads one continuous stream from the relay here. streamlink can't change
renditions within a stream, so a switch starts a second streamlink for the new
rendition in the background and splices it in at a segment boundary. Twitch's
renditions share their timestamps and 2-second segment boundaries, so mpv
plays on without reloading and keeps its rewind buffer.

Most channels come as a transport stream. Channels on Twitch's Enhanced
Broadcasting come as fragmented MP4 instead: one header (ftyp and moov) that
mpv reads once, then fragments (moof and mdat). A splice there sends the new
rendition's fragments from a keyframe on, without its header: the renditions
share their track IDs and timescales, and each keyframe carries its picture
settings (SPS and PPS), so mpv's decoder follows the new size.
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


Piece = tuple[float | None, bytes]


class Segmenter:
    """Cuts a stream into Twitch's segments as it arrives, in whichever format
    streamlink writes: a transport stream, or fragmented MP4.

    `feed` returns pieces of the stream in order, as (timestamp, bytes): the
    timestamp is set on a piece that starts a segment and None on the rest.
    Joined, the pieces are always exactly the bytes fed in.
    """

    def __init__(self) -> None:
        self.format: str | None = None
        self.inner: TsSegmenter | Mp4Segmenter | None = None
        self.head = b""

    def feed(self, data: bytes) -> list[Piece]:
        if self.inner is None:
            self.head += data
            if len(self.head) < 8:
                return []
            data, self.head = self.head, b""
            if data[4:8] == b"ftyp":
                self.format, self.inner = "mp4", Mp4Segmenter()
            else:
                self.format, self.inner = "ts", TsSegmenter()
        return self.inner.feed(data)

    def flush(self) -> bytes:
        """What is still held back once the stream has ended."""
        if self.inner is None:
            head, self.head = self.head, b""
            return head
        return self.inner.flush()

    def splices_into(self, output: Segmenter) -> bool | None:
        """Whether this stream's segments can follow what mpv got from
        `output`; None until both have shown enough to tell."""
        if self.format == "ts" and output.format in (None, "ts"):
            return True  # each segment brings all mpv needs
        if self.format is None or output.format is None:
            return None
        if self.format != output.format:
            return False
        tracks = self.inner.tracks
        known = output.inner.tracks
        if tracks is None or known is None:
            return None
        # mpv read the first header only: it knows only those tracks.
        return all(
            number in known and track.matches(known[number])
            for number, track in tracks.items()
        )


class TsSegmenter:
    """Cuts a transport stream into Twitch's segments.

    Each segment starts with a PAT; its start time is the timestamp of the
    first PES packet after it.
    """

    # A segment start whose timestamp doesn't follow this soon is passed on as is.
    MAX_HELD_PACKETS = 64

    def __init__(self) -> None:
        self.rest = b""
        self.held: bytearray | None = None

    def feed(self, data: bytes) -> list[Piece]:
        data = self.rest + data
        pieces: list[Piece] = []
        run = bytearray()
        position = 0
        while len(data) - position >= PACKET_SIZE:
            if data[position] != SYNC_BYTE:
                position = self.skip_to_sync(data, position, run)
                continue
            packet = data[position : position + PACKET_SIZE]
            position += PACKET_SIZE
            run = self.take_packet(packet, pieces, run)
        self.rest = data[position:]
        if run:
            pieces.append((None, bytes(run)))
        return pieces

    def skip_to_sync(self, data: bytes, position: int, run: bytearray) -> int:
        """Out of step (not expected from streamlink): passes bytes on until
        the next sync byte, after any held segment start so that the order
        stays. Returns where that sync byte is."""
        if self.held is not None:
            run += self.held
            self.held = None
        found = data.find(bytes([SYNC_BYTE]), position + 1)
        stop = found if found >= 0 else len(data)
        run += data[position:stop]
        return stop

    def take_packet(
        self, packet: bytes, pieces: list[Piece], run: bytearray
    ) -> bytearray:
        """Adds one packet to the run, the held segment start or a new one;
        returns the run to go on with."""
        pid = (packet[1] & 0x1F) << 8 | packet[2]
        if pid == 0 and packet[1] & 0x40:
            if run:
                pieces.append((None, bytes(run)))
                run = bytearray()
            if self.held:
                pieces.append((None, bytes(self.held)))
            self.held = bytearray(packet)
            return run
        if self.held is None:
            run += packet
            return run
        self.held += packet
        timestamp = pes_timestamp(packet)
        if (
            timestamp is not None
            or len(self.held) >= self.MAX_HELD_PACKETS * PACKET_SIZE
        ):
            pieces.append((timestamp, bytes(self.held)))
            self.held = None
        return run

    def flush(self) -> bytes:
        """What is still held back once the stream has ended."""
        rest = bytes(self.held or b"") + self.rest
        self.held, self.rest = None, b""
        return rest


# Video codecs whose keyframes can bring their own picture settings, and the
# NAL unit types of those settings (H.264's SPS; H.265's VPS and SPS).
CODEC_FAMILIES = {b"avc1": b"avc", b"avc3": b"avc", b"hvc1": b"hevc", b"hev1": b"hevc"}
PARAMETER_SETS = {b"avc": {7}, b"hevc": {32, 33}}
# The MP4 boxes read whole; everything else passes through as it arrives.
PARSED_BOXES = {b"moov", b"moof"}
MAX_PARSED_BYTES = 16 << 20
# How far into a keyframe a segment start may look for its picture settings.
MAX_PROBE_BYTES = 1 << 20
NON_SYNC_SAMPLE = 0x10000


def box_header(data: bytes, offset: int, end: int) -> tuple[int, bytes, int] | None:
    """(size, type, header length) of the box at `offset`; None while its
    header isn't all there."""
    if end - offset < 8:
        return None
    size = int.from_bytes(data[offset : offset + 4], "big")
    kind = bytes(data[offset + 4 : offset + 8])
    if size != 1:
        return size, kind, 8
    if end - offset < 16:
        return None
    return int.from_bytes(data[offset + 8 : offset + 16], "big"), kind, 16


def child_boxes(data: bytes, start: int, end: int):
    """(type, payload start, end) of each box within data[start:end]."""
    while header := box_header(data, start, end):
        size, kind, header_length = header
        if size < header_length or start + size > end:
            return
        yield kind, start + header_length, start + size
        start += size


def find_box(data: bytes, start: int, end: int, *path: bytes) -> tuple[int, int]:
    """The payload span of the box at `path` below data[start:end]; (0, 0) if
    it isn't there."""
    for kind in path:
        found = next((b for b in child_boxes(data, start, end) if b[0] == kind), None)
        if found is None:
            return 0, 0
        _, start, end = found
    return start, end


def number(data: bytes, offset: int, size: int = 4) -> int:
    return int.from_bytes(data[offset : offset + size], "big")


@dataclass
class Track:
    """What mpv needs to play a track's fragments, from an MP4 header."""

    handler: bytes
    timescale: int
    codec: bytes
    # Bytes in front of each NAL unit, for H.264 and H.265.
    length_size: int = 0
    # The sample defaults (trex) fragments fall back on.
    default_duration: int = 0
    default_size: int = 0
    default_flags: int = 0

    @property
    def family(self) -> bytes:
        return CODEC_FAMILIES.get(self.codec, self.codec)

    def matches(self, other: Track) -> bool:
        # mpv reads every fragment with the first header: its NAL length size
        # and its sample defaults.
        return self.key() == other.key()

    def key(self) -> tuple:
        return (
            self.handler,
            self.timescale,
            self.family,
            self.length_size,
            self.default_duration,
            self.default_size,
            self.default_flags,
        )


def parse_moov(box: bytes) -> dict[int, Track]:
    """The tracks of an MP4 header, by track ID."""
    tracks: dict[int, Track] = {}
    _, start, end = next(iter(child_boxes(box, 0, len(box))))
    for kind, s, e in child_boxes(box, start, end):
        if kind == b"trak":
            track_id, track = parse_trak(box, s, e)
            if track_id:
                tracks[track_id] = track
    for kind, s, e in child_boxes(box, *find_box(box, start, end, b"mvex")):
        if kind == b"trex" and e - s >= 24 and number(box, s + 4) in tracks:
            track = tracks[number(box, s + 4)]
            track.default_duration = number(box, s + 12)
            track.default_size = number(box, s + 16)
            track.default_flags = number(box, s + 20)
    return tracks


def header_field(box: bytes, span: tuple[int, int]) -> int:
    """The track ID of a tkhd or the timescale of an mdhd, which sit after
    the times, 4 or 8 bytes each by the box's version; 0 if it is too short."""
    start, end = span
    if end - start < 24:
        return 0
    return number(box, start + 20) if box[start] == 1 else number(box, start + 12)


def nal_length_size(box: bytes, entry: tuple[bytes, int, int], family: bytes) -> int:
    """Bytes in front of each NAL unit, from a sample entry's configuration
    box, which follows the 78 bytes of a visual sample entry."""
    config, at = (b"avcC", 4) if family == b"avc" else (b"hvcC", 21)
    s, e = find_box(box, entry[1] + 78, entry[2], config)
    return (box[s + at] & 3) + 1 if e - s > at else 0


def parse_trak(box: bytes, start: int, end: int) -> tuple[int, Track]:
    track_id = header_field(box, find_box(box, start, end, b"tkhd"))
    timescale = header_field(box, find_box(box, start, end, b"mdia", b"mdhd"))
    s, e = find_box(box, start, end, b"mdia", b"hdlr")
    handler = bytes(box[s + 8 : s + 12]) if e - s >= 12 else b""
    s, e = find_box(box, start, end, b"mdia", b"minf", b"stbl", b"stsd")
    entry = next(iter(child_boxes(box, s + 8, e)), None) if e - s >= 8 else None
    track = Track(handler, timescale, entry[0] if entry else b"")
    if entry and track.family in PARAMETER_SETS:
        track.length_size = nal_length_size(box, entry, track.family)
    return track_id, track


@dataclass
class Fragment:
    """The part of a moof about one track."""

    track_id: int
    decode_time: int
    # The first sample: whether it is a keyframe, and where its data starts,
    # counted from the start of the moof (None if it can't be told).
    sync: bool
    offset: int | None
    size: int


def parse_moof(box: bytes, tracks: dict[int, Track]) -> dict[int, Fragment]:
    fragments: dict[int, Fragment] = {}
    _, start, end = next(iter(child_boxes(box, 0, len(box))))
    for kind, s, e in child_boxes(box, start, end):
        if kind != b"traf":
            continue
        fragment = parse_traf(box, s, e, tracks, first=not fragments)
        if fragment:
            fragments[fragment.track_id] = fragment
    return fragments


def parse_traf(
    box: bytes, start: int, end: int, tracks: dict[int, Track], first: bool
) -> Fragment | None:
    s, e = find_box(box, start, end, b"tfhd")
    if e - s < 8 or number(box, s + 4) not in tracks:
        return None
    track_id = number(box, s + 4)
    flags, fields = parse_tfhd(box, s)
    s, e = find_box(box, start, end, b"tfdt")
    if e - s < 8:
        return None
    decode_time = number(box, s + 4, 8 if box[s] == 1 else 4)
    s, e = find_box(box, start, end, b"trun")
    if e - s < 8 or not number(box, s + 4):
        return None
    # The track's defaults, then the fragment's, then the run's own.
    track = tracks[track_id]
    sample = {
        "flags": fields.get("flags", track.default_flags),
        "size": fields.get("size", track.default_size),
    }
    offset = first_sample(box, s, sample)
    # Offsets count from the moof only without a base offset, and past the
    # first track only if the header says so.
    if "base" in fields or not (flags & 0x20000 or first):
        offset = None
    sync = not sample["flags"] & NON_SYNC_SAMPLE
    return Fragment(track_id, decode_time, sync, offset, sample["size"])


def parse_tfhd(box: bytes, start: int) -> tuple[int, dict[str, int]]:
    """A tfhd's flags and the optional fields they say are there."""
    flags = number(box, start) & 0xFFFFFF
    at = start + 8
    fields = {}
    for bit, name, size in (
        (0x1, "base", 8),
        (0x2, "description", 4),
        (0x8, "duration", 4),
        (0x10, "size", 4),
        (0x20, "flags", 4),
    ):
        if flags & bit:
            fields[name] = number(box, at, size)
            at += size
    return flags, fields


def first_sample(box: bytes, start: int, sample: dict[str, int]) -> int | None:
    """Reads a trun's first sample into `sample` (flags and size), and
    returns the run's data offset, if it has one."""
    run_flags = number(box, start) & 0xFFFFFF
    at = start + 8
    offset = None
    if run_flags & 0x1:
        offset = int.from_bytes(box[at : at + 4], "big", signed=True)
        at += 4
    if run_flags & 0x4:
        sample["flags"] = number(box, at)
        at += 4
    at += 4 if run_flags & 0x100 else 0
    if run_flags & 0x200:
        sample["size"] = number(box, at)
        at += 4
    if run_flags & 0x400 and not run_flags & 0x4:
        sample["flags"] = number(box, at)
    return offset


@dataclass
class HeldStart:
    """A keyframe's moof, held with the data that follows until it shows
    whether the keyframe brings its picture settings."""

    timestamp: float
    data: bytearray
    offset: int
    size: int
    length_size: int
    family: bytes

    def decide(self) -> bool | None:
        """True if a new rendition can start here, False if not; None while
        that can't be told yet."""
        position = self.offset
        end = self.offset + self.size if self.size else self.offset + MAX_PROBE_BYTES
        missing = set(PARAMETER_SETS[self.family])
        while position < end:
            if position + self.length_size >= len(self.data):
                return None if len(self.data) < self.offset + MAX_PROBE_BYTES else False
            header = self.data[position + self.length_size]
            if self.family == b"avc":
                kind, picture = header & 0x1F, 1 <= header & 0x1F <= 5
            else:
                kind, picture = header >> 1 & 0x3F, header >> 1 & 0x3F < 32
            missing.discard(kind)
            if not missing:
                return True
            if picture:
                return False
            length = number(self.data, position, self.length_size)
            position += self.length_size + length
        return False


class Mp4Segmenter:
    """Cuts fragmented MP4 into segments, at the moofs a new rendition can
    start from.

    In a stream with video that is a moof whose video starts with a keyframe
    that brings its picture settings; audio alone can start at any moof. The
    start time is the audio's when the moof has audio (identical across
    Twitch's renditions, video's differs by up to a frame), else the video's.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        # Bytes of the box being passed through that are still to come.
        self.skip = 0
        # Set once the stream stops making sense: it then passes through.
        self.broken = False
        self.tracks: dict[int, Track] | None = None
        self.held: HeldStart | None = None

    def feed(self, data: bytes) -> list[Piece]:
        pieces: list[Piece] = []
        self.buffer += data
        while self.buffer:
            if self.broken:
                self.emit(pieces, bytes(self.buffer))
                self.buffer.clear()
                break
            if self.skip:
                count = min(self.skip, len(self.buffer))
                self.emit(pieces, bytes(self.buffer[:count]))
                del self.buffer[:count]
                self.skip -= count
                continue
            header = box_header(self.buffer, 0, len(self.buffer))
            if header is None:
                break
            size, kind, header_length = header
            if size < header_length or (
                kind in PARSED_BOXES and size > MAX_PARSED_BYTES
            ):
                self.broken = True
                continue
            if kind not in PARSED_BOXES:
                self.skip = size
                continue
            if len(self.buffer) < size:
                break
            box = bytes(self.buffer[:size])
            del self.buffer[:size]
            self.parsed(kind, box, pieces)
        return merged(pieces)

    def flush(self) -> bytes:
        rest = bytes(self.held.data if self.held else b"") + bytes(self.buffer)
        self.held, self.buffer = None, bytearray()
        return rest

    def emit(self, pieces: list[Piece], data: bytes) -> None:
        if self.held is None:
            pieces.append((None, data))
            return
        self.held.data += data
        usable = self.held.decide()
        if usable is not None:
            pieces.append(
                (self.held.timestamp if usable else None, bytes(self.held.data))
            )
            self.held = None

    def parsed(self, kind: bytes, box: bytes, pieces: list[Piece]) -> None:
        if self.held:
            # The keyframe's data never came: not a start after all.
            pieces.append((None, bytes(self.held.data)))
            self.held = None
        start = None
        try:
            if kind == b"moov":
                self.tracks = parse_moov(box)
            elif self.tracks:
                start = self.segment_start(box)
        except (IndexError, StopIteration, ValueError):
            start = None
        if start is None:
            pieces.append((None, box))
        elif isinstance(start, HeldStart):
            self.held = start
            self.emit(pieces, b"")
        else:
            pieces.append((start, box))

    def segment_start(self, box: bytes) -> float | HeldStart | None:
        tracks = self.tracks
        video = next((n for n, t in tracks.items() if t.handler == b"vide"), None)
        audio = next((n for n, t in tracks.items() if t.handler == b"soun"), None)
        fragments = parse_moof(box, tracks)
        timing = fragments.get(audio) or fragments.get(video)
        if timing is None or not tracks[timing.track_id].timescale:
            return None
        timestamp = timing.decode_time / tracks[timing.track_id].timescale
        if video is None:
            return timestamp
        fragment = fragments.get(video)
        track = tracks[video]
        if (
            fragment is None
            or not fragment.sync
            or fragment.offset is None
            or track.family not in PARAMETER_SETS
            or not track.length_size
        ):
            return None
        return HeldStart(
            timestamp,
            bytearray(box),
            fragment.offset,
            fragment.size,
            track.length_size,
            track.family,
        )


def merged(pieces: list[Piece]) -> list[Piece]:
    """Joins the pieces that don't start a segment."""
    out: list[Piece] = []
    run: list[bytes] = []
    for timestamp, piece in pieces:
        if timestamp is None:
            run.append(piece)
            continue
        if run:
            out.append((None, b"".join(run)))
            run = []
        out.append((timestamp, piece))
    if run:
        out.append((None, b"".join(run)))
    return [piece for piece in out if piece[1] or piece[0] is not None]


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
        # Set when the pending source can't follow what mpv plays, such as
        # video for an MP4 stream that started as audio only.
        self.pending_unusable = False
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
        # What the first source sent mpv stays what mpv goes by: an MP4
        # header comes only once.
        self.header_source = self.segmenters[id(source)]
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
            self.pending_unusable = False
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
                if self.relay_ready(pipes, ready):
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

    def relay_ready(self, pipes: dict[BinaryIO, Source], ready: list[BinaryIO]) -> bool:
        """Passes on what the ready sources wrote; True once the stream that
        plays has ended."""
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
                return True
        return False

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
            if not data:
                self.pending_ended = True
            elif not self.pending_unusable:
                pieces = segmenter.feed(data)
                usable = segmenter.splices_into(self.header_source)
                if usable:
                    self.hold(pieces)
                elif usable is False:
                    self.pending_unusable = True
                    self.segments = []
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
                self.drop_passed_segments(start)
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

    def drop_passed_segments(self, start: float) -> None:
        """Drops the pending segments the current source has already passed,
        which can't be used. MP4 audio starts at every fragment, which the
        renditions don't all cut alike: of those near this start, the nearest
        stays first."""
        self.segments = [
            s for s in self.segments if s[0] > start - SPLICE_TOLERANCE_SECONDS
        ]
        while len(self.segments) > 1 and abs(self.segments[1][0] - start) < abs(
            self.segments[0][0] - start
        ):
            self.segments.pop(0)

    def has_segment(self, start: float) -> bool:
        if not self.segments:
            return False
        target = self.segments[0][0]
        if abs(target - start) >= SPLICE_TOLERANCE_SECONDS:
            return False
        # A later start of the current source may lie nearer still.
        later = next((t for t, _ in self.queue if t is not None and t > start), None)
        return later is None or abs(later - target) >= abs(start - target)

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
        # Renditions that can't follow what mpv plays; not tried again.
        self.impossible: set[str] = set()
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
        if (
            target
            and target != self.source.quality
            and target not in self.impossible
            and now >= self.retry_at
        ):
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
            self.finish_switch(*spliced, now)
        elif pending.quality != self.policy.target():
            # Asked for something else meanwhile; the next step starts that.
            self.drop_pending()
        elif self.relay.pending_unusable:
            self.refuse(pending)
        elif self.switch_failed(pending, now):
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

    def finish_switch(self, new: Source, old: Source, now: float) -> None:
        old.stop()
        self.pending = None
        self.policy.loaded(new.quality, now)
        if new.quality == AUDIO_ONLY and old.quality != AUDIO_ONLY:
            self.turn_video_off()
        self.show(label(new.quality))

    def refuse(self, pending: Source) -> None:
        """Drops a rendition that can't follow what mpv plays, for good."""
        if not self.drop_pending():
            return
        self.impossible.add(pending.quality)
        self.policy.switch_failed()
        self.show(f"This stream can't switch to {label(pending.quality)}", 4000)

    def switch_failed(self, pending: Source, now: float) -> bool:
        """True once the pending source has ended or taken too long. Video
        after audio may wait longer for mpv to play out its audio, which takes
        as long as playback is behind, and once released it is spliced in at
        once."""
        return pending.process.poll() is not None or (
            now - pending.started > SWITCH_TIMEOUT_SECONDS
            and not (self.relay.awaiting_release or self.relay.released)
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
