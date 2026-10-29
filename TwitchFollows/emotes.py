"""Twitch, 7TV, BTTV and FFZ emotes for the chat overlay.

Emote sets come from the providers' public APIs (no login needed); images are
downloaded on first use and kept in memory for the life of the stream window.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

USER_AGENT = "beasty-dms-plugins TwitchFollows chat overlay"
FETCH_TIMEOUT_SECONDS = 15
FETCH_ATTEMPTS = 3
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_FRAMES = 240
# Browsers play GIF frames this short at 100 ms; emote artists rely on that.
MIN_FRAME_MS = 20
SLOW_FRAME_MS = 100
# 7TV marks emotes that are drawn on top of the previous one.
SEVENTV_ACTIVE_ZERO_WIDTH = 1 << 0
SEVENTV_EMOTE_ZERO_WIDTH = 1 << 8

SEVENTV_GLOBAL = "https://7tv.io/v3/emote-sets/global"
SEVENTV_CHANNEL = "https://7tv.io/v3/users/twitch/{room_id}"
BTTV_GLOBAL = "https://api.betterttv.net/3/cached/emotes/global"
BTTV_CHANNEL = "https://api.betterttv.net/3/cached/users/twitch/{room_id}"
FFZ_GLOBAL = "https://api.betterttv.net/3/cached/frankerfacez/emotes/global"
FFZ_CHANNEL = "https://api.betterttv.net/3/cached/frankerfacez/users/twitch/{room_id}"


@dataclass(frozen=True)
class Emote:
    name: str
    url: str
    zero_width: bool = False


def twitch_emote(emote_id: str, name: str) -> Emote:
    # `default` serves the animated GIF when the emote has one.
    return Emote(
        name, f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/default/dark/2.0"
    )


def seventv_emotes(payload: object) -> list[Emote]:
    """Emotes from a 7TV emote set, or from a 7TV user (its active set)."""
    if not isinstance(payload, dict):
        return []
    emote_set = payload.get("emote_set", payload)
    emotes = []
    for entry in (emote_set or {}).get("emotes") or []:
        data = entry.get("data") or {}
        host = data.get("host") or {}
        if not entry.get("name") or not host.get("url"):
            continue
        zero_width = bool(
            (entry.get("flags") or 0) & SEVENTV_ACTIVE_ZERO_WIDTH
            or (data.get("flags") or 0) & SEVENTV_EMOTE_ZERO_WIDTH
        )
        emotes.append(Emote(entry["name"], f"https:{host['url']}/2x.webp", zero_width))
    return emotes


def bttv_emotes(payload: object) -> list[Emote]:
    """Emotes from BTTV's global list or a channel's own and shared emotes."""
    if isinstance(payload, dict):
        entries = (payload.get("channelEmotes") or []) + (
            payload.get("sharedEmotes") or []
        )
    else:
        entries = payload if isinstance(payload, list) else []
    return [
        Emote(entry["code"], f"https://cdn.betterttv.net/emote/{entry['id']}/2x.webp")
        for entry in entries
        if entry.get("code") and entry.get("id")
    ]


def ffz_emotes(payload: object) -> list[Emote]:
    """FFZ emotes as mirrored by BTTV, which serves them in one flat list."""
    emotes = []
    for entry in payload if isinstance(payload, list) else []:
        images = entry.get("images") or {}
        url = images.get("2x") or images.get("1x")
        if entry.get("code") and url:
            emotes.append(Emote(entry["code"], url))
    return emotes


def fetch(url: str) -> bytes:
    # Image URLs come from third-party APIs; never let one open a local file.
    if not url.startswith("https://"):
        raise ValueError(f"{url} is not an https URL")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        data = response.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"{url} is larger than {MAX_IMAGE_BYTES} bytes")
    return data


def fetch_json(url: str) -> object:
    for attempt in range(FETCH_ATTEMPTS):
        try:
            return json.loads(fetch(url))
        except (OSError, ValueError):
            if attempt + 1 < FETCH_ATTEMPTS:
                time.sleep(2**attempt)
    return None


# Earlier providers win when two use the same name, as in Chatterino.
GLOBAL_SOURCES: tuple[tuple[str, Callable[[object], list[Emote]]], ...] = (
    (SEVENTV_GLOBAL, seventv_emotes),
    (BTTV_GLOBAL, bttv_emotes),
    (FFZ_GLOBAL, ffz_emotes),
)
CHANNEL_SOURCES: tuple[tuple[str, Callable[[object], list[Emote]]], ...] = (
    (SEVENTV_CHANNEL, seventv_emotes),
    (BTTV_CHANNEL, bttv_emotes),
    (FFZ_CHANNEL, ffz_emotes),
)


def merge(sets: list[list[Emote]]) -> dict[str, Emote]:
    merged: dict[str, Emote] = {}
    for emotes in reversed(sets):
        merged.update((emote.name, emote) for emote in emotes)
    return merged


class EmoteCatalog:
    """Maps words in chat to third-party emotes; channel emotes beat global ones."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self.on_change = on_change
        self.lock = threading.Lock()
        self.global_emotes: dict[str, Emote] = {}
        self.channel_emotes: dict[str, Emote] = {}
        self.room_id = ""

    def lookup(self, word: str) -> Emote | None:
        with self.lock:
            return self.channel_emotes.get(word) or self.global_emotes.get(word)

    def load_global(self) -> None:
        threading.Thread(
            target=self._load, args=(GLOBAL_SOURCES, ""), daemon=True
        ).start()

    def load_channel(self, room_id: str) -> None:
        with self.lock:
            if not room_id.isdigit() or room_id == self.room_id:
                return
            self.room_id = room_id
        threading.Thread(
            target=self._load, args=(CHANNEL_SOURCES, room_id), daemon=True
        ).start()

    def _load(self, sources, room_id: str) -> None:
        sets = [
            parse(fetch_json(url.format(room_id=room_id))) for url, parse in sources
        ]
        emotes = merge(sets)
        with self.lock:
            if room_id:
                self.channel_emotes = emotes
            else:
                self.global_emotes = emotes
        self.on_change()


