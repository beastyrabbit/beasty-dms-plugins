#!/usr/bin/env python3
"""Draw a channel's Twitch chat on top of an mpv window.

Reads chat anonymously over Twitch IRC and renders the latest messages
through mpv's JSON IPC as an OSD overlay. Press `c` in mpv, or the uosc chat
button, to hide or show it. Exits when mpv closes.
"""

from __future__ import annotations

import collections
import hashlib
import json
import socket
import ssl
import sys
import textwrap
import threading
import time
from dataclasses import dataclass

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


def parse_tags(raw: str) -> dict[str, str]:
    tags = {}
    for item in raw.split(";"):
        key, _, value = item.partition("=")
        tags[key] = value.replace("\\s", " ").replace("\\:", ";").replace("\\\\", "\\")
    return tags


def parse_privmsg(line: str, now: float) -> ChatMessage | None:
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        tags = parse_tags(raw_tags)
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
    return ChatMessage(name, color, text, action, now)


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

    def __init__(self, channel: str) -> None:
        self.channel = channel
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
                    "CAP REQ :twitch.tv/tags\r\n"
                    f"NICK {nick}\r\n"
                    f"JOIN #{self.channel}\r\n"
                ).encode()
            )
            for line in irc.makefile("r", encoding="utf-8", errors="replace"):
                line = line.rstrip("\r\n")
                if line.startswith("PING"):
                    irc.sendall(("PONG" + line[4:] + "\r\n").encode())
                    continue
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

    def send(self, command: list | dict) -> None:
        self.sock.sendall(json.dumps({"command": command}).encode() + b"\n")

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


def run(channel: str, ipc_path: str) -> int:
    mpv = connect_mpv(ipc_path)
    if not mpv:
        return 1
    feed = ChatFeed(channel)
    threading.Thread(target=feed.run_forever, daemon=True).start()
    mpv.send(["set_property", VISIBLE_PROPERTY, "yes"])
    mpv.send(["set_property", MARKER_PROPERTY, True])
    mpv.send(["observe_property", 1, VISIBLE_PROPERTY])
    mpv.send(["keybind", TOGGLE_KEY, f"script-message {TOGGLE_MESSAGE}"])
    # Twitch pre-roll ads can hold back the video for half a minute.
    mpv.send(["show-text", f"Loading {channel}…", 60000])
    loading = True
    hidden = False
    last_draw = 0.0
    try:
        while True:
            for event in mpv.events(timeout=1.0):
                if event.get("event") == "client-message" and event.get("args") == [
                    TOGGLE_MESSAGE
                ]:
                    mpv.send(
                        ["set_property", VISIBLE_PROPERTY, "yes" if hidden else "no"]
                    )
                if loading and event.get("event") == "playback-restart":
                    loading = False
                    mpv.send(["show-text", "", 1])
                if (
                    event.get("event") == "property-change"
                    and event.get("name") == VISIBLE_PROPERTY
                    and (event.get("data") == "no") != hidden
                ):
                    hidden = not hidden
                    mpv.send(["show-text", "Chat hidden" if hidden else "Chat shown"])
                    last_draw = 0.0
            now = time.monotonic()
            if feed.changed.is_set() or now - last_draw > 5:
                feed.changed.clear()
                mpv.show(render(feed.snapshot(), time.time()), hidden)
                last_draw = now
    except (EOFError, OSError):
        return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: chat_overlay.py CHANNEL MPV_IPC_SOCKET", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(run(sys.argv[1].lower(), sys.argv[2]))
