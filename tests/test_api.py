"""Tests for api.py: tokens, errors, event parsing and reconnect delays."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from api import (
    API_URL,
    AlarmChange,
    DeviceChange,
    HomelyAuthError,
    HomelyClient,
    HomelyConnectionError,
    HomelyError,
    HomelyRateLimitError,
    HomelyWebSocket,
    parse_event,
)

FIXTURES = Path(__file__).parent / "fixtures"
HOME_ID = "00000000-0000-0000-0000-000000000001"
PASSWORD = "hemmelig-passord"

TOKEN_URL = f"{API_URL}/oauth/token"
REFRESH_URL = f"{API_URL}/oauth/refresh-token"
LOCATIONS_URL = f"{API_URL}/locations"
HOME_URL = f"{API_URL}/home/{HOME_ID}"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def token(access: str = "access-1", expires_in: int = 1800, refresh: str = "refresh-1") -> dict:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": expires_in,
        "refresh_expires_in": 1800,
        "token_type": "bearer",
    }


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeResponse:
    def __init__(self, status: int, payload: Any = None, body: str | None = None) -> None:
        self.status = status
        self._payload = payload
        self._body = body

    async def json(self, content_type: str | None = "application/json") -> Any:
        if self._body is not None:
            return json.loads(self._body)
        return self._payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class Call:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs


class FakeSession:
    """Minimal stand-in for aiohttp.ClientSession with queued answers per URL."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], list[tuple[dict[str, Any], bool]]] = {}
        self.requests: dict[tuple[str, str], list[Call]] = {}

    def add(self, method, url, *, repeat=False, status=200, payload=None, body=None, exception=None):
        """Queue an answer for the next request to method + url."""
        answer = {"status": status, "payload": payload, "body": body, "exception": exception}
        self._routes.setdefault((method, url), []).append((answer, repeat))

    def post(self, url, **kwargs):
        return self._request("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._request("GET", url, kwargs)

    def _request(self, method, url, kwargs):
        self.requests.setdefault((method, url), []).append(Call(kwargs))
        queue = self._routes.get((method, url))
        if not queue:
            raise AssertionError(f"Unexpected request {method} {url}")
        answer, repeat = queue[0]
        if not repeat:
            queue.pop(0)
        if answer["exception"] is not None:
            raise answer["exception"]
        return FakeResponse(answer["status"], answer["payload"], answer["body"])


def calls(mock: FakeSession, method: str, url: str) -> list[Call]:
    return mock.requests.get((method, url), [])


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(session, clock) -> HomelyClient:
    return HomelyClient(session, "user@example.com", PASSWORD, clock=clock)


# --- REST and tokens ---------------------------------------------------------


async def test_login_and_get_locations(session, client):
    m = session
    m.add("POST", TOKEN_URL, payload=token())
    m.add("GET", LOCATIONS_URL, payload=load("locations.json"))
    locations = await client.get_locations()

    assert locations[0]["locationId"] == HOME_ID
    login = calls(m, "POST", TOKEN_URL)[0]
    assert login.kwargs["json"] == {"username": "user@example.com", "password": PASSWORD}
    get = calls(m, "GET", LOCATIONS_URL)[0]
    assert get.kwargs["headers"]["Authorization"] == "Bearer access-1"


async def test_login_accepts_201_created(session, client):
    session.add("POST", TOKEN_URL, status=201, payload=token())
    assert await client.access_token() == "access-1"


async def test_get_home_returns_devices(session, client):
    m = session
    m.add("POST", TOKEN_URL, payload=token())
    m.add("GET", HOME_URL, payload=load("home.json"))
    home = await client.get_home(HOME_ID)
    assert home["alarmState"] == "DISARMED"
    assert len(home["devices"]) == 9


async def test_token_is_reused(session, client):
    m = session
    m.add("POST", TOKEN_URL, payload=token())
    m.add("GET", HOME_URL, payload=load("home.json"), repeat=True)
    await client.get_home(HOME_ID)
    await client.get_home(HOME_ID)
    assert len(calls(m, "POST", TOKEN_URL)) == 1


@pytest.mark.parametrize(("expires_in", "renew_after"), [(1800, 1500), (60, 30)])
async def test_token_renewed_at_right_time(session, client, clock, expires_in, renew_after):
    m = session
    m.add("POST", TOKEN_URL, payload=token(expires_in=expires_in))
    m.add("POST", REFRESH_URL, payload=token("access-2", expires_in=expires_in))
    assert await client.access_token() == "access-1"
    assert client.seconds_until_renewal() == renew_after

    clock.now += renew_after - 1
    assert await client.access_token() == "access-1"

    clock.now += 2
    assert await client.access_token() == "access-2"
    refresh = calls(m, "POST", REFRESH_URL)[0]
    assert refresh.kwargs["json"] == {"refresh_token": "refresh-1"}


async def test_rejected_refresh_falls_back_to_login(session, client, clock):
    m = session
    m.add("POST", TOKEN_URL, payload=token())
    m.add("POST", REFRESH_URL, status=400, payload={"statusCode": 400, "message": "Invalid refresh token"})
    m.add("POST", TOKEN_URL, payload=token("access-2"))
    await client.access_token()
    clock.now += 1600
    assert await client.access_token() == "access-2"
    assert len(calls(m, "POST", TOKEN_URL)) == 2


async def test_expired_refresh_token_logs_in_directly(session, client, clock):
    m = session
    m.add("POST", TOKEN_URL, payload=token(expires_in=3600))
    m.add("POST", TOKEN_URL, payload=token("access-2"))
    await client.access_token()
    clock.now += 3500  # refresh token (1800 s) has expired
    assert await client.access_token() == "access-2"
    assert calls(m, "POST", REFRESH_URL) == []


async def test_wrong_password_raises_auth_error_without_password(session, client):
    m = session
    m.add("POST", TOKEN_URL, status=401, payload={"statusCode": 401, "message": "Invalid user credentials"})
    with pytest.raises(HomelyAuthError) as err:
        await client.login()
    assert PASSWORD not in str(err.value)


async def test_401_on_get_logs_in_again_and_retries(session, client):
    m = session
    m.add("POST", TOKEN_URL, payload=token())
    m.add("GET", HOME_URL, status=401)
    m.add("POST", TOKEN_URL, payload=token("access-2"))
    m.add("GET", HOME_URL, payload=load("home.json"))
    home = await client.get_home(HOME_ID)
    assert home["name"] == "Hjemme"
    headers = [c.kwargs["headers"]["Authorization"] for c in calls(m, "GET", HOME_URL)]
    assert headers == ["Bearer access-1", "Bearer access-2"]


async def test_repeated_401_raises_auth_error(session, client):
    m = session
    m.add("POST", TOKEN_URL, payload=token(), repeat=True)
    m.add("GET", HOME_URL, status=401, repeat=True)
    with pytest.raises(HomelyAuthError):
        await client.get_home(HOME_ID)


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, HomelyRateLimitError), (500, HomelyConnectionError), (403, HomelyError)],
)
async def test_http_errors(session, client, status, error):
    m = session
    m.add("POST", TOKEN_URL, payload=token())
    m.add("GET", HOME_URL, status=status)
    with pytest.raises(error):
        await client.get_home(HOME_ID)


