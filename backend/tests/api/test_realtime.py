"""Realtime tests with two real uvicorn servers (separate app instances, separate
hubs) that share nothing but Redis. Redis outages are simulated with a TCP proxy
between the servers and Redis that the test can cut and restore.

Negative assertions ("org B received nothing about org A") use an ordering marker:
a later public event is published, and the first message the socket receives must
be that marker."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
import pytest
import uvicorn
import websockets
from sqlalchemy import update

from app.main import create_app
from app.models import UserSession
from app.realtime.hub import Hub
from tests.conftest import ORIGIN, PASSWORD, TEST_REDIS_URL, make_settings


class TcpProxy:
    def __init__(self, host: str, port: int) -> None:
        self.target = (host, port)
        self.blocked = False
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        if self.blocked:
            writer.close()
            return
        try:
            up_reader, up_writer = await asyncio.open_connection(*self.target)
        except OSError:
            writer.close()
            return
        self._writers |= {writer, up_writer}

        async def pipe(src, dst):
            with contextlib.suppress(Exception):
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            dst.close()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

    def cut(self) -> None:
        self.blocked = True
        for w in list(self._writers):
            w.close()
        self._writers.clear()

    def restore(self) -> None:
        self.blocked = False

    async def stop(self) -> None:
        self.cut()
        self.server.close()


class Server:
    def __init__(self, name: str, redis_url: str) -> None:
        self.name = name
        self.app = create_app(
            make_settings(instance_id=name, redis_url=redis_url, ws_revalidate_seconds=2)
        )
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=0,
                log_config=None,
                log_level="warning",
                lifespan="on",
            )
        )

    async def start(self) -> None:
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        self.server.should_exit = True
        await self.task


@pytest.fixture
async def redis_proxy():
    url = urlparse(TEST_REDIS_URL)
    proxy = TcpProxy(url.hostname, url.port)
    port = await proxy.start()
    yield proxy, f"redis://127.0.0.1:{port}{url.path}"
    await proxy.stop()


@pytest.fixture
async def servers(redis_proxy):
    _, proxied_url = redis_proxy
    a = Server("api-a", TEST_REDIS_URL)
    b = Server("api-b", proxied_url)  # B reaches Redis through the cuttable proxy
    await a.start()
    await b.start()
    yield a, b
    await b.stop()
    await a.stop()


class Session:
    def __init__(self, server: Server) -> None:
        self.http = httpx.AsyncClient(base_url=server.base, headers={"Origin": ORIGIN})
        self.server = server

    async def login(self, email):
        r = await self.http.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        self.token = r.cookies["fb_session"]
        return self

    async def post(self, url, json=None, headers=None):
        return await self.http.post(
            url, json=json, headers={"X-CSRF-Token": self.csrf, **(headers or {})}
        )

    async def patch(self, url, json=None):
        return await self.http.patch(url, json=json, headers={"X-CSRF-Token": self.csrf})

    def ws(self, server: Server | None = None, origin: str = ORIGIN, cookie: bool = True):
        target = server or self.server
        headers = {"Origin": origin}
        if cookie:
            headers["Cookie"] = f"fb_session={self.token}"
        return websockets.connect(
            f"ws://127.0.0.1:{target.port}/api/ws", additional_headers=headers
        )


async def recv(ws, timeout=10.0):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def recv_type(ws, type_, timeout=10.0, scope=None):
    """Skip unrelated messages (e.g. resync on subscriber connect) until a match."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        msg = await recv(ws, max(0.1, deadline - loop.time()))
        if msg["type"] == type_ and (scope is None or msg.get("scope") == scope):
            return msg


