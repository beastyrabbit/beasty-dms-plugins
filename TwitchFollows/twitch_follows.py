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
CHATTERINO_CLASSES = {CHATTERINO_FLATPAK_ID, "chatterino"}
CHAT_WINDOW_WAIT_SECONDS = 20
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-f]+$")
# Puts the stream window (STREAM) and its Chatterino window (CHAT) into one
# locked Hyprland tab group with the stream as the shown tab. Hyprland may
# already have auto-grouped either window into whatever unlocked group was
# focused, so each first leaves any group that holds other windows.
GROUP_LUA = """
local S, C = "STREAM", "CHAT"
local function sel(a) return "address:" .. a end
local function index_of(group, a)
  for i, m in ipairs(group and group.members or {}) do
    if m.address == a then return i end
  end
end
for _, a in ipairs({ S, C }) do
  local w = hl.get_window(sel(a))
  if not w then return "missing" end
  for _, m in ipairs(w.group and w.group.members or {}) do
    if m.address ~= S and m.address ~= C then w.group:remove(w) break end
  end
end
if not hl.get_window(sel(S)).group then
  hl.dispatch(hl.dsp.group.toggle({ window = sel(S) }))
end
local stream, chat = hl.get_window(sel(S)), hl.get_window(sel(C))
if not index_of(stream.group, C) then
  if chat.group then chat.group:remove(chat) end
  stream.group:add(chat)
end
stream = hl.get_window(sel(S))
hl.dispatch(hl.dsp.group.active({ index = index_of(stream.group, S), window = sel(S) }))
hl.dispatch(hl.dsp.focus({ window = sel(S) }))
hl.dispatch(hl.dsp.group.lock_active({ action = "lock" }))
return "ok"
"""
DEFAULT_VOLUME = 25
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,25}$")
QUALITY_PATTERN = re.compile(r"^[A-Za-z0-9_,+-]{1,40}$")
# Twitch renditions from highest to lowest. Transcodes are named like 480p30;
# a source stream can have no frame rate in its name (720p).
RENDITIONS = [
    "1080p60", "1080p", "1080p30", "720p60", "720p", "720p30",
    "480p", "480p30", "360p", "360p30", "160p", "160p30",
]  # fmt: skip


def ceiling(quality: str) -> str:
    """streamlink tries these in order: the setting and everything below it,
    then the best stream when the channel has nothing at or below it."""
    return ",".join([*RENDITIONS[RENDITIONS.index(quality) :], "best"])


QUALITY_FALLBACKS = {
    "best": "best",
    "1080p60": ceiling("1080p60"),
    "720p60": ceiling("720p60"),
    "480p": ceiling("480p"),
    "audio_only": "audio_only,worst",
}


class TwitchError(Exception):
    pass


def chatterino_settings_paths() -> list[Path]:
    home = Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    flatpak = (
        home / ".var/app" / CHATTERINO_FLATPAK_ID / "data/chatterino/Settings"
    ) / "settings.json"
    native = data / "chatterino/Settings/settings.json"
    # Read the account of the Chatterino that chatterino_command() opens.
    if shutil.which("chatterino") or shutil.which("chatterino2"):
        return [native, flatpak]
    return [flatpak, native]


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
    streams = helix_all(account, "streams/followed", {"user_id": account["userID"]})
    avatars = fetch_avatars(account, [stream["user_id"] for stream in streams])
    live = [
        {
            "login": stream["user_login"],
            "name": stream.get("user_name") or stream["user_login"],
            "avatar": avatars.get(stream["user_id"], ""),
            "title": stream.get("title", ""),
        }
        for stream in sorted(streams, key=lambda s: -s.get("viewer_count", 0))
    ]
    return {"ok": True, "user": account.get("username", ""), "live": live}


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


