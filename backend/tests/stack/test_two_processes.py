"""Cross-process verification against the running docker compose stack:
two API containers behind nginx, sharing Postgres and Redis.

    docker compose up -d --build && docker compose exec api-1 python -m app.cli seed
    cd backend && uv run pytest -m stack tests/stack -v

Stops and restarts the compose `redis` service; do not run against a stack whose
Redis you care about.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import websockets

pytestmark = pytest.mark.stack

PROXY = os.environ.get("STACK_URL", "http://localhost:8080")
DIRECT = {"api-1": "http://localhost:8001", "api-2": "http://localhost:8002"}
ORIGIN = "http://localhost:8080"
PASSWORD = os.environ.get("STACK_PASSWORD", "foodbank-demo")
ROOT = Path(__file__).resolve().parents[3]
RECOVERY_BOUND_S = 20  # resync interval (15 s) + one fetch; see docs/DECISIONS.md D8


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


async def login(base: str, email: str) -> tuple[httpx.AsyncClient, str, str]:
    client = httpx.AsyncClient(base_url=base, headers={"Origin": ORIGIN}, timeout=10)
    r = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client, r.json()["csrf_token"], r.cookies["fb_session"]


async def socket_on(instance: str, token: str):
    """Connect through nginx until round-robin lands on the wanted instance."""
    ws_url = PROXY.replace("http", "ws") + "/api/ws"
    headers = {"Origin": ORIGIN, "Cookie": f"fb_session={token}"}
    for _ in range(10):
        ws = await websockets.connect(ws_url, additional_headers=headers)
        hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if hello["instance"] == instance:
            return ws
        await ws.close()
    raise AssertionError(f"could not reach {instance} through the proxy")


async def next_of(ws, type_: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, deadline - time.monotonic())))
        if msg["type"] == type_:
            return msg


async def open_round(admin: httpx.AsyncClient, csrf: str) -> int:
    h = {"X-CSRF-Token": csrf}
    stamp = datetime.now(UTC).strftime("%H%M%S%f")
    lot = (
        await admin.post(
            "/api/admin/lots",
            headers=h,
            json={"name": f"Stack lot {stamp}", "unit": "cases", "quantity_on_hand": 10},
        )
    ).json()
    rnd = (
        await admin.post(
            "/api/admin/rounds",
            headers=h,
            json={
                "name": f"Stack round {stamp}",
                "strategy": "fcfs",
                "request_deadline": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
    ).json()
    await admin.post(
        f"/api/admin/rounds/{rnd['id']}/lots", headers=h, json={"lot_id": lot["id"], "quantity": 5}
    )
    r = await admin.post(f"/api/admin/rounds/{rnd['id']}/open", headers=h, json={})
    assert r.status_code == 200, r.text
    return rnd["id"]


async def edit_description(admin: httpx.AsyncClient, csrf: str, round_id: int, text: str) -> int:
    detail = (await admin.get(f"/api/rounds/{round_id}")).json()
    r = await admin.patch(
        f"/api/admin/rounds/{round_id}",
        headers={"X-CSRF-Token": csrf},
        json={"version": detail["public_version"], "description": text},
    )
    assert r.status_code == 200, r.text
    assert r.headers["x-instance-id"] in DIRECT
    return r.json()["public_version"]


async def test_cross_process_delivery_both_directions():
    admin1, csrf1, _ = await login(DIRECT["api-1"], "admin@example.org")
    admin2, csrf2, _ = await login(DIRECT["api-2"], "admin@example.org")
    _, _, member_token = await login(PROXY, "northside@example.org")
    rid = await open_round(admin1, csrf1)
    ws1 = await socket_on("api-1", member_token)
    ws2 = await socket_on("api-2", member_token)
    try:
        v = await edit_description(admin1, csrf1, rid, "written on api-1")
        msg = await next_of(ws2, "round.changed", 5)  # api-1 write → api-2 socket
        assert (msg["round_id"], msg["scope"], msg["version"]) == (rid, "public", v)
        await next_of(ws1, "round.changed", 5)
        v = await edit_description(admin2, csrf2, rid, "written on api-2")
        msg = await next_of(ws1, "round.changed", 5)  # api-2 write → api-1 socket
        assert msg["version"] == v
    finally:
        await ws1.close()
        await ws2.close()


async def test_redis_outage_degrades_then_recovers():
    admin1, csrf1, _ = await login(DIRECT["api-1"], "admin@example.org")
    member, _, member_token = await login(PROXY, "northside@example.org")
    rid = await open_round(admin1, csrf1)
    ws2 = await socket_on("api-2", member_token)
    try:
        compose("stop", "redis")
        # Readiness stays green (degraded realtime) on both processes.
        async with httpx.AsyncClient() as c:
            for base in DIRECT.values():
                r = await c.get(f"{base}/readyz")
                assert r.status_code == 200 and r.json()["redis"] == "degraded"
        # Writes still commit; the api-2 socket cannot hear about it via Redis.
        v = await edit_description(admin1, csrf1, rid, "written during outage")
        versions = (await member.get("/api/sync/versions")).json()["rounds"]
        assert next(x for x in versions if x["round_id"] == rid)["public_version"] == v

        restarted = time.monotonic()
        compose("start", "redis")
        # api-2's subscriber reconnects and tells its still-open sockets to resync.
        msg = await next_of(ws2, "resync", RECOVERY_BOUND_S)
        assert msg["reason"] == "subscriber_connected"
        assert time.monotonic() - restarted < RECOVERY_BOUND_S
    finally:
        compose("start", "redis")
        await ws2.close()
