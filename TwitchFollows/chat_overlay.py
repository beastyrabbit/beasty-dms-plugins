#!/usr/bin/env python3
"""Draw a channel's Twitch chat on top of an mpv window.

Reads chat anonymously over Twitch IRC and renders the latest messages
through mpv's JSON IPC. With Pillow installed, chat is drawn as an image with
Twitch, 7TV, BTTV and FFZ emotes (`overlay-add`); without it, as plain ASS text.
Press `c` in mpv, or the uosc chat button, to hide or show it; `End` jumps back
to live. Exits when mpv closes.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import socket
import ssl
import sys
import textwrap
import threading
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import chat_image
    import emotes
except ImportError:  # Pillow missing: fall back to text-only chat.
    chat_image = None
    emotes = None

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
CONNECT_WAIT_SECONDS = 90
MAX_MESSAGES = 30
MAX_LINES = 14
MAX_AGE_SECONDS = 120
WRAP_WIDTH = 48
TOGGLE_KEY = "c"
# Marks a Twitch window, so uosc can show its chat button only there.
MARKER_PROPERTY = "user-data/twitch-chat"
# "yes" or "no"; the uosc chat button sets it, and the `c` key asks us to flip it.
VISIBLE_PROPERTY = "user-data/twitch-chat-visible"
TOGGLE_MESSAGE = "twitch-chat-toggle"
# `End` (and a uosc button) jump back to the live edge, mirroring mpv's `Home`.
LIVE_KEY = "END"
LIVE_MESSAGE = "twitch-go-live"
LIVE_REQUEST_ID = 42
# Stay this far behind the newest buffered frame so playback doesn't stall.
LIVE_MARGIN_SECONDS = 1.5
OSD_PROPERTY = "osd-dimensions"
IMAGE_OVERLAY_ID = 0
# Redraw rate while an animated emote is on screen.
ANIMATION_FPS = 15
# Twitch's palette for users who never picked a name color.
DEFAULT_COLORS = (
    "#FF0000",
    "#0000FF",
    "#008000",
    "#B22222",
    "#FF7F50",
    "#9ACD32",
    "#FF4500",
    "#2E8B57",
    "#DAA520",
    "#D2691E",
    "#5F9EA0",
    "#1E90FF",
    "#FF69B4",
    "#8A2BE2",
    "#00FF7F",
)


@dataclass
class ChatMessage:
    name: str
    color: str
    text: str
    action: bool
    received: float
    # Twitch's own emotes as (first, last, emote id) character positions in `text`.
    emotes: tuple[tuple[int, int, str], ...] = ()


def parse_tags(raw: str) -> dict[str, str]:
    tags = {}
    for item in raw.split(";"):
        key, _, value = item.partition("=")
        tags[key] = value.replace("\\s", " ").replace("\\:", ";").replace("\\\\", "\\")
    return tags


def split_tags(line: str) -> tuple[dict[str, str], str]:
    if not line.startswith("@"):
        return {}, line
    raw_tags, _, line = line[1:].partition(" ")
    return parse_tags(raw_tags), line


def parse_emote_tag(raw: str, text_length: int) -> tuple[tuple[int, int, str], ...]:
    """`25:0-4,12-16/1902:6-10` into sorted (first, last, id) positions."""
    positions = []
    for entry in raw.split("/"):
        emote_id, _, ranges = entry.partition(":")
        for span in ranges.split(","):
            first, _, last = span.partition("-")
            if (
                first.isdigit()
                and last.isdigit()
                and int(first) <= int(last) < text_length
            ):
                positions.append((int(first), int(last), emote_id))
    return tuple(sorted(positions))


def parse_privmsg(line: str, now: float) -> ChatMessage | None:
    tags, line = split_tags(line)
    prefix, _, rest = line.partition(" ")
    command, _, rest = rest.partition(" ")
    if command != "PRIVMSG" or " :" not in rest:
        return None
    text = rest.split(" :", 1)[1]
    login = prefix.lstrip(":").split("!", 1)[0]
    name = tags.get("display-name") or login
    action = text.startswith("\x01ACTION ") and text.endswith("\x01")
    if action:
        text = text[len("\x01ACTION ") : -1]
    color = tags.get("color") or default_color(login)
    positions = parse_emote_tag(tags.get("emotes", ""), len(text))
    return ChatMessage(name, color, text, action, now, positions)


def room_id(line: str) -> str:
    """The channel's Twitch user id, which the emote providers are keyed by."""
    tags, _ = split_tags(line)
    return tags.get("room-id", "")


