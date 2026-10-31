#!/usr/bin/env python3
"""Twitch follows helper for the Twitch Follows DMS plugin.

Reads the account Chatterino is logged in with, so no separate Twitch app or
token is needed. The token only ever leaves this process as a Helix header.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import http.client
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
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import stream_quality

HELIX_URL = "https://api.twitch.tv/helix"
REQUEST_TIMEOUT_SECONDS = 10
# Avatars are kept on disk so the list shows them at once; the periodic list
# refresh downloads them again once they are this old, and drops unused ones.
AVATAR_MAX_AGE_SECONDS = 7 * 24 * 3600
AVATAR_TIMEOUT_SECONDS = 5
# All downloads together, well within the 30 seconds the list may take.
AVATAR_DEADLINE_SECONDS = 10
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
REWIND_BUFFER = "2300MiB"
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


def avatar_dir() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / "twitch-follows" / "avatars"


def cached_avatar(url: str, directory: Path, now: float) -> str:
    """A local copy of the avatar, downloaded again once it's a week old;
    the old copy, or the URL itself, if downloading fails."""
    if not url.startswith("https://"):
        return url
    suffix = Path(urllib.parse.urlparse(url).path).suffix[:5]
    path = directory / (hashlib.sha256(url.encode()).hexdigest()[:32] + suffix)
    with contextlib.suppress(OSError):
        if now - path.stat().st_mtime < AVATAR_MAX_AGE_SECONDS:
            return path.as_uri()
    try:
        with urllib.request.urlopen(url, timeout=AVATAR_TIMEOUT_SECONDS) as reply:
            data = reply.read()
        directory.mkdir(parents=True, exist_ok=True)
        # Renamed into place, so the list never loads a half-written file.
        temporary = path.with_name(f"{path.name}.{os.getpid()}.new")
        temporary.write_bytes(data)
        os.replace(temporary, path)
    except (OSError, http.client.HTTPException):  # timeouts are OSErrors
        return path.as_uri() if path.exists() else url
    return path.as_uri()


def cache_avatars(urls: dict[str, str]) -> dict[str, str]:
    directory = avatar_dir()
    now = time.time()
    # Each URL once: channels without their own picture share Twitch's default.
    unique = list(dict.fromkeys(urls.values()))
    pool = ThreadPoolExecutor(max_workers=8)
    futures = {url: pool.submit(cached_avatar, url, directory, now) for url in unique}
    done, _ = wait(futures.values(), timeout=AVATAR_DEADLINE_SECONDS)
    # Avatars still downloading load from Twitch this time.
    local = {url: f.result() if f in done else url for url, f in futures.items()}
    pool.shutdown(wait=False, cancel_futures=True)
    used = {Path(urllib.parse.urlparse(uri).path).name for uri in local.values()}
    with contextlib.suppress(OSError):
        for path in directory.iterdir():
            if (
                path.name not in used
                and now - path.stat().st_mtime > AVATAR_MAX_AGE_SECONDS
            ):
                path.unlink(missing_ok=True)
    return {user: local[url] for user, url in urls.items()}


def fetch_follows(account: dict[str, str]) -> dict[str, Any]:
    streams = helix_all(account, "streams/followed", {"user_id": account["userID"]})
    avatars = cache_avatars(
        fetch_avatars(account, [stream["user_id"] for stream in streams])
    )
    live = [
        {
            "login": stream["user_login"],
            "name": stream.get("user_name") or stream["user_login"],
            "avatar": avatars.get(stream["user_id"], ""),
            "title": stream.get("title", ""),
            "game": stream.get("game_name", ""),
        }
        for stream in sorted(streams, key=lambda s: -s.get("viewer_count", 0))
    ]
    return {"ok": True, "user": account.get("username", ""), "live": live}


# chat_overlay.VISIBLE_PROPERTY: "yes" while chat shows in mpv.
CHAT_VISIBLE_PROPERTY = "user-data/twitch-chat-visible"
# A group's Chatterino window waits here while its chat shows in the streams.
CHAT_WORKSPACE = "special:twitch-chat"
# Takes the chat window out of the group's layout (a hyprweave weave or a tab
# group) and hides it.
HIDE_CHAT_LUA = """
local sel = "address:CHAT"
if not hl.get_window(sel) then return "missing" end
local weave = hl.plugin.hyprweave
if weave and weave.get({ window = sel }) then weave.dissolve({ window = sel }) end
local w = hl.get_window(sel)
if w.group then w.group:remove(w) end
hl.dispatch(hl.dsp.window.move({ workspace = "WORKSPACE", window = sel, follow = false }))
return "ok"
"""
SHOW_CHAT_LUA = """
hl.dispatch(hl.dsp.window.move({ workspace = "WORKSPACE", window = "address:CHAT", follow = false }))
return "ok"
"""


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
    chat = group["chat"]
    if chat not in present or group.get("chatInMpv"):
        chat = ""
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
    login: str, viewer: subprocess.Popen, group_id: str, chat_in_mpv: bool
) -> tuple[dict[str, Any], subprocess.Popen | None]:
    """Adds the stream to a viewing group and opens its chat there.

    Without group_id, or when that group has closed, the stream starts a new
    group. A native Chatterino (the chatterino-slim fork) shares one window
    per group: the first stream starts it as a named instance, and later
    streams hand it their channel as another tab. The Flatpak can't, so it
    opens one window per stream and each stream gets its own group.

    A new group starts with its chat where chat_in_mpv says; a stream that
    joins takes the group's (see ChatSync).

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
                "chatInMpv": chat_in_mpv,
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
            find_windows(hyprctl, viewer, chat, group, stream, before)
            save_groups(groups)
            if group.get("chatInMpv"):
                hide_chat(hyprctl, group["chat"])
            arrange(hyprctl, group, focus=stream["window"])
    return group, chat


