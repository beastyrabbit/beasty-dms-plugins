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
import math
import os
import re
import secrets
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
GROUP_ID_PATTERN = re.compile(r"^[0-9a-f]{8}$")
# Viewing groups: streams that share one Chatterino window. The QML reads this
# file to number the groups; only the helper writes it, under GROUPS_LOCK.
GROUPS_FILE = "twitch-follows-groups.json"
GROUPS_LOCK = "twitch-follows-groups.lock"
# Arranges a viewing group. SPEC is the hyprweave layout tree (streams in a
# grid, the shared chat in a strip below), WINDOWS the same windows as a list,
# streams first, and FOCUS the window to focus ("" after a stream left).
# The hyprweave group stays unlocked: hyprweave closes every member of a
# locked group when one closes, but a stream that ends should only leave its
# group. The old group is dissolved and created again whenever a stream joins
# or leaves (create() on windows that are still woven is untested upstream).
# Without hyprweave, the windows become a locked Hyprland tab group showing
# the new stream. Hyprland may already have auto-grouped a window into
# whatever unlocked group was focused, so each first leaves any group that
# holds other windows. A stream leaving a tab group needs nothing.
ARRANGE_LUA = """
local spec, wins, focus = SPEC, WINDOWS, "FOCUS"
local function sel(a) return "address:" .. a end
for _, a in ipairs(wins) do
  if not hl.get_window(sel(a)) then return "missing" end
end
local weave = hl.plugin.hyprweave
if weave then
  for _, a in ipairs(wins) do
    if weave.get({ window = sel(a) }) then weave.dissolve({ window = sel(a) }) end
  end
end
if weave and weave.create(spec) then
  if focus ~= "" then hl.dispatch(hl.dsp.focus({ window = sel(focus) })) end
  return "ok"
end
if focus == "" then return "ok" end
local function index_of(group, a)
  for i, m in ipairs(group and group.members or {}) do
    if m.address == a then return i end
  end
end
local ours = {}
for _, a in ipairs(wins) do ours[a] = true end
for _, a in ipairs(wins) do
  local w = hl.get_window(sel(a))
  for _, m in ipairs(w.group and w.group.members or {}) do
    if not ours[m.address] then w.group:remove(w) break end
  end
end
local first = wins[1]
-- lock_active toggles the focused window's group whatever its action says,
-- so it only runs when the lock has to change.
local function set_locked(locked)
  if hl.get_window(sel(first)).group.locked ~= locked then
    hl.dispatch(hl.dsp.focus({ window = sel(first) }))
    hl.dispatch(hl.dsp.group.lock_active({ action = locked and "lock" or "unlock" }))
  end
end
if not hl.get_window(sel(first)).group then
  hl.dispatch(hl.dsp.group.toggle({ window = sel(first) }))
end
set_locked(false)
for _, a in ipairs(wins) do
  local group, w = hl.get_window(sel(first)).group, hl.get_window(sel(a))
  if not index_of(group, a) then
    if w.group then w.group:remove(w) end
    group:add(w)
  end
end
local group = hl.get_window(sel(first)).group
hl.dispatch(hl.dsp.group.active({ index = index_of(group, focus), window = sel(first) }))
set_locked(true)
hl.dispatch(hl.dsp.focus({ window = sel(focus) }))
return "ok"
"""
DEFAULT_VOLUME = 30
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


def pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def load_groups() -> list[dict[str, Any]]:
    """The viewing groups, without streams whose helper died without leaving."""
    try:
        groups = json.loads(runtime_path(GROUPS_FILE).read_text())["groups"]
        for group in groups:
            group["streams"] = [s for s in group["streams"] if pid_alive(s["pid"])]
    except (OSError, ValueError, LookupError, TypeError):
        return []
    return [group for group in groups if group["streams"]]


def save_groups(groups: list[dict[str, Any]]) -> None:
    # Replaced in one step, so the QML and chat_closed() never read half a file.
    path = runtime_path(GROUPS_FILE)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"groups": groups}))
    temporary.replace(path)


def chat_instance(group: dict[str, Any]) -> list[str]:
    return ["--instance", f"twitchfollows-{group['id']}"]


def grid_rows(streams: list[str]) -> list[list[str]]:
    """Side by side, with a second row only from three streams on: on a 16:9
    screen, 2 streams get one row, 3-4 a 2x2 grid and 5-6 a 3x2 grid."""
    columns = math.ceil(math.sqrt(len(streams)))
    return [streams[i : i + columns] for i in range(0, len(streams), columns)]