def default_color(login: str) -> str:
    digest = hashlib.sha1(login.encode(), usedforsecurity=False).digest()
    return DEFAULT_COLORS[digest[0] % len(DEFAULT_COLORS)]


def ass_color(hex_color: str) -> str:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        value = "FFFFFF"
    return f"&H{value[4:6]}{value[2:4]}{value[0:2]}&"


def ass_escape(text: str) -> str:
    # A BOM after a backslash stops libass from reading it as an override tag.
    return text.replace("\\", "\\﻿").replace("{", "\\{").replace("}", "\\}")


def render(messages: list[ChatMessage], now: float) -> str:
    lines: list[str] = []
    for message in messages:
        if now - message.received > MAX_AGE_SECONDS:
            continue
        separator = " " if message.action else ": "
        wrapped = textwrap.wrap(
            message.name + separator + message.text,
            WRAP_WIDTH,
            break_long_words=True,
        ) or [message.name]
        body_style = (
            f"{{\\b0\\i1\\1c{ass_color(message.color)}}}"
            if message.action
            else "{\\b0\\i0\\1c&HFFFFFF&}"
        )
        name_part = wrapped[0][: len(message.name)]
        first = (
            f"{{\\b1\\1c{ass_color(message.color)}}}{ass_escape(name_part)}"
            f"{body_style}{ass_escape(wrapped[0][len(message.name) :])}"
        )
        lines.append(first)
        lines.extend(body_style + ass_escape(line) for line in wrapped[1:])
    if not lines:
        return ""
    style = "{\\an1\\pos(16,640)\\fs20\\bord2\\shad0\\3c&H000000&\\4a&HFF&}"
    return style + "\\N".join(lines[-MAX_LINES:])


class ChatFeed:
    """Keeps the latest chat messages, reconnecting to Twitch IRC as needed."""

    def __init__(self, channel: str, on_room=None) -> None:
        self.channel = channel
        self.on_room = on_room
        self.messages: collections.deque[ChatMessage] = collections.deque(
            maxlen=MAX_MESSAGES
        )
        self.changed = threading.Event()
        self.lock = threading.Lock()

    def snapshot(self) -> list[ChatMessage]:
        with self.lock:
            return list(self.messages)

    def run_forever(self) -> None:
        delay = 2
        while True:
            try:
                self.read_chat()
                delay = 2
            except OSError:
                pass
            time.sleep(delay)
            delay = min(delay * 2, 60)

    def read_chat(self) -> None:
        context = ssl.create_default_context()
        with (
            socket.create_connection((IRC_HOST, IRC_PORT), timeout=30) as raw,
            context.wrap_socket(raw, server_hostname=IRC_HOST) as irc,
        ):
            irc.settimeout(360)
            # Anonymous read-only login: justinfan nicks need no token.
            nick = f"justinfan{int(time.time()) % 100000}"
            irc.sendall(
                (
                    # `commands` adds ROOMSTATE, whose room-id lets emotes load
                    # before the first message arrives.
                    "CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
                    f"NICK {nick}\r\n"
                    f"JOIN #{self.channel}\r\n"
                ).encode()
            )
            for line in irc.makefile("r", encoding="utf-8", errors="replace"):
                line = line.rstrip("\r\n")
                if line.startswith("PING"):
                    irc.sendall(("PONG" + line[4:] + "\r\n").encode())
                    continue
                if self.on_room and (room := room_id(line)):
                    self.on_room(room)
                message = parse_privmsg(line, time.time())
                if message:
                    with self.lock:
                        self.messages.append(message)
                    self.changed.set()