def hypr_clients(hyprctl: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [hyprctl, "clients", "-j"], capture_output=True, text=True, check=False
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def chatterino_windows(clients: list[dict[str, Any]]) -> set[str]:
    return {c["address"] for c in clients if c.get("class") in CHATTERINO_CLASSES}


def open_chat_tab(
    login: str, viewer: subprocess.Popen
) -> tuple[subprocess.Popen, str] | None:
    """Open Chatterino for the channel and tab it behind the stream window.

    Returns the Chatterino process and its window address ("" when it could
    not be grouped), or None when Chatterino is not installed.
    """
    command = chatterino_command()
    if not command:
        return None
    hyprctl = shutil.which("hyprctl")
    # One launch at a time, so a new Chatterino window can't be taken for
    # another channel's.
    with file_lock("twitch-follows-chat-launch.lock", blocking=True):
        before = chatterino_windows(hypr_clients(hyprctl)) if hyprctl else set()
        # -c starts a separate Chatterino with only this channel and without
        # saving its layout.
        chat = subprocess.Popen(
            [*command, "-c", f"t:{login}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not hyprctl:
            return chat, ""
        deadline = time.monotonic() + CHAT_WINDOW_WAIT_SECONDS
        stream_window = chat_window = ""
        while viewer.poll() is None and chat.poll() is None:
            clients = hypr_clients(hyprctl)
            stream_window = next(
                (c["address"] for c in clients if c.get("pid") == viewer.pid), ""
            )
            chat_window = min(chatterino_windows(clients) - before, default="")
            if (stream_window and chat_window) or time.monotonic() > deadline:
                break
            time.sleep(0.1)
    if not (
        ADDRESS_PATTERN.match(stream_window) and ADDRESS_PATTERN.match(chat_window)
    ):
        return chat, ""
    lua = GROUP_LUA.replace("STREAM", stream_window).replace("CHAT", chat_window)
    subprocess.run([hyprctl, "repl", lua], capture_output=True, check=False)
    return chat, chat_window


def close_chat_tab(chat: subprocess.Popen, window: str) -> None:
    hyprctl = shutil.which("hyprctl")
    if window and hyprctl:
        close = f'hl.dispatch(hl.dsp.window.close({{ window = "address:{window}" }}))'
        subprocess.run([hyprctl, "repl", close], capture_output=True, check=False)
    else:
        chat.terminate()
    try:
        chat.wait(timeout=5)
    except subprocess.TimeoutExpired:
        chat.terminate()
        chat.wait()


def watch(
    login: str,
    quality: str,
    chat_overlay: bool = True,
    volume: int = DEFAULT_VOLUME,
    media_title: str = "",
) -> int:
    streamlink = shutil.which("streamlink")
    mpv = shutil.which("mpv")
    if not streamlink or not mpv:
        missing = "streamlink" if not streamlink else "mpv"
        notify(
            f"{missing} is not installed", f"Install it with: sudo pacman -S {missing}"
        )
        return 1
    with file_lock(f"twitch-follows-watch-{login}.lock", blocking=False) as acquired:
        if not acquired:
            return 0
        return play(
            streamlink, mpv, login, quality, chat_overlay, volume, media_title or login
        )


def play(
    streamlink: str,
    mpv: str,
    login: str,
    quality: str,
    chat_overlay: bool,
    volume: int,
    media_title: str,
) -> int:
    # mpv starts right away and streamlink pipes the stream into it, so the
    # window (with chat and a loading message) appears while streamlink is
    # still talking to Twitch or skipping a pre-roll ad.
    player = [
        mpv,
        "--force-window=immediate",
        f"--volume={volume}",
        f"--force-media-title={media_title}",
    ]
    # The chat relay always runs because it also handles `End` (jump to live);
    # the setting only decides whether chat starts visible.
    ipc_path = runtime_path(f"twitch-follows-{login}-{os.getpid()}.sock")
    player.append(f"--input-ipc-server={ipc_path}")
    overlay = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("chat_overlay.py")),
            login,
            str(ipc_path),
            *([] if chat_overlay else ["--hidden"]),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # streamlink's log goes to a file: a pipe nobody reads could fill up and
    # stall a long stream.
    log_path = runtime_path(f"twitch-follows-{login}.log")
    chat_tab = None
    try:
        with open(log_path, "w") as log:
            source = subprocess.Popen(
                [
                    streamlink,
                    "--stdout",
                    "--twitch-low-latency",
                    f"https://twitch.tv/{login}",
                    QUALITY_FALLBACKS.get(quality, f"{quality},best"),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
            )
            viewer = subprocess.Popen(
                [*player, "-"],
                stdin=source.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            assert source.stdout is not None
            source.stdout.close()
            chat_tab = open_chat_tab(login, viewer)
            # Whichever exits first decides: mpv closed by the user is normal,
            # streamlink ending first means the stream failed or ended.
            while viewer.poll() is None and source.poll() is None:
                time.sleep(0.2)
            source_ended_first = viewer.poll() is None
            if source_ended_first:
                viewer.wait()
            else:
                source.terminate()
            source.wait()
            # Both can be gone by the same poll: mpv exits 2 when it got no
            # video, but 0 when the user quit it.
            stream_failed = source.returncode != 0 and (
                source_ended_first or viewer.returncode != 0
            )
    finally:
        # The chat tab belongs to the stream: closing the stream closes it.
        if chat_tab:
            close_chat_tab(*chat_tab)
        overlay.terminate()
        overlay.wait()
        ipc_path.unlink(missing_ok=True)
    if stream_failed:
        lines = log_path.read_text(errors="replace").strip().splitlines()
        notify(f"Could not play {login}", lines[-1] if lines else "streamlink failed.")
        return source.returncode
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list", action="store_true", help="print followed channels as JSON"
    )
    group.add_argument(
        "--watch",
        metavar="LOGIN",
        help="play a channel in mpv, tabbed with its chat in Chatterino",
    )
    parser.add_argument(
        "--quality", default="best", help="streamlink quality for --watch"
    )
    parser.add_argument(
        "--volume",
        type=int,
        default=DEFAULT_VOLUME,
        help="starting mpv volume (0-100) for --watch",
    )
    parser.add_argument(
        "--media-title", default="", help="mpv window title for --watch"
    )
    parser.add_argument(
        "--no-chat-overlay",
        action="store_true",
        help="start the --watch player with chat hidden",
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
    login = args.watch
    if not LOGIN_PATTERN.match(login):
        print(f"Invalid channel name: {login!r}", file=sys.stderr)
        return 2
    quality = args.quality if QUALITY_PATTERN.match(args.quality) else "best"
    volume = min(max(args.volume, 0), 100)
    return watch(
        login.lower(), quality, not args.no_chat_overlay, volume, args.media_title[:200]
    )


if __name__ == "__main__":
    raise SystemExit(main())