def find_windows(
    hyprctl: str,
    viewer: subprocess.Popen,
    chat: subprocess.Popen | None,
    group: dict[str, Any],
    stream: dict[str, Any],
    before: set[str],
) -> None:
    """Records the stream's window and, when one appears, a new chat window
    (any Chatterino window that was not open before the launch)."""
    deadline = time.monotonic() + CHAT_WINDOW_WAIT_SECONDS
    while viewer.poll() is None and time.monotonic() <= deadline:
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
            return
        time.sleep(0.1)


def hide_chat(hyprctl: str, chat: str) -> None:
    if ADDRESS_PATTERN.match(chat):
        lua = HIDE_CHAT_LUA.replace("CHAT", chat).replace("WORKSPACE", CHAT_WORKSPACE)
        subprocess.run([hyprctl, "repl", lua], capture_output=True, check=False)


def show_chat(hyprctl: str, group: dict[str, Any]) -> str:
    """Brings the chat window back to the group's first stream's workspace;
    returns that stream's window."""
    clients = {c.get("address"): c for c in hypr_clients(hyprctl)}
    stream = next((s["window"] for s in group["streams"] if s["window"] in clients), "")
    if not stream or not ADDRESS_PATTERN.match(group["chat"]):
        return ""
    workspace = clients[stream].get("workspace", {})
    name = str(workspace.get("name", ""))
    if workspace.get("id", 0) > 0:
        target = str(workspace["id"])
    elif name.startswith("special:"):
        target = name
    else:
        target = f"name:{name}"
    lua = SHOW_CHAT_LUA.replace("CHAT", group["chat"]).replace("WORKSPACE", target)
    subprocess.run([hyprctl, "repl", lua], capture_output=True, check=False)
    return stream