class Mpv:
    def __init__(self, path: str) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.buffer = b""

    def send(self, command: list | dict, request_id: int | None = None) -> None:
        message: dict = {"command": command}
        if request_id is not None:
            message["request_id"] = request_id
        self.sock.sendall(json.dumps(message).encode() + b"\n")

    def show(self, data: str, hidden: bool) -> None:
        self.send(
            {
                "name": "osd-overlay",
                "id": 1,
                "format": "ass-events",
                "data": data,
                "res_y": 720,
                "z": 1,
                "hidden": hidden,
            }
        )

    def show_image(self, path: Path, image, x: int, y: int) -> None:
        # mpv copies the file while handling the command; writing a new file
        # and renaming it means it never sees a half-written frame.
        temporary = path.with_name(path.name + ".new")
        temporary.write_bytes(chat_image.to_bgra(image))
        os.replace(temporary, path)
        self.send(
            {
                "name": "overlay-add",
                "id": IMAGE_OVERLAY_ID,
                "x": x,
                "y": y,
                "file": str(path),
                "offset": 0,
                "fmt": "bgra",
                "w": image.width,
                "h": image.height,
                "stride": 4 * image.width,
            }
        )

    def hide_image(self) -> None:
        self.send(["overlay-remove", IMAGE_OVERLAY_ID])

    def events(self, timeout: float) -> list[dict]:
        """Return pending mpv events; raise EOFError when mpv has closed."""
        self.sock.settimeout(timeout)
        try:
            chunk = self.sock.recv(65536)
        except TimeoutError:
            return []
        if not chunk:
            raise EOFError
        self.buffer += chunk
        *complete, self.buffer = self.buffer.split(b"\n")
        events = []
        for raw in complete:
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return events


def connect_mpv(path: str) -> Mpv | None:
    deadline = time.monotonic() + CONNECT_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            return Mpv(path)
        except OSError:
            time.sleep(0.5)
    return None