async def test_network_error(session, client):
    m = session
    m.add("POST", TOKEN_URL, exception=aiohttp.ClientConnectionError("boom"))
    with pytest.raises(HomelyConnectionError):
        await client.get_home(HOME_ID)


@pytest.mark.parametrize(
    ("url", "payload", "method"),
    [
        (HOME_URL, [], "get_home"),
        (HOME_URL, {"name": "no devices"}, "get_home"),
        (LOCATIONS_URL, {"not": "a list"}, "get_locations"),
    ],
)
async def test_unexpected_response_shape(session, client, url, payload, method):
    m = session
    m.add("POST", TOKEN_URL, payload=token())
    m.add("GET", url, payload=payload)
    with pytest.raises(HomelyConnectionError):
        if method == "get_home":
            await client.get_home(HOME_ID)
        else:
            await client.get_locations()


async def test_bad_token_response(session, client):
    m = session
    m.add("POST", TOKEN_URL, payload={"token_type": "bearer"})
    with pytest.raises(HomelyConnectionError):
        await client.login()


async def test_invalid_json(session, client):
    m = session
    m.add("POST", TOKEN_URL, payload=token())
    m.add("GET", HOME_URL, body="<html>not json</html>")
    with pytest.raises(HomelyConnectionError):
        await client.get_home(HOME_ID)


# --- Event parsing -----------------------------------------------------------


def test_parse_all_recorded_events():
    events = load("ws_events.json")
    parsed = [parse_event(message, HOME_ID) for message in events]
    assert all(isinstance(event, list) and event for event in parsed)
    assert parsed[0] == [
        DeviceChange(
            device_id="00000000-0000-0000-0000-000000000003",
            feature="alarm",
            state="alarm",
            value=True,
            last_updated="2026-11-21T07:50:37.436Z",
        )
    ]
    features = {(c.feature, c.state) for event in parsed for c in event}
    assert ("lock", "state") in features
    assert ("report", "event") in features


def test_parse_ignores_other_home():
    message = load("ws_events.json")[0]
    assert parse_event(message, "another-home") is None


def test_parse_falls_back_to_single_change():
    message = load("ws_events.json")[0]
    data = dict(message["data"])
    del data["changes"]
    event = parse_event({"type": "device-state-changed", "data": data}, HOME_ID)
    assert event is not None and len(event) == 1


def test_parse_alarm_state_changed():
    message = {
        "type": "alarm-state-changed",
        "data": {
            "locationId": HOME_ID,
            "state": "ARMED_AWAY",
            "userId": "u",
            "userName": "Ola",
            "timestamp": "2026-11-21T08:00:00.000Z",
            "eventId": 1,
        },
    }
    assert parse_event(message, HOME_ID) == AlarmChange(
        state="ARMED_AWAY", user_name="Ola", timestamp="2026-11-21T08:00:00.000Z"
    )