def weave_spec(streams: list[str], chat: str) -> dict[str, Any]:
    """The hyprweave tree for ARRANGE_LUA: the streams in a grid filling 75%
    of the height and the chat below them across the full width."""

    def window(address: str) -> dict[str, Any]:
        return {"window": f"address:{address}"}

    rows = [
        {"direction": "row", "children": [window(a) for a in row]}
        if len(row) > 1
        else window(row[0])
        for row in grid_rows(streams)
    ]
    video = rows[0] if len(rows) == 1 else {"direction": "column", "children": rows}
    style = {"lock": "none", "gap": 0, "background": "rgb(8c9388)"}
    if not chat:
        return {**video, **style}
    return {
        "direction": "column",
        **style,
        "children": [{**video, "size": 75}, {**window(chat), "size": 25}],
    }


def lua_value(value: Any) -> str:
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{k} = {lua_value(v)}" for k, v in value.items()) + " }"
        )
    if isinstance(value, list):
        return "{ " + ", ".join(lua_value(v) for v in value) + " }"
    if isinstance(value, str):
        return json.dumps(value)
    return str(value)


def arrange(hyprctl: str, group: dict[str, Any], focus: str = "") -> None:
    """Lays out the group's open windows; see ARRANGE_LUA."""
    present = {c.get("address") for c in hypr_clients(hyprctl)}
    streams = [s["window"] for s in group["streams"] if s["window"] in present]
    chat = group["chat"] if group["chat"] in present else ""
    windows = [*streams, chat] if chat else streams
    if len(windows) < 2 or not all(ADDRESS_PATTERN.match(w) for w in windows):
        return
    lua = (
        ARRANGE_LUA.replace("FOCUS", focus if focus in streams else "")
        .replace("WINDOWS", lua_value(windows))
        .replace("SPEC", lua_value(weave_spec(streams, chat)))
    )
    subprocess.run([hyprctl, "repl", lua], capture_output=True, check=False)