async def wait_subscribed(server: Server, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not server.app.state.bus.stats.subscriber_connected:
        assert loop.time() < deadline, f"{server.name} subscriber never connected"
        await asyncio.sleep(0.02)


async def open_round(admin: Session) -> tuple[int, int]:
    lot = (
        await admin.post(
            "/api/admin/lots", json={"name": "Oats", "unit": "boxes", "quantity_on_hand": 50}
        )
    ).json()
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    rnd = (
        await admin.post(
            "/api/admin/rounds",
            json={"name": "Live", "strategy": "fcfs", "request_deadline": deadline},
        )
    ).json()
    await admin.post(
        f"/api/admin/rounds/{rnd['id']}/lots", json={"lot_id": lot["id"], "quantity": 20}
    )
    r = await admin.post(f"/api/admin/rounds/{rnd['id']}/open", json={})
    assert r.status_code == 200
    return rnd["id"], lot["id"]


async def test_cross_instance_delivery_and_org_isolation(world, servers):
    a, b = servers
    await wait_subscribed(a)
    await wait_subscribed(b)
    admin = await Session(a).login("admin@test.local")
    member_a = await Session(a).login("a@test.local")
    member_b = await Session(b).login("b@test.local")
    rid, lot_id = await open_round(admin)

    async with admin.ws(b) as admin_ws, member_b.ws(b) as b_ws, member_a.ws(b) as a_ws:
        for sock in (admin_ws, b_ws, a_ws):
            hello = await recv(sock)
            assert hello["type"] == "hello" and hello["instance"] == "api-b"
        # Member A writes through instance A; sockets live on instance B.
        r = await member_a.post(
            f"/api/rounds/{rid}/requests",
            json={"lot_id": lot_id, "quantity": 5},
            headers={"Idempotency-Key": "rt-create-0001"},
        )
        assert r.status_code == 201
        assert r.headers["x-instance-id"] == "api-a"
        got_admin = await recv_type(admin_ws, "round.changed", scope="admin")
        got_a = await recv_type(a_ws, "round.changed", scope="org")
        assert got_admin["round_id"] == rid and got_a["round_id"] == rid
        assert set(got_a) == {"type", "scope", "round_id", "version"}  # ids/versions only

        # Marker: a public change. Org B's first round event must be the marker,
        # proving org A's private event was never routed to it.
        await admin.post(f"/api/admin/rounds/{rid}/close", json={})
        first_b = await recv_type(b_ws, "round.changed")
        assert first_b["scope"] == "public"


async def test_handshake_rejects_bad_origin_and_missing_session(world, servers):
    a, _ = servers
    member = await Session(a).login("a@test.local")
    # Closing before accept is surfaced to the client as an HTTP 403 handshake failure.
    for kwargs in ({"origin": "https://evil.example"}, {"cookie": False}):
        with pytest.raises(websockets.exceptions.InvalidStatus) as err:
            async with member.ws(**kwargs) as ws:
                await recv(ws)
        assert err.value.response.status_code == 403


async def test_logout_closes_socket(world, servers):
    a, b = servers
    await wait_subscribed(b)
    member = await Session(a).login("a@test.local")
    async with member.ws(b) as ws:
        await recv(ws)
        assert (await member.post("/api/auth/logout")).status_code == 204
        with pytest.raises(websockets.exceptions.ConnectionClosed) as err:
            while True:
                await recv(ws, 10)
        assert err.value.rcvd.code == 4401


async def test_membership_change_forces_resubscribe(world, servers):
    a, b = servers
    await wait_subscribed(b)
    admin = await Session(a).login("admin@test.local")
    member = await Session(a).login("a@test.local")
    async with member.ws(b) as ws:
        await recv(ws)
        r = await admin.patch(
            f"/api/admin/users/{world.member_a.id}",
            json={"version": 1, "organization_id": world.org_b.id, "reason": "transfer"},
        )
        assert r.status_code == 200
        with pytest.raises(websockets.exceptions.ConnectionClosed) as err:
            while True:
                await recv(ws, 10)
        assert err.value.rcvd.code == 4409


async def test_session_expiry_closes_socket(world, servers, maker):
    a, _ = servers
    member = await Session(a).login("a@test.local")
    async with member.ws() as ws:
        await recv(ws)
        async with maker() as s:
            await s.execute(
                update(UserSession).values(
                    expires_at=datetime.now(UTC) + timedelta(milliseconds=300)
                )
            )
            await s.commit()
        with pytest.raises(websockets.exceptions.ConnectionClosed) as err:
            while True:
                await recv(ws, 10)  # revalidation (every 2 s here) finds it expired
        assert err.value.rcvd.code == 4401


async def test_redis_outage_and_subscriber_recovery(world, servers, redis_proxy):
    """B loses Redis while its browser socket stays open. Writes on A still commit.
    When Redis returns, B resubscribes and tells its sockets to resync; the version
    endpoint then shows the state the socket missed."""
    a, b = servers
    proxy, _ = redis_proxy
    await wait_subscribed(b)
    admin = await Session(a).login("admin@test.local")
    member = await Session(a).login("a@test.local")
    rid, lot_id = await open_round(admin)
    async with member.ws(b) as ws:
        await recv(ws)
        versions = (await member.http.get("/api/sync/versions")).json()["rounds"]
        before = next(v for v in versions if v["round_id"] == rid)["org_version"]

        proxy.cut()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while b.app.state.bus.stats.subscriber_connected:
            assert loop.time() < deadline
            await asyncio.sleep(0.02)
        ready = await httpx.AsyncClient().get(f"{b.base}/readyz")
        assert ready.status_code == 200 and ready.json()["redis"] == "degraded"

        r = await member.post(
            f"/api/rounds/{rid}/requests",
            json={"lot_id": lot_id, "quantity": 3},
            headers={"Idempotency-Key": "rt-create-0002"},
        )
        assert r.status_code == 201  # write unaffected by B's outage

        proxy.restore()
        msg = await recv_type(ws, "resync", timeout=15)
        assert msg["reason"] == "subscriber_connected"
        versions = (await member.http.get("/api/sync/versions")).json()["rounds"]
        assert next(v for v in versions if v["round_id"] == rid)["org_version"] > before


async def test_publish_failure_does_not_fail_write(world, maker):
    """With Redis unreachable, the write commits, the client gets success, and a
    socket on the same process still receives the event via local fallback."""
    server = Server("api-down", "redis://127.0.0.1:1/0")
    await server.start()
    try:
        admin = await Session(server).login("admin@test.local")
        member = await Session(server).login("a@test.local")
        rid, lot_id = await open_round(admin)
        async with member.ws() as ws:
            await recv(ws)
            r = await member.post(
                f"/api/rounds/{rid}/requests",
                json={"lot_id": lot_id, "quantity": 4},
                headers={"Idempotency-Key": "rt-create-0003"},
            )
            assert r.status_code == 201
            msg = await recv_type(ws, "round.changed", scope="org")
            assert msg["round_id"] == rid
        stats = server.app.state.bus.stats
        assert stats.publish_failures >= 1
        ready = await httpx.AsyncClient().get(f"{server.base}/readyz")
        assert ready.status_code == 200 and ready.json()["redis"] == "degraded"
    finally:
        await server.stop()


def test_slow_consumer_queue_collapses_to_resync():
    hub = Hub(queue_size=3)
    conn = hub.register(1, frozenset({"public", "user:1"}))
    for v in range(10):
        hub.dispatch({"topic": "public", "type": "round.changed", "round_id": 1, "version": v})
    items = []
    while not conn.queue.empty():
        items.append(conn.queue.get_nowait())
    assert items[0] == {"type": "resync", "reason": "slow_consumer"}
    assert len(items) <= 3 and conn.overflows >= 1


def test_hub_routes_by_topic_and_strips_fields():
    hub = Hub(queue_size=10)
    member_a = hub.register(1, frozenset({"public", "org:1", "user:1"}))
    member_b = hub.register(2, frozenset({"public", "org:2", "user:2"}))
    hub.dispatch(
        {
            "topic": "org:1",
            "type": "round.changed",
            "round_id": 5,
            "version": 3,
            "origin": "api-1",
            "secret": "x",
        }
    )
    assert member_b.queue.empty()
    assert member_a.queue.get_nowait() == {
        "type": "round.changed",
        "scope": "org",
        "round_id": 5,
        "version": 3,
    }
    hub.dispatch({"topic": "user:2", "type": "session.revoked"})
    assert member_b.revalidate.is_set() and not member_a.revalidate.is_set()
    assert member_b.queue.empty()


def test_chat_events_reach_named_recipients_only():
    hub = Hub(queue_size=10)
    member = hub.register(1, frozenset({"public", "org:1", "user:1", "chat"}))
    other = hub.register(2, frozenset({"public", "org:2", "user:2", "chat"}))
    viewer = hub.register(3, frozenset({"public", "user:3"}))
    hub.dispatch(
        {
            "topic": "chat",
            "type": "chat.message",
            "channel_id": 7,
            "recipients": [1, 3],
            "origin": "api-2",
        }
    )
    assert member.queue.get_nowait() == {"type": "chat.message", "scope": "chat", "channel_id": 7}
    assert other.queue.empty()
    assert viewer.queue.empty()  # named, but viewers never subscribe to chat