class Overlay:
    """Keeps mpv's chat overlay in step with the chat feed and the user's toggles."""

    def __init__(self, mpv: Mpv, channel: str, ipc_path: str, hidden: bool) -> None:
        self.mpv = mpv
        self.channel = channel
        self.hidden = hidden
        self.loading = True
        self.last_draw = 0.0
        self.animated = False
        self.image_shown = False
        self.osd = (0, 0)
        self.image_path = Path(ipc_path).with_suffix(".chat.bgra")
        # Set from download threads when an emote set or image arrives.
        self.emotes_changed = threading.Event()
        self.renderer = None
        on_room = None
        if chat_image:
            catalog = emotes.EmoteCatalog(self.emotes_changed.set)
            images = emotes.EmoteImages(self.emotes_changed.set)
            self.renderer = chat_image.ChatRenderer(catalog, images)
            catalog.load_global()
            on_room = catalog.load_channel
        self.feed = ChatFeed(channel, on_room)

    def run(self) -> int:
        threading.Thread(target=self.feed.run_forever, daemon=True).start()
        self.mpv.send(
            ["set_property", VISIBLE_PROPERTY, "no" if self.hidden else "yes"]
        )
        self.mpv.send(["set_property", MARKER_PROPERTY, True])
        self.mpv.send(["observe_property", 1, VISIBLE_PROPERTY])
        self.mpv.send(["observe_property", 2, OSD_PROPERTY])
        self.mpv.send(["keybind", TOGGLE_KEY, f"script-message {TOGGLE_MESSAGE}"])
        self.mpv.send(["keybind", LIVE_KEY, f"script-message {LIVE_MESSAGE}"])
        # Twitch pre-roll ads can hold back the video for half a minute.
        self.mpv.send(["show-text", f"Loading {self.channel}…", 60000])
        try:
            while True:
                for event in self.mpv.events(timeout=self.wait_time()):
                    self.handle(event)
                self.draw_if_due()
        except (EOFError, OSError):
            return 0
        finally:
            self.image_path.unlink(missing_ok=True)

    def wait_time(self) -> float:
        if not self.animated or self.hidden:
            return 1.0
        next_frame = self.last_draw + 1 / ANIMATION_FPS
        return max(0.005, next_frame - time.monotonic())

    def handle(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "client-message" and event.get("args") == [LIVE_MESSAGE]:
            self.mpv.send(["get_property", "demuxer-cache-time"], LIVE_REQUEST_ID)
        if event.get("request_id") == LIVE_REQUEST_ID and isinstance(
            event.get("data"), (int, float)
        ):
            target = max(0.0, event["data"] - LIVE_MARGIN_SECONDS)
            self.mpv.send(["seek", target, "absolute"])
            self.mpv.send(["show-text", "Live"])
        if kind == "client-message" and event.get("args") == [TOGGLE_MESSAGE]:
            self.mpv.send(
                ["set_property", VISIBLE_PROPERTY, "yes" if self.hidden else "no"]
            )
        if self.loading and kind == "playback-restart":
            self.loading = False
            self.mpv.send(["show-text", "", 1])
        if kind == "property-change":
            self.property_changed(event)

    def property_changed(self, event: dict) -> None:
        if (
            event.get("name") == VISIBLE_PROPERTY
            and (event.get("data") == "no") != self.hidden
        ):
            self.hidden = not self.hidden
            self.mpv.send(["show-text", "Chat hidden" if self.hidden else "Chat shown"])
            self.last_draw = 0.0
        if event.get("name") == OSD_PROPERTY and isinstance(event.get("data"), dict):
            size = (int(event["data"].get("w", 0)), int(event["data"].get("h", 0)))
            if size != self.osd:
                self.osd = size
                self.last_draw = 0.0

    def draw_if_due(self) -> None:
        now = time.monotonic()
        if self.emotes_changed.is_set() and self.renderer:
            self.emotes_changed.clear()
            self.renderer.invalidate()
            self.last_draw = 0.0
        interval = 1 / ANIMATION_FPS if self.animated and not self.hidden else 5
        if self.feed.changed.is_set() or now - self.last_draw >= interval:
            self.feed.changed.clear()
            self.draw()
            self.last_draw = now

    def draw(self) -> None:
        if not self.renderer:
            self.mpv.show(render(self.feed.snapshot(), time.time()), self.hidden)
            return
        now = time.time()
        recent = [
            message
            for message in self.feed.snapshot()
            if now - message.received <= MAX_AGE_SECONDS
        ]
        panel = None
        if not self.hidden and min(self.osd) > 0:
            panel = self.renderer.layout(recent, *self.osd)
        self.animated = bool(panel and panel.animated)
        if panel:
            image = panel.draw(time.monotonic() * 1000)
            self.mpv.show_image(self.image_path, image, panel.x, panel.y)
            self.image_shown = True
        elif self.image_shown:
            self.mpv.hide_image()
            self.image_shown = False


def run(channel: str, ipc_path: str, hidden: bool = False) -> int:
    mpv = connect_mpv(ipc_path)
    if not mpv:
        return 1
    return Overlay(mpv, channel, ipc_path, hidden).run()


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4) or sys.argv[3:] not in ([], ["--hidden"]):
        print(
            "usage: chat_overlay.py CHANNEL MPV_IPC_SOCKET [--hidden]", file=sys.stderr
        )
        raise SystemExit(2)
    raise SystemExit(
        run(sys.argv[1].lower(), sys.argv[2], sys.argv[3:] == ["--hidden"])
    )