def join_group(
    login: str, viewer: subprocess.Popen, group_id: str
) -> tuple[dict[str, Any], subprocess.Popen | None]:
    """Adds the stream to a viewing group and opens its chat there.

    Without group_id, or when that group has closed, the stream starts a new
    group. A native Chatterino (the chatterino-slim fork) shares one window
    per group: the first stream starts it as a named instance, and later
    streams hand it their channel as another tab. The Flatpak can't, so it
    opens one window per stream and each stream gets its own group.

    Returns the group and the Chatterino process started here, if any.
    """
    command = chatterino_command()
    shared = bool(command) and Path(command[0]).name != "flatpak"
    hyprctl = shutil.which("hyprctl")
    # One join or leave at a time, so a new Chatterino window can't be taken
    # for another group's and the layouts don't race.
    with file_lock(GROUPS_LOCK, blocking=True):
        groups = load_groups()
        group = next(
            (g for g in groups if shared and g["shared"] and g["id"] == group_id),
            None,
        )
        if group is None:
            group = {
                "id": secrets.token_hex(4),
                "shared": shared,
                "chat": "",
                "chatPid": 0,
                "streams": [],
            }
            groups.append(group)
        stream = {"login": login, "pid": os.getpid(), "window": ""}
        group["streams"].append(stream)
        save_groups(groups)
        before = chatterino_windows(hypr_clients(hyprctl)) if hyprctl else set()
        chat = None
        if command:
            # -c opens only this channel without saving the layout. With
            # --instance, a running instance takes the channel as a tab and
            # this process exits right away.
            slim = ["--slim", *chat_instance(group)] if shared else []
            chat = subprocess.Popen(
                [*command, *slim, "-c", f"t:{login}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if hyprctl:
            deadline = time.monotonic() + CHAT_WINDOW_WAIT_SECONDS
            while viewer.poll() is None:
                clients = hypr_clients(hyprctl)
                stream["window"] = next(
                    (c["address"] for c in clients if c.get("pid") == viewer.pid), ""
                )
                chats = chatterino_windows(clients)
                new_chat = min(chats - before, default="")
                if new_chat:
                    group["chat"] = new_chat
                    group["chatPid"] = next(
                        c.get("pid", 0) for c in clients if c["address"] == new_chat
                    )
                chat_done = chat is None or chat.poll() is not None
                if stream["window"] and (group["chat"] in chats or chat_done):
                    break
                if time.monotonic() > deadline:
                    break
                time.sleep(0.1)
            save_groups(groups)
            arrange(hyprctl, group, focus=stream["window"])
    return group, chat


def chat_closed(group_id: str) -> bool:
    """True once the group's Chatterino is gone while its streams still play,
    which means the user closed the chat."""
    group = next((g for g in load_groups() if g["id"] == group_id), None)
    return bool(group and group["chatPid"]) and not pid_alive(group["chatPid"])


def leave_group(
    login: str, joined: dict[str, Any], chat: subprocess.Popen | None
) -> None:
    """Takes the stream out of its group and closes its chat tab. The chat
    window closes with the group's last stream."""
    command = chatterino_command()
    hyprctl = shutil.which("hyprctl")
    with file_lock(GROUPS_LOCK, blocking=True):
        groups = load_groups()
        group = next((g for g in groups if g["id"] == joined["id"]), None)
        remaining = []
        if group:
            group["streams"] = [s for s in group["streams"] if s["pid"] != os.getpid()]
            remaining = group["streams"]
            if not remaining:
                groups.remove(group)
            save_groups(groups)
        if joined["shared"] and command:
            # Closing the instance's last tab quits it; without a running
            # instance this does nothing.
            with contextlib.suppress(subprocess.TimeoutExpired):
                subprocess.run(
                    [*command, *chat_instance(joined), "--remove", f"t:{login}"],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
        elif joined["chat"] and hyprctl:
            close = f'hl.dispatch(hl.dsp.window.close({{ window = "address:{joined["chat"]}" }}))'
            subprocess.run([hyprctl, "repl", close], capture_output=True, check=False)
        elif chat:
            chat.terminate()
        if group and remaining and hyprctl:
            arrange(hyprctl, group)
    if chat and not remaining:
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
    group_id: str = "",
    muted: bool = False,
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
            streamlink,
            mpv,
            login,
            quality,
            chat_overlay,
            volume,
            media_title or login,
            group_id,
            muted,
        )


def play(
    streamlink: str,
    mpv: str,
    login: str,
    quality: str,
    chat_overlay: bool,
    volume: int,
    media_title: str,
    group_id: str,
    muted: bool,
) -> int:
    # mpv starts right away and streamlink pipes the stream into it, so the
    # window (with chat and a loading message) appears while streamlink is
    # still talking to Twitch or skipping a pre-roll ad. A muted stream keeps
    # its volume, so unmuting brings it back at that level.
    player = [
        mpv,
        "--force-window=immediate",
        f"--volume={volume}",
        f"--mute={'yes' if muted else 'no'}",
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
    joined = None
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
            joined = join_group(login, viewer, group_id)
            # Whichever exits first decides: mpv closed by the user is normal,
            # streamlink ending first means the stream failed or ended. The
            # group's streams close with its chat window.
            closed_with_chat = False
            while viewer.poll() is None and source.poll() is None:
                if joined[1]:
                    joined[1].poll()  # reaps it, so a closed chat isn't a zombie
                if chat_closed(joined[0]["id"]):
                    closed_with_chat = True
                    viewer.terminate()
                    viewer.wait()
                    break
                time.sleep(0.2)
            source_ended_first = viewer.poll() is None
            if source_ended_first:
                viewer.wait()
            else:
                source.terminate()
            source.wait()
            # Both can be gone by the same poll: mpv exits 2 when it got no
            # video, but 0 when the user quit it.
            stream_failed = (
                not closed_with_chat
                and source.returncode != 0
                and (source_ended_first or viewer.returncode != 0)
            )
    finally:
        # The stream's chat tab closes with it, and the chat window with the
        # group's last stream.
        if joined:
            leave_group(login, *joined)
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
        help="play a channel in mpv, grouped with its chat in Chatterino",
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
    parser.add_argument(
        "--group",
        default="",
        metavar="ID",
        help="join this viewing group for --watch instead of starting a new one",
    )
    parser.add_argument(
        "--muted",
        action="store_true",
        help="start the --watch player muted, keeping --volume for unmuting",
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
    group_id = args.group if GROUP_ID_PATTERN.match(args.group) else ""
    return watch(
        login.lower(),
        quality,
        not args.no_chat_overlay,
        volume,
        args.media_title[:200],
        group_id,
        args.muted,
    )


if __name__ == "__main__":
    raise SystemExit(main())