def set_chat_mode(group_id: str, in_mpv: bool) -> None:
    """Moves a group's chat into its streams' mpv windows, hiding the
    Chatterino window, or back into Chatterino, and lays the group out again.
    The other streams' ChatSync then follow the group."""
    hyprctl = shutil.which("hyprctl")
    with file_lock(GROUPS_LOCK, blocking=True):
        groups = load_groups()
        group = next((g for g in groups if g["id"] == group_id), None)
        if group is None or group.get("chatInMpv") == in_mpv:
            return
        group["chatInMpv"] = in_mpv
        save_groups(groups)
        if not hyprctl or not group["chat"]:
            return
        if in_mpv:
            hide_chat(hyprctl, group["chat"])
            arrange(hyprctl, group)
        else:
            # Focusing a stream regroups the tabs when hyprweave isn't there.
            arrange(hyprctl, group, focus=show_chat(hyprctl, group))


class ChatSync:
    """Keeps a stream's mpv chat in step with its group. A group's chat shows
    either in every stream's mpv, with the Chatterino window hidden, or in
    Chatterino, with mpv's chat hidden. The chat button (or `c`) in any of the
    group's streams switches the whole group: that stream sets the group's
    mode, and the other streams follow it."""

    def __init__(self, ipc_path: Path, group_id: str) -> None:
        self.ipc_path = ipc_path
        self.group_id = group_id
        self.mpv = None
        # What this mpv shows, the group mode it last agreed with, and a
        # value set here that mpv hasn't reported back yet.
        self.shown: bool | None = None
        self.synced: bool | None = None
        self.expected: bool | None = None

    def step(self, group: dict[str, Any] | None) -> None:
        if group is None:
            return
        if self.mpv is None:
            self.connect()
            return
        try:
            events = self.mpv.events(0.01)
        except (EOFError, OSError):
            self.close()
            return
        for event in events:
            value = event.get("data")
            if (
                event.get("event") == "property-change"
                and event.get("name") == CHAT_VISIBLE_PROPERTY
                and value in ("yes", "no")
                and self.expected in (None, value == "yes")
            ):
                self.shown, self.expected = value == "yes", None
        if self.shown is None or self.expected is not None:
            return
        mode = bool(group.get("chatInMpv"))
        if mode != self.synced:
            # Joined just now, or another stream switched the group.
            self.synced = mode
            if self.shown != mode:
                self.expected = mode
                value = "yes" if mode else "no"
                self.mpv.send(["set_property", CHAT_VISIBLE_PROPERTY, value])
        elif self.shown != mode:
            # This stream's chat button.
            self.synced = self.shown
            set_chat_mode(self.group_id, self.shown)

    def connect(self) -> None:
        # Imported here: chat_overlay pulls in Pillow, which listing channels
        # doesn't need.
        from chat_overlay import Mpv

        try:
            self.mpv = Mpv(str(self.ipc_path))
        except OSError:
            return
        self.mpv.send(["observe_property", 1, CHAT_VISIBLE_PROPERTY])

    def close(self) -> None:
        if self.mpv:
            self.mpv.sock.close()
            self.mpv = None


def chat_closed(group: dict[str, Any] | None) -> bool:
    """True once the group's Chatterino is gone while its streams still play,
    which means the user closed the chat."""
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
    chat_in_mpv: bool = False,
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
            chat_in_mpv,
            volume,
            media_title or login,
            group_id,
            muted,
        )


def wait_while_playing(
    viewer: subprocess.Popen,
    relay: stream_quality.Relay,
    switcher: stream_quality.Switcher,
    chat_sync: ChatSync,
    group: dict[str, Any],
    chat: subprocess.Popen | None,
) -> bool:
    """Waits until mpv exits or the stream ends, switching quality and keeping
    the chat in step with the group meanwhile. The group's streams close with
    its chat window: returns True when that closed mpv."""
    while viewer.poll() is None and not relay.finished.is_set():
        if chat:
            chat.poll()  # reaps it, so a closed chat isn't a zombie
        current = next((g for g in load_groups() if g["id"] == group["id"]), None)
        if chat_closed(current):
            viewer.terminate()
            viewer.wait()
            return True
        chat_sync.step(current)
        switcher.step(0.2)
    return False


