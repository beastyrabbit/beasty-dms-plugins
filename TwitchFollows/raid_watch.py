# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=14"]
# ///
"""Tells the Twitch Follows streams when their channel raids another.

One watcher runs per desktop, whatever number of streams play: Twitch allows
an app and account only 3 EventSub WebSocket connections and 10 watched
channels at once (a raid subscription for someone else's channel costs 1).
The streams start it as needed; a second copy exits at once.

It subscribes to `channel.raid` for every playing stream that follows raids
(the viewing groups file says which), and writes each raid to a runtime file
the streams read. It logs in with the plugin's own Twitch app (raid_login),
and exits half a minute after the last such stream closed.
"""

from __future__ import annotations

import collections
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

import raid_login
import twitch_follows

EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
HELIX_URL = "https://api.twitch.tv/helix"
RAIDS_LOCK = "twitch-follows-raids.lock"
LOG_FILE = "twitch-follows-raids.log"
MAX_LOG_BYTES = 256 << 10
IDLE_EXIT_SECONDS = 30
RECONCILE_SECONDS = 2
# Twitch closes a connection that has no subscription 10 s after its welcome.
WELCOME_TIMEOUT_SECONDS = 10
# How long after the expected keepalive the connection counts as lost.
KEEPALIVE_SLACK_SECONDS = 5
MAX_RECONNECT_WAIT_SECONDS = 60
# A channel Twitch wouldn't subscribe to (as when 10 are watched already) is
# tried again after this, doubling up to the maximum.
RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 600
VALIDATE_SECONDS = 3600


def log(message: str) -> None:
    path = twitch_follows.runtime_path(LOG_FILE)
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            path.unlink()
    except OSError:
        pass
    with open(path, "a") as handle:
        handle.write(f"{time.strftime('%H:%M:%S')} {message}\n")


def wanted_logins() -> set[str]:
    return {
        stream["login"]
        for group in twitch_follows.load_groups()
        for stream in group["streams"]
        if stream.get("followRaids")
    }


def parse_timestamp(stamp: str) -> float:
    """Twitch's RFC 3339 time, with nanoseconds, as seconds since the epoch."""
    stamp = re.sub(r"(\.\d{6})\d+", r"\1", stamp).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return time.time()


def record_raid(event: dict[str, Any], message_id: str, at: float) -> None:
    """Adds a raid to the runtime raids file, dropping ones the streams have
    had long enough to see."""
    raids = twitch_follows.read_raids()
    now = time.time()
    raids = {
        login: raid
        for login, raid in raids.items()
        if now - raid.get("at", 0) < twitch_follows.RAID_TTL_SECONDS
    }
    raids[event["from_broadcaster_user_login"]] = {
        "to": event["to_broadcaster_user_login"],
        "name": event.get("to_broadcaster_user_name")
        or event["to_broadcaster_user_login"],
        "id": message_id,
        "at": at,
    }
    path = twitch_follows.runtime_path(twitch_follows.RAIDS_FILE)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(raids))
    temporary.replace(path)


class Helix:
    """Helix requests with the raid login, refreshed once when Twitch turns
    the token down."""

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = raid_login.current_token()
        reply = self.send(token, method, endpoint, params, body)
        if reply.get("status") == 401:
            token = raid_login.current_token(force_refresh=True)
            reply = self.send(token, method, endpoint, params, body)
        return reply

    def send(self, token, method, endpoint, params, body) -> dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params, doseq=True)}" if params else ""
        request = urllib.request.Request(
            f"{HELIX_URL}/{endpoint}{query}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {token['access_token']}",
                "Client-Id": token["client_id"],
                "Content-Type": "application/json",
            },
        )
        return raid_login.send(request)

    def user_ids(self, logins: list[str]) -> dict[str, str]:
        reply = self.request("GET", "users", {"login": logins[:100]})
        return {user["login"]: user["id"] for user in reply.get("data", [])}

    def subscribe(self, broadcaster_id: str, session_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "eventsub/subscriptions",
            body={
                "type": "channel.raid",
                "version": "1",
                "condition": {"from_broadcaster_user_id": broadcaster_id},
                "transport": {"method": "websocket", "session_id": session_id},
            },
        )

    def unsubscribe(self, subscription_id: str) -> None:
        self.request("DELETE", "eventsub/subscriptions", {"id": subscription_id})

    def validate(self) -> None:
        token = raid_login.current_token()
        if raid_login.validate(token["access_token"]).get("status") == 401:
            raid_login.current_token(force_refresh=True)


