"""The plugin's own Twitch login, used only to follow raids.

Following raids needs Twitch EventSub over a WebSocket, which Twitch limits to
3 connections per app and account. Chatterino already uses one per open
window, so the raid watcher logs in with an app of its own: a public client
registered by the user, through Twitch's device code flow (no client secret).

The token file lives in $XDG_STATE_HOME/twitch-follows and is only readable by
the user. Tokens only ever leave this process in HTTPS headers and form bodies.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

ID_URL = "https://id.twitch.tv/oauth2"
REQUEST_TIMEOUT_SECONDS = 10
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{10,64}$")
# Refreshed this long before Twitch says the access token expires.
REFRESH_MARGIN_SECONDS = 300
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class LoginError(Exception):
    """The login is gone or was refused: the user has to log in again."""


class TransientError(Exception):
    """Twitch could not be reached or answered with a server error."""


def state_dir() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(state) / "twitch-follows"


def token_path() -> Path:
    return state_dir() / "raid-token.json"


def has_token() -> bool:
    return token_path().is_file()


@contextlib.contextmanager
def token_lock() -> Iterator[None]:
    """One reader or writer at a time: refresh tokens work only once, so two
    processes refreshing together would log each other out."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(directory / "raid-token.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_token() -> dict[str, Any] | None:
    try:
        token = json.loads(token_path().read_text())
    except (OSError, ValueError):
        return None
    keys = ("client_id", "user_id", "access_token", "refresh_token")
    if not isinstance(token, dict) or not all(token.get(k) for k in keys):
        return None
    return token


def write_token(token: dict[str, Any]) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.new")
    # Created private, so the token is never readable by others, even briefly.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(token, handle)
    os.replace(temporary, path)


def post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    """POSTs a form; returns the JSON reply. HTTP errors carry their status in
    `status` and Twitch's message in `message`."""
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(), method="POST"
    )
    return send(request)


def send(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as reply:
            body = reply.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        if error.code >= 500 or error.code == 429:
            raise TransientError(f"Twitch returned HTTP {error.code}.") from None
        try:
            reply = json.loads(error.read())
        except (OSError, ValueError):
            reply = {}
        if not isinstance(reply, dict):
            reply = {}
        return {**reply, "status": error.code}
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise TransientError(f"Could not reach Twitch: {error}") from None


def refresh(token: dict[str, Any]) -> dict[str, Any]:
    reply = post_form(
        f"{ID_URL}/token",
        {
            "client_id": token["client_id"],
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
    )
    if reply.get("status") or not reply.get("access_token"):
        raise LoginError(str(reply.get("message") or "Twitch refused the login."))
    return {
        **token,
        "access_token": reply["access_token"],
        "refresh_token": reply.get("refresh_token") or token["refresh_token"],
        "expires_at": time.time() + float(reply.get("expires_in") or 0),
    }


def current_token(force_refresh: bool = False) -> dict[str, Any]:
    """The stored login, refreshed first when it is about to expire (or when
    Twitch has just turned it down). A login Twitch refuses is deleted, so the
    user is told once and the watcher stops trying."""
    with token_lock():
        token = read_token()
        if token is None:
            raise LoginError("Not logged in.")
        expires_at = float(token.get("expires_at") or 0)
        if not force_refresh and time.time() < expires_at - REFRESH_MARGIN_SECONDS:
            return token
        try:
            token = refresh(token)
        except LoginError:
            token_path().unlink(missing_ok=True)
            raise
        write_token(token)
        return token


def validate(access_token: str) -> dict[str, Any]:
    """Twitch's token check: who the token belongs to. Twitch asks apps to run
    it at least once an hour."""
    request = urllib.request.Request(
        f"{ID_URL}/validate", headers={"Authorization": f"OAuth {access_token}"}
    )
    return send(request)


def device_login(
    client_id: str,
    show_code: Callable[[str, str], None],
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Logs in through Twitch's device code flow: show_code(user_code, url)
    tells the user where to confirm, then this waits until they have."""
    # channel.raid needs no permissions, so nothing is asked for.
    reply = post_form(f"{ID_URL}/device", {"client_id": client_id, "scopes": ""})
    if reply.get("status") or not reply.get("device_code"):
        raise LoginError(str(reply.get("message") or "Twitch refused the client ID."))
    show_code(reply["user_code"], reply["verification_uri"])
    interval = max(1.0, float(reply.get("interval") or 5))
    deadline = time.monotonic() + float(reply.get("expires_in") or 1800)
    while time.monotonic() < deadline:
        sleep(interval)
        try:
            token = post_form(
                f"{ID_URL}/token",
                {
                    "client_id": client_id,
                    "scopes": "",
                    "device_code": reply["device_code"],
                    "grant_type": DEVICE_GRANT,
                },
            )
        except TransientError:
            continue
        message = str(token.get("message", ""))
        if token.get("status"):
            if "pending" in message:
                continue
            if "slow_down" in message:
                interval += 5
                continue
            raise LoginError(message or "Twitch refused the login.")
        user = validate(token["access_token"])
        if user.get("status") or not user.get("user_id"):
            raise LoginError("Twitch did not accept the new login.")
        stored = {
            "client_id": client_id,
            "user_id": user["user_id"],
            "login": user.get("login", ""),
            "access_token": token["access_token"],
            "refresh_token": token["refresh_token"],
            "expires_at": time.time() + float(token.get("expires_in") or 0),
        }
        with token_lock():
            write_token(stored)
        return stored
    raise LoginError("The login code expired before it was confirmed.")