class EmoteImage:
    """Decoded frames of one emote, resized lazily to the height being drawn."""

    def __init__(self, frames: list[Image.Image], durations: list[int]) -> None:
        self.frames = frames
        self.durations = durations
        self.total_ms = sum(durations)
        self.sized: dict[int, list[Image.Image]] = {}

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    def width_for(self, height: int) -> int:
        first = self.frames[0]
        return max(1, round(first.width * height / first.height))

    def frame(self, height: int, now_ms: float) -> Image.Image:
        frames = self.sized.get(height)
        if frames is None:
            size = (self.width_for(height), height)
            frames = [
                frame.resize(size, Image.Resampling.LANCZOS) for frame in self.frames
            ]
            self.sized[height] = frames
        if not self.animated:
            return frames[0]
        position = now_ms % self.total_ms
        for index, duration in enumerate(self.durations):
            if position < duration:
                return frames[index]
            position -= duration
        return frames[-1]


def decode(data: bytes) -> EmoteImage:
    image = Image.open(io.BytesIO(data))
    frames, durations = [], []
    for index in range(min(getattr(image, "n_frames", 1), MAX_FRAMES)):
        image.seek(index)
        frames.append(image.convert("RGBA"))
        duration = image.info.get("duration") or SLOW_FRAME_MS
        durations.append(SLOW_FRAME_MS if duration < MIN_FRAME_MS else int(duration))
    if not frames or frames[0].height == 0:
        raise ValueError("emote has no frames")
    return EmoteImage(frames, durations)


class EmoteImages:
    """Downloads emote images in the background and reports when one arrives."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self.on_change = on_change
        self.lock = threading.Lock()
        self.images: dict[str, EmoteImage | None] = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def get(self, url: str) -> EmoteImage | None:
        """The image if it has loaded; otherwise starts the download (once)."""
        with self.lock:
            if url in self.images:
                return self.images[url]
            self.images[url] = None
        self.pool.submit(self._download, url)
        return None

    def _download(self, url: str) -> None:
        for attempt in range(FETCH_ATTEMPTS):
            try:
                image = decode(fetch(url))
            except (OSError, ValueError, Image.DecompressionBombError):
                time.sleep(2**attempt)
                continue
            with self.lock:
                self.images[url] = image
            self.on_change()
            return
