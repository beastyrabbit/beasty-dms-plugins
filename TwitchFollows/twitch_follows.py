#!/usr/bin/env python3
"""Twitch follows helper for the Twitch Follows DMS plugin.

Reads the account Chatterino is logged in with, so no separate Twitch app or
token is needed. The token only ever leaves this process as a Helix header.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

HELIX_URL = "https://api.twitch.tv/helix"
REQUEST_TIMEOUT_SECONDS = 10
MAX_PAGES = 20
CHATTERINO_FLATPAK_ID = "com.chatterino.chatterino"
CHATTERINO_START_SECONDS = 20
CHATTERINO_SETTLE_SECONDS = 3
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,25}$")
QUALITY_PATTERN = re.compile(r"^[A-Za-z0-9_,+-]{1,40}$")


class TwitchError(Exception):
    pass


def chatterino_settings_paths() -> list[Path]:
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return [
        home
        / ".var/app"
        / CHATTERINO_FLATPAK_ID
        / "data/chatterino/Settings/settings.json",
        data / "chatterino/Settings/settings.json",
    ]


def load_account(paths: list[Path] | None = None) -> dict[str, str]:
    for path in paths if paths is not None else chatterino_settings_paths():
        try:
            accounts = json.loads(path.read_text(encoding="utf-8")).get("accounts", {})
        except (OSError, json.JSONDecodeError):
            continue
        current = accounts.get("current", "")
        candidates = [
            value
            for value in accounts.values()
            if isinstance(value, dict)
            and all(value.get(key) for key in ("oauthToken", "clientID", "userID"))
        ]
        candidates.sort(key=lambda value: value.get("username") != current)
        if candidates:
            return candidates[0]
    raise TwitchError("Log in to Chatterino to see your followed channels.")


def helix_get(
    account: dict[str, str], endpoint: str, params: dict[str, Any]
) -> dict[str, Any]:
    url = f"{HELIX_URL}/{endpoint}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {account['oauthToken']}",
            "Client-Id": account["clientID"],
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise TwitchError(
                "The Chatterino login expired. Log in again in Chatterino."
            ) from None
        raise TwitchError(f"Twitch returned HTTP {error.code}.") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise TwitchError("Could not reach Twitch.") from None


def helix_all(
    account: dict[str, str], endpoint: str, params: dict[str, str]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        page_params = {**params, "first": "100"}
        if cursor:
            page_params["after"] = cursor
        reply = helix_get(account, endpoint, page_params)
        items.extend(reply.get("data", []))
        cursor = reply.get("pagination", {}).get("cursor", "")
        if not cursor:
            break
    return items


def fetch_avatars(account: dict[str, str], user_ids: list[str]) -> dict[str, str]:
    avatars: dict[str, str] = {}
    for start in range(0, len(user_ids), 100):
        reply = helix_get(account, "users", {"id": user_ids[start : start + 100]})
        for user in reply.get("data", []):
            # The 70x70 variant is plenty for a list row and much smaller to load.
            url = user.get("profile_image_url", "")
            avatars[user["id"]] = url.replace("-300x300.", "-70x70.")
    return avatars


def fetch_follows(account: dict[str, str]) -> dict[str, Any]:
    user = {"user_id": account["userID"]}
    streams = helix_all(account, "streams/followed", user)
    channels = helix_all(account, "channels/followed", user)
    user_ids = list(
        dict.fromkeys(
            [stream["user_id"] for stream in streams]
            + [channel["broadcaster_id"] for channel in channels]
        )
    )
    avatars = fetch_avatars(account, user_ids)
    live = [
        {
            "login": stream["user_login"],
            "name": stream.get("user_name") or stream["user_login"],
            "avatar": avatars.get(stream["user_id"], ""),
            "title": stream.get("title", ""),
            "game": stream.get("game_name", ""),
            "viewers": stream.get("viewer_count", 0),
            "startedAt": stream.get("started_at", ""),
        }
        for stream in sorted(streams, key=lambda s: -s.get("viewer_count", 0))
    ]
    live_logins = {channel["login"] for channel in live}
    offline = sorted(
        (
            {
                "login": channel["broadcaster_login"],
                "name": channel.get("broadcaster_name") or channel["broadcaster_login"],
                "avatar": avatars.get(channel["broadcaster_id"], ""),
            }
            for channel in channels
            if channel["broadcaster_login"] not in live_logins
        ),
        key=lambda channel: channel["name"].lower(),
    )
    return {
        "ok": True,
        "user": account.get("username", ""),
        "live": live,
        "offline": offline,
    }


def chatterino_command() -> list[str] | None:
    for name in ("chatterino", "chatterino2"):
        path = shutil.which(name)
        if path:
            return [path]
    flatpak = shutil.which("flatpak")
    if flatpak:
        installed = subprocess.run(
            [flatpak, "info", CHATTERINO_FLATPAK_ID],
            capture_output=True,
            check=False,
        )
        if installed.returncode == 0:
            return [flatpak, "run", CHATTERINO_FLATPAK_ID]
    return None


def notify(summary: str, body: str) -> None:
    notifier = shutil.which("notify-send")
    if notifier:
        subprocess.run(
            [notifier, "-a", "Twitch Follows", summary, body],
            capture_output=True,
            check=False,
        )


def runtime_path(name: str) -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / name


@contextlib.contextmanager
def file_lock(name: str, blocking: bool) -> Iterator[bool]:
    """Hold an flock on a runtime file; yield False if non-blocking and busy."""
    with open(runtime_path(name), "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True


def chatterino_running() -> bool:
    return (
        subprocess.run(
            ["pgrep", "-x", "chatterino"], capture_output=True, check=False
        ).returncode
        == 0
    )


def open_chat(login: str) -> int:
    command = chatterino_command()
    if not command:
        notify("Chatterino is not installed", "Install Chatterino to open chat.")
        return 1
    # A running Chatterino takes --activate from a second launch and that launch
    # exits. While it is still starting, a second launch opens another window,
    # so clicks wait here until the first instance has had time to start.
    with file_lock("twitch-follows-chat.lock", blocking=True):
        cold_start = not chatterino_running()
        subprocess.Popen(
            [*command, "--activate", f"t:{login}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if cold_start:
            deadline = time.monotonic() + CHATTERINO_START_SECONDS
            while time.monotonic() < deadline and not chatterino_running():
                time.sleep(0.25)
            time.sleep(CHATTERINO_SETTLE_SECONDS)
    return 0


def watch(login: str, quality: str, chat_overlay: bool = True) -> int:
    streamlink = shutil.which("streamlink")
    if not streamlink:
        notify(
            "streamlink is not installed", "Install it with: sudo pacman -S streamlink"
        )
        return 1
    with file_lock(f"twitch-follows-watch-{login}.lock", blocking=False) as acquired:
        if not acquired:
            return 0
        return play(streamlink, login, quality, chat_overlay)


def play(streamlink: str, login: str, quality: str, chat_overlay: bool) -> int:
    # Open the window at once: during a pre-roll ad streamlink sends no data,
    # and mpv would otherwise stay invisible until the ad is over.
    player_args = ["--force-window=immediate"]
    overlay = None
    ipc_path = None
    if chat_overlay:
        ipc_path = runtime_path(f"twitch-follows-{login}-{os.getpid()}.sock")
        player_args.append(f"--input-ipc-server={ipc_path}")
        overlay = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("chat_overlay.py")),
                login,
                str(ipc_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    command = [
        streamlink,
        "--player",
        "mpv",
        "--player-args",
        " ".join(player_args),
        "--title",
        "{author} - {title}",
        "--twitch-low-latency",
        f"https://twitch.tv/{login}",
        f"{quality},best",
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        if overlay:
            overlay.terminate()
        if ipc_path:
            ipc_path.unlink(missing_ok=True)
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        notify(f"Could not play {login}", lines[-1] if lines else "streamlink failed.")
    return result.returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list", action="store_true", help="print followed channels as JSON"
    )
    group.add_argument(
        "--watch", metavar="LOGIN", help="play a channel with streamlink and mpv"
    )
    group.add_argument("--chat", metavar="LOGIN", help="open a channel in Chatterino")
    parser.add_argument(
        "--quality", default="best", help="streamlink quality for --watch"
    )
    parser.add_argument(
        "--no-chat-overlay",
        action="store_true",
        help="do not draw chat on top of the --watch player",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        try:
            result = fetch_follows(load_account())
        except TwitchError as error:
            result = {"ok": False, "message": str(error)}
        print(json.dumps(result))
        return 0
    login = args.watch or args.chat
    if not LOGIN_PATTERN.match(login):
        print(f"Invalid channel name: {login!r}", file=sys.stderr)
        return 2
    if args.chat:
        return open_chat(login.lower())
    quality = args.quality if QUALITY_PATTERN.match(args.quality) else "best"
    return watch(login.lower(), quality, not args.no_chat_overlay)


if __name__ == "__main__":
    raise SystemExit(main())