class Watcher:
    """Receives on the WebSocket in run(); subscribes and unsubscribes on its
    own thread, so a slow Helix request never holds up an event."""

    def __init__(self, helix: Helix, connect=None, url: str = EVENTSUB_URL) -> None:
        if connect is None:
            from websockets.sync.client import connect
        self.helix = helix
        self.connect = connect
        self.url = url
        self.lock = threading.Lock()
        self.session_id = ""
        self.keepalive = 10.0
        # Broadcaster id -> subscription id, for the current session.
        self.subscriptions: dict[str, str] = {}
        self.ids: dict[str, str] = {}
        self.retry: dict[str, tuple[float, float]] = {}
        self.seen: collections.deque[str] = collections.deque(maxlen=200)
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.idle_since: float | None = None
        self.next_validate = 0.0

    def run(self) -> None:
        reconciler = threading.Thread(target=self.reconcile_loop, daemon=True)
        reconciler.start()
        wait = 1.0
        while not self.stopping.is_set():
            # Twitch closes a connection without subscriptions after 10 s,
            # so there is none while no stream follows raids.
            if not wanted_logins():
                self.stopping.wait(RECONCILE_SECONDS)
                continue
            try:
                ws = self.open(self.url, keep_subscriptions=False)
            except Exception as error:  # noqa: BLE001 - any failure: try again
                log(f"connecting failed: {error!r}")
                self.stopping.wait(wait)
                wait = min(wait * 2, MAX_RECONNECT_WAIT_SECONDS)
                continue
            wait = 1.0
            self.receive(ws)
        reconciler.join(timeout=15)

    def open(self, url: str, keep_subscriptions: bool):
        # Twitch closes a client that sends anything but Pong, so no pings.
        # Entered by hand rather than in a with block: on a reconnect the old
        # connection outlives the new one's start. close() still ends it.
        ws = self.connect(
            url, ping_interval=None, open_timeout=10, close_timeout=2
        ).__enter__()
        try:
            welcome = json.loads(ws.recv(timeout=WELCOME_TIMEOUT_SECONDS))
            if welcome["metadata"]["message_type"] != "session_welcome":
                raise ValueError("no welcome")
            session = welcome["payload"]["session"]
        except Exception:
            ws.close()
            raise
        with self.lock:
            self.session_id = session["id"]
            self.keepalive = float(session.get("keepalive_timeout_seconds") or 10)
            if not keep_subscriptions:
                # A new connection starts without subscriptions.
                self.subscriptions.clear()
        self.wake.set()
        return ws

    def receive(self, ws) -> None:
        last = time.monotonic()
        try:
            while not self.stopping.is_set():
                try:
                    raw = ws.recv(timeout=1)
                except TimeoutError:
                    if (
                        time.monotonic() - last
                        > self.keepalive + KEEPALIVE_SLACK_SECONDS
                    ):
                        log("keepalive missed; reconnecting")
                        return
                    continue
                last = time.monotonic()
                message = json.loads(raw)
                if message["metadata"]["message_type"] == "session_reconnect":
                    url = message["payload"]["session"]["reconnect_url"]
                    # Subscriptions move along; the old connection closes once
                    # the new one has its welcome.
                    new = self.open(url, keep_subscriptions=True)
                    ws.close()
                    ws = new
                    continue
                self.handle(message)
        except Exception as error:  # noqa: BLE001 - connection lost
            log(f"connection lost: {error!r}")
        finally:
            ws.close()
            with self.lock:
                self.session_id = ""

    def handle(self, message: dict[str, Any]) -> None:
        metadata = message.get("metadata", {})
        kind = metadata.get("message_type")
        payload = message.get("payload", {})
        if kind == "notification":
            if payload.get("subscription", {}).get("type") != "channel.raid":
                return
            # Twitch may send a notification twice.
            if metadata.get("message_id") in self.seen:
                return
            self.seen.append(metadata.get("message_id"))
            event = payload["event"]
            log(
                f"raid: {event['from_broadcaster_user_login']} -> "
                f"{event['to_broadcaster_user_login']}"
            )
            record_raid(
                event,
                metadata.get("message_id", ""),
                parse_timestamp(metadata.get("message_timestamp", "")),
            )
        elif kind == "revocation":
            revoked = payload.get("subscription", {}).get("id")
            with self.lock:
                for broadcaster, subscription in list(self.subscriptions.items()):
                    if subscription == revoked:
                        del self.subscriptions[broadcaster]
            log(
                f"subscription revoked: {payload.get('subscription', {}).get('status')}"
            )

    def reconcile_loop(self) -> None:
        while not self.stopping.is_set():
            self.wake.wait(RECONCILE_SECONDS)
            self.wake.clear()
            try:
                self.reconcile(time.monotonic())
            except raid_login.LoginError as error:
                log(f"login refused: {error}")
                twitch_follows.notify(
                    "Raid following stopped",
                    "Log in again in the Twitch Follows settings.",
                )
                self.stopping.set()
            except raid_login.TransientError as error:
                log(str(error))
            except Exception as error:  # noqa: BLE001 - keep watching
                log(f"reconcile failed: {error!r}")

    def reconcile(self, now: float) -> None:
        wanted = wanted_logins()
        if not wanted:
            if self.idle_since is None:
                self.idle_since = now
            elif now - self.idle_since >= IDLE_EXIT_SECONDS:
                self.stopping.set()
                return
        else:
            self.idle_since = None
        with self.lock:
            session = self.session_id
            subscriptions = dict(self.subscriptions)
        if not session:
            return
        if now >= self.next_validate:
            self.helix.validate()
            self.next_validate = now + VALIDATE_SECONDS
        unknown = sorted(wanted - self.ids.keys())
        if unknown:
            self.ids.update(self.helix.user_ids(unknown))
        wanted_ids = {self.ids[login] for login in wanted if login in self.ids}
        # Released first, so a new channel finds room among Twitch's 10.
        for broadcaster, subscription in subscriptions.items():
            if broadcaster not in wanted_ids:
                self.helix.unsubscribe(subscription)
                with self.lock:
                    self.subscriptions.pop(broadcaster, None)
        for broadcaster in wanted_ids - subscriptions.keys():
            retry_at, wait = self.retry.get(broadcaster, (0.0, RETRY_SECONDS / 2))
            if now < retry_at:
                continue
            reply = self.helix.subscribe(broadcaster, session)
            data = reply.get("data") or [{}]
            if reply.get("status") or not data[0].get("id"):
                wait = min(wait * 2, MAX_RETRY_SECONDS)
                self.retry[broadcaster] = (now + wait, wait)
                log(
                    f"subscribing to {broadcaster} failed: "
                    f"{reply.get('status')} {reply.get('message', '')}"
                )
                continue
            self.retry.pop(broadcaster, None)
            with self.lock:
                if self.session_id == session:
                    self.subscriptions[broadcaster] = data[0]["id"]


def main() -> int:
    lock = twitch_follows.try_lock(RAIDS_LOCK)
    if lock is None:
        return 0  # another watcher runs
    with lock:
        if not raid_login.has_token():
            return 0
        log("watcher started")
        Watcher(Helix()).run()
        log("watcher stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