@pytest.mark.parametrize(
    "message",
    [
        None,
        "text",
        {"type": "something-new", "data": {}},
        {"type": "device-state-changed"},
        {"type": "device-state-changed", "data": {"rootLocationId": HOME_ID}},
        {"type": "device-state-changed", "data": {"deviceId": "d", "changes": [{"value": 1}]}},
        {"type": "alarm-state-changed", "data": {"locationId": HOME_ID}},
    ],
)
def test_parse_ignores_malformed(message):
    assert parse_event(message, HOME_ID) is None


# --- WebSocket loop ----------------------------------------------------------


class FakeClient:
    def __init__(self, renewal: float = 1000.0) -> None:
        self.renewal = renewal
        self.tokens = 0

    async def access_token(self) -> str:
        self.tokens += 1
        return f"token-{self.tokens}"

    def seconds_until_renewal(self) -> float:
        return self.renewal


class FakeSio:
    """Stands in for socketio.AsyncClient."""

    def __init__(self, script: list[str], connections: list[FakeSio]) -> None:
        self.script = script
        self.handlers: dict[str, Any] = {}
        self.url: str | None = None
        self.headers: dict[str, str] | None = None
        self.disconnected = False
        connections.append(self)

    def on(self, name, handler):
        self.handlers[name] = handler

    async def connect(self, url, headers, wait_timeout):
        self.url, self.headers = url, headers
        action = self.script.pop(0)
        if action == "fail":
            raise ConnectionError("refused")
        if action == "drop":
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(self.handlers["disconnect"]("transport close"))
            )

    async def disconnect(self):
        self.disconnected = True


class StopLoop(Exception):
    pass


def make_ws(script, *, max_sleeps, client=None, events=None, clock=None):
    connections: list[FakeSio] = []
    sleeps: list[float] = []
    connected: list[int] = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= max_sleeps:
            raise StopLoop

    async def on_connected():
        connected.append(1)

    ws = HomelyWebSocket(
        client or FakeClient(),
        HOME_ID,
        on_event=(events.append if events is not None else lambda e: None),
        on_connected=on_connected,
        sleep=sleep,
        clock=clock or Clock(),
        sio_factory=lambda: FakeSio(script, connections),
    )
    return ws, connections, sleeps, connected


async def test_reconnect_delays_grow_and_cap():
    ws, connections, sleeps, connected = make_ws(["fail"] * 10, max_sleeps=8)
    with pytest.raises(StopLoop):
        await ws._run()
    assert sleeps == [5, 10, 20, 40, 80, 160, 300, 300]
    assert connected == []
    assert all(c.disconnected for c in connections)


async def test_connects_with_token_in_query_and_header():
    ws, connections, sleeps, connected = make_ws(["fail", "drop"], max_sleeps=2)
    with pytest.raises(StopLoop):
        await ws._run()
    sio = connections[1]
    assert "locationId=" + HOME_ID in sio.url
    assert "token=Bearer%20token-2" in sio.url
    assert sio.headers == {"Authorization": "Bearer token-2", "locationId": HOME_ID}
    assert connected == [1]
    assert sleeps == [5, 10]  # short session: delay keeps growing


async def test_stable_session_resets_delay():
    clock = Clock()

    class SlowClock(Clock):
        def __call__(self):
            clock.now += 100  # every reading moves time on
            return clock.now

    ws, connections, sleeps, connected = make_ws(
        ["fail", "fail", "drop"], max_sleeps=3, clock=SlowClock()
    )
    with pytest.raises(StopLoop):
        await ws._run()
    assert sleeps == [5, 10, 5]


async def test_planned_reconnect_before_token_renewal(monkeypatch):
    import api

    monkeypatch.setattr(api, "MIN_SESSION", 0.01)
    client = FakeClient(renewal=0.0)
    ws, connections, sleeps, connected = make_ws(
        ["ok", "ok", "fail"], max_sleeps=1, client=client
    )
    with pytest.raises(StopLoop):
        await ws._run()
    assert len(connections) == 3
    assert connected == [1, 1]
    assert sleeps == [5]  # no wait between planned reconnects
    assert client.tokens == 3


async def test_events_are_parsed_and_delivered():
    events: list = []
    ws, connections, sleeps, connected = make_ws(["ok"], max_sleeps=1, events=events)
    await ws._connect("token")
    handler = connections[0].handlers["event"]
    for message in load("ws_events.json")[:3]:
        await handler(message)
    await handler({"type": "unknown", "data": {}})
    assert len(events) == 3
    assert isinstance(events[0][0], DeviceChange)


async def test_start_and_stop():
    ws, connections, sleeps, connected = make_ws(["ok"], max_sleeps=99)
    await ws.start()
    for _ in range(5):
        await asyncio.sleep(0)
    assert ws.connected
    await ws.stop()
    assert not ws.connected
    assert connections[0].disconnected
