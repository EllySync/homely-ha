"""Client for the Homely API (REST and WebSocket).

Read-only: the API has no endpoints for arming, disarming or locking.
This module must not import Home Assistant, so it can be tested with plain pytest.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
import time
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientSession, ClientTimeout
import socketio

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://sdk.iotiliti.cloud"
API_URL = f"{BASE_URL}/homely"

REQUEST_TIMEOUT = ClientTimeout(total=20)
CONNECT_TIMEOUT = 20

# Renew the access token when less than this is left (or half its lifetime if shorter).
RENEW_MARGIN = 300
# Treat the refresh token as expired a little early.
REFRESH_MARGIN = 30

RECONNECT_MIN = 5
RECONNECT_MAX = 300
# A connection that lasted this long resets the reconnect delay.
STABLE_AFTER = 60
# Never schedule a planned token reconnect sooner than this.
MIN_SESSION = 30


class HomelyError(Exception):
    """Base error for the Homely API."""


class HomelyAuthError(HomelyError):
    """The credentials were rejected."""


class HomelyRateLimitError(HomelyError):
    """The API answered 429 Too Many Requests."""


class HomelyConnectionError(HomelyError):
    """Network error, timeout, server error or an unexpected response."""


class HomelyClient:
    """REST client with automatic token renewal."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._clock = clock
        self._lock = asyncio.Lock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._renew_at = 0.0
        self._refresh_expires_at = 0.0

    async def login(self) -> None:
        """Log in with username and password."""
        async with self._lock:
            await self._login()

    async def access_token(self) -> str:
        """Return a valid access token, renewing it if needed."""
        async with self._lock:
            if self._access_token is not None and self._clock() < self._renew_at:
                return self._access_token
            if self._refresh_token is not None and self._clock() < self._refresh_expires_at:
                try:
                    self._store(await self._post_token(
                        "/oauth/refresh-token", {"refresh_token": self._refresh_token}
                    ))
                except HomelyAuthError:
                    _LOGGER.debug("Token refresh rejected, logging in again")
                else:
                    return self._access_token  # type: ignore[return-value]
            await self._login()
            return self._access_token  # type: ignore[return-value]

    def seconds_until_renewal(self) -> float:
        """Seconds until the current access token is due for renewal."""
        if self._access_token is None:
            return 0.0
        return max(0.0, self._renew_at - self._clock())

    async def get_locations(self) -> list[dict[str, Any]]:
        """Return the homes the user has access to."""
        data = await self._get("/locations")
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise HomelyConnectionError("Unexpected response from /locations")
        return data

    async def get_home(self, location_id: str) -> dict[str, Any]:
        """Return alarm state and all devices for a home."""
        data = await self._get(f"/home/{quote(location_id, safe='')}")
        if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
            raise HomelyConnectionError("Unexpected response from /home")
        return data

    async def _login(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._store(await self._post_token(
            "/oauth/token", {"username": self._username, "password": self._password}
        ))

    def _store(self, token: dict[str, Any]) -> None:
        access = token.get("access_token")
        expires_in = token.get("expires_in")
        if not isinstance(access, str) or not access or not isinstance(expires_in, (int, float)):
            raise HomelyConnectionError("Unexpected token response")
        now = self._clock()
        self._access_token = access
        self._renew_at = now + expires_in - min(RENEW_MARGIN, expires_in / 2)
        refresh = token.get("refresh_token")
        refresh_expires_in = token.get("refresh_expires_in")
        if isinstance(refresh, str) and refresh and isinstance(refresh_expires_in, (int, float)):
            self._refresh_token = refresh
            self._refresh_expires_at = now + refresh_expires_in - REFRESH_MARGIN
        else:
            self._refresh_token = None

    async def _post_token(self, path: str, payload: dict[str, str]) -> dict[str, Any]:
        try:
            async with self._session.post(
                API_URL + path,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status in (400, 401):
                    raise HomelyAuthError(f"Homely rejected the login (HTTP {response.status})")
                _raise_for_status(response.status)
                data = await response.json(content_type=None)
        except (ClientError, TimeoutError, ValueError):
            raise HomelyConnectionError("Could not reach Homely or read the login response") from None
        if not isinstance(data, dict):
            raise HomelyConnectionError("Unexpected token response")
        return data

    async def _get(self, path: str) -> Any:
        for attempt in (1, 2):
            token = await self.access_token()
            try:
                async with self._session.get(
                    API_URL + path,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                ) as response:
                    if response.status == 401 and attempt == 1:
                        await self._invalidate()
                        continue
                    if response.status == 401:
                        raise HomelyAuthError("Homely rejected the access token")
                    _raise_for_status(response.status)
                    return await response.json(content_type=None)
            except (ClientError, TimeoutError, ValueError):
                raise HomelyConnectionError(f"Could not reach Homely or read {path}") from None
        raise HomelyConnectionError("Unreachable")  # pragma: no cover

    async def _invalidate(self) -> None:
        async with self._lock:
            self._access_token = None
            self._refresh_token = None


def _raise_for_status(status: int) -> None:
    # The token endpoints answer 201 Created.
    if 200 <= status < 300:
        return
    if status == 429:
        raise HomelyRateLimitError("Homely answered 429 Too Many Requests")
    if status == 403:
        raise HomelyError("No access to this Homely location (HTTP 403)")
    raise HomelyConnectionError(f"Homely answered HTTP {status}")


# --- WebSocket events --------------------------------------------------------


@dataclass(frozen=True)
class DeviceChange:
    """One changed state on a device."""

    device_id: str
    feature: str
    state: str
    value: Any
    last_updated: str | None


@dataclass(frozen=True)
class AlarmChange:
    """The alarm state changed."""

    state: str
    user_name: str | None
    timestamp: str | None


HomelyEvent = list[DeviceChange] | AlarmChange


def parse_event(message: Any, location_id: str) -> HomelyEvent | None:
    """Turn a message from the `event` channel into a typed event.

    Returns None for unknown types, malformed messages and other homes.
    """
    if not isinstance(message, dict):
        return None
    event_type = message.get("type")
    data = message.get("data", message.get("payload"))
    if not isinstance(data, dict):
        return None

    if event_type == "device-state-changed":
        # locationId is the room here; rootLocationId is the home.
        if data.get("rootLocationId", location_id) != location_id:
            return None
        device_id = data.get("deviceId")
        if not isinstance(device_id, str):
            return None
        changes = data.get("changes")
        if not isinstance(changes, list):
            change = data.get("change")
            changes = [change] if isinstance(change, dict) else []
        result = [
            DeviceChange(
                device_id=device_id,
                feature=change["feature"],
                state=change["stateName"],
                value=change.get("value"),
                last_updated=_str_or_none(change.get("lastUpdated")),
            )
            for change in changes
            if isinstance(change, dict)
            and isinstance(change.get("feature"), str)
            and isinstance(change.get("stateName"), str)
        ]
        return result or None

    if event_type == "alarm-state-changed":
        if data.get("locationId", location_id) != location_id:
            return None
        state = data.get("state")
        if not isinstance(state, str):
            return None
        return AlarmChange(
            state=state,
            user_name=_str_or_none(data.get("userName")),
            timestamp=_str_or_none(data.get("timestamp")),
        )

    _LOGGER.debug("Ignoring Homely event of type %r", event_type)
    return None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# --- WebSocket connection ----------------------------------------------------


class HomelyWebSocket:
    """Keeps a Socket.IO connection open and never gives up.

    Reconnects with growing delay (5 s to 5 min) after errors, and reconnects
    with a fresh token before the current one is due for renewal.
    """

    def __init__(
        self,
        client: HomelyClient,
        location_id: str,
        on_event: Callable[[HomelyEvent], None],
        on_connected: Callable[[], Awaitable[None]],
        *,
        http_session: ClientSession | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        sio_factory: Callable[[], socketio.AsyncClient] | None = None,
    ) -> None:
        self._client = client
        self._location_id = location_id
        self._on_event = on_event
        self._on_connected = on_connected
        self._http_session = http_session
        self._sleep = sleep
        self._clock = clock
        self._sio_factory = sio_factory or self._default_sio
        self._sio: socketio.AsyncClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the WebSocket is connected."""
        return self._connected

    async def start(self) -> None:
        """Start the connection loop in the background."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="homely_orion_websocket")

    async def stop(self) -> None:
        """Stop the loop and close the connection."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close()

    def _default_sio(self) -> socketio.AsyncClient:
        kwargs: dict[str, Any] = {"reconnection": False, "logger": False, "engineio_logger": False}
        if self._http_session is not None:
            kwargs["http_session"] = self._http_session
        return socketio.AsyncClient(**kwargs)

    async def _run(self) -> None:
        delay = RECONNECT_MIN
        try:
            while True:
                token: str | None = None
                try:
                    token = await self._client.access_token()
                    await self._connect(token)
                except HomelyError as err:
                    _LOGGER.warning("Homely WebSocket: %s, retrying in %s s", err, delay)
                    await self._close()
                    await self._sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX)
                    continue
                except Exception as err:  # noqa: BLE001 - socketio raises many types
                    _LOGGER.warning(
                        "Homely WebSocket could not connect (%s), retrying in %s s",
                        _scrub(err, token), delay,
                    )
                    await self._close()
                    await self._sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX)
                    continue

                started = self._clock()
                _LOGGER.debug("Homely WebSocket connected")
                try:
                    await self._on_connected()
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Error in Homely on_connected callback")

                dropped = await self._wait_session()
                await self._close()
                if not dropped:
                    _LOGGER.debug("Homely WebSocket reconnecting with a new token")
                    continue
                if self._clock() - started >= STABLE_AFTER:
                    delay = RECONNECT_MIN
                _LOGGER.info("Homely WebSocket disconnected, reconnecting in %s s", delay)
                await self._sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)
        finally:
            await self._close()

    async def _connect(self, token: str) -> None:
        bearer = f"Bearer {token}"
        url = (
            f"{BASE_URL}?locationId={quote(self._location_id, safe='')}"
            f"&token={quote(bearer, safe='')}"
        )
        self._wake.clear()
        sio = self._sio_factory()
        sio.on("event", self._handle_event)
        sio.on("disconnect", self._handle_disconnect)
        self._sio = sio
        await sio.connect(
            url,
            headers={"Authorization": bearer, "locationId": self._location_id},
            wait_timeout=CONNECT_TIMEOUT,
        )
        self._connected = True

    async def _wait_session(self) -> bool:
        """Wait until the connection drops (True) or the token is due (False)."""
        timeout = max(self._client.seconds_until_renewal(), MIN_SESSION)
        try:
            async with asyncio.timeout(timeout):
                await self._wake.wait()
        except TimeoutError:
            return False
        return True

    async def _close(self) -> None:
        self._connected = False
        sio, self._sio = self._sio, None
        if sio is not None:
            try:
                await sio.disconnect()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Error while closing the Homely WebSocket")

    async def _handle_disconnect(self, *_: Any) -> None:
        self._connected = False
        self._wake.set()

    async def _handle_event(self, message: Any) -> None:
        event = parse_event(message, self._location_id)
        if event is None:
            return
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error handling Homely event")


def _scrub(err: BaseException, token: str | None) -> str:
    """Describe an error without leaking the token."""
    text = f"{type(err).__name__}: {err}"
    if token:
        text = text.replace(token, "***").replace(quote(token, safe=""), "***")
    return text