def play(
    streamlink: str,
    mpv: str,
    login: str,
    quality: str,
    chat_in_mpv: bool,
    volume: int,
    media_title: str,
    group_id: str,
    muted: bool,
) -> int:
    # mpv starts right away and the relay feeds it what streamlink writes, so
    # the window (with chat and a loading message) appears while streamlink is
    # still talking to Twitch or skipping a pre-roll ad. A muted stream keeps
    # its volume, so unmuting brings it back at that level.
    player = [
        mpv,
        "--force-window=immediate",
        f"--volume={volume}",
        f"--mute={'yes' if muted else 'no'}",
        f"--force-media-title={media_title}",
        # Keep up to 30 minutes to rewind (about 10 Mbit/s at 1080p60), in
        # memory: on disk, mpv's limits only bound the index, not the file.
        "--cache=yes",
        f"--demuxer-max-back-bytes={REWIND_BUFFER}",
        f"--script={Path(__file__).with_name('twitch_buttons.lua')}",
    ]
    # Without a Chatterino, mpv is the only place for chat. The chat relay
    # always runs, because it also handles `End` (jump to live).
    chat_in_mpv = chat_in_mpv or chatterino_command() is None
    ipc_path = runtime_path(f"twitch-follows-{login}-{os.getpid()}.sock")
    player.append(f"--input-ipc-server={ipc_path}")
    # uv provides the Pillow the overlay declares inline, so emotes show even
    # where the system Python lacks it; plain Python falls back to text chat.
    uv = shutil.which("uv")
    overlay = subprocess.Popen(
        [
            *([uv, "run", "--script"] if uv else [sys.executable]),
            str(Path(__file__).with_name("chat_overlay.py")),
            login,
            str(ipc_path),
            *([] if chat_in_mpv else ["--hidden"]),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log_path = runtime_path(f"twitch-follows-{login}.log")
    selection = QUALITY_FALLBACKS.get(quality, f"{quality},best")
    joined = None
    switcher = None
    chat_sync = None
    try:
        first = stream_quality.Source.start(streamlink, login, selection, log_path)
        viewer = subprocess.Popen(
            [*player, "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        relay = stream_quality.Relay(viewer.stdin, first)
        relay.start()
        policy = stream_quality.QualityPolicy(
            selection.split(","), audio_only=quality == "audio_only"
        )
        switcher = stream_quality.Switcher(streamlink, login, ipc_path, policy, relay)
        joined = join_group(login, viewer, group_id, chat_in_mpv)
        chat_sync = ChatSync(ipc_path, joined[0]["id"])
        # Whichever ends first decides: mpv closed by the user is normal, the
        # stream ending first means it failed or ended. After a quality
        # switch, that is the streamlink switched to.
        closed_with_chat = wait_while_playing(
            viewer, relay, switcher, chat_sync, *joined
        )
        source = relay.current.process
        source_ended_first = relay.source_ended
        if source_ended_first:
            viewer.wait()  # the relay closed its input
        elif source.poll() is None:
            # Killed: streamlink ignores SIGTERM while stuck writing into a
            # pipe nobody reads any more.
            source.kill()
        source.wait()
        relay.thread.join(timeout=2)
        source.stdout.close()
        # Both can be gone by the same poll: mpv exits 2 when it got no
        # video, but 0 when the user quit it.
        stream_failed = (
            not closed_with_chat
            and source.returncode != 0
            and (source_ended_first or viewer.returncode != 0)
        )
        lines = relay.current.log().strip().splitlines()
    finally:
        if chat_sync:
            chat_sync.close()
        if switcher:
            switcher.close()
            if switcher.source.log_path != log_path:
                switcher.source.log_path.unlink(missing_ok=True)
        # The stream's chat tab closes with it, and the chat window with the
        # group's last stream.
        if joined:
            leave_group(login, *joined)
        overlay.terminate()
        overlay.wait()
        ipc_path.unlink(missing_ok=True)
    if stream_failed:
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
        "--chat-in-player",
        action="store_true",
        help="start a new --watch group with chat in mpv and Chatterino hidden",
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
        args.chat_in_player,
        volume,
        args.media_title[:200],
        group_id,
        args.muted,
    )


if __name__ == "__main__":
    raise SystemExit(main())
