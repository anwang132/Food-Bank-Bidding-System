"""End-to-end API workflow and object-level authorization."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import AllocationResult, AuditEntry, InventoryLot


def _deadline(minutes=60):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


async def _setup_open_round(admin, strategy="fcfs", supply=60, stock=100):
    lot = (
        await admin.post(
            "/api/admin/lots", json={"name": "Rice", "unit": "bags", "quantity_on_hand": stock}
        )
    ).json()
    rnd = (
        await admin.post(
            "/api/admin/rounds",
            json={"name": "Week 1", "strategy": strategy, "request_deadline": _deadline()},
        )
    ).json()
    r = await admin.post(
        f"/api/admin/rounds/{rnd['id']}/lots", json={"lot_id": lot["id"], "quantity": supply}
    )
    assert r.status_code == 201, r.text
    r = await admin.post(f"/api/admin/rounds/{rnd['id']}/open", json={})
    assert r.status_code == 200 and r.json()["status"] == "open"
    return rnd["id"], lot["id"]


async def test_full_workflow(world, client_factory, maker):
    admin = await client_factory("admin@test.local")
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    rid, lot_id = await _setup_open_round(admin)

    # Members submit; an identical retry replays instead of creating a duplicate.
    key = {"Idempotency-Key": "a-create-0001"}
    r1 = await a.post(
        f"/api/rounds/{rid}/requests", json={"lot_id": lot_id, "quantity": 30}, headers=key
    )
    r2 = await a.post(
        f"/api/rounds/{rid}/requests", json={"lot_id": lot_id, "quantity": 30}, headers=key
    )
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"] and r2.headers["Idempotent-Replayed"] == "true"
    r3 = await a.post(
        f"/api/rounds/{rid}/requests", json={"lot_id": lot_id, "quantity": 31}, headers=key
    )
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    missing = await a.post(f"/api/rounds/{rid}/requests", json={"lot_id": lot_id, "quantity": 3})
    assert missing.status_code == 422

    rb = await b.post(
        f"/api/rounds/{rid}/requests",
        json={"lot_id": lot_id, "quantity": 50},
        headers={"Idempotency-Key": "b-create-0001"},
    )
    assert rb.status_code == 201
    req_a = r1.json()

    # Edit with version; a stale version is a conflict, not an overwrite.
    ok = await a.patch(f"/api/requests/{req_a['id']}", json={"quantity": 40, "version": 1})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = await a.patch(f"/api/requests/{req_a['id']}", json={"quantity": 45, "version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"

    # Member view contains only their own requests, without accepted order.
    detail = (await a.get(f"/api/rounds/{rid}")).json()
    assert [r["organization_id"] for r in detail["requests"]] == [world.org_a.id]
    assert detail["requests"][0]["accepted_seq"] is None and detail["participants"] is None

    # Close freezes input; late requests are rejected.
    assert (await admin.post(f"/api/admin/rounds/{rid}/close", json={})).status_code == 200
    late = await b.patch(f"/api/requests/{rb.json()['id']}", json={"quantity": 1, "version": 1})
    assert late.status_code == 409 and late.json()["error"]["code"] == "INVALID_STATE"

    preview = (await admin.get(f"/api/admin/rounds/{rid}/preview")).json()
    assert [ln["allocated"] for ln in preview["lots"][0]["lines"]] == [40, 20]
    whatif = (await admin.get(f"/api/admin/rounds/{rid}/preview?strategy=need_weighted")).json()
    assert whatif["strategy"] == "need_weighted" and whatif["frozen_strategy"] == "fcfs"

    fin_key = {"Idempotency-Key": "finalize-0001"}
    f1 = await admin.post(f"/api/admin/rounds/{rid}/finalize", headers=fin_key)
    f2 = await admin.post(f"/api/admin/rounds/{rid}/finalize", headers=fin_key)
    f3 = await admin.post(
        f"/api/admin/rounds/{rid}/finalize", headers={"Idempotency-Key": "finalize-0002"}
    )
    assert f1.status_code == f2.status_code == 200 and f2.headers["Idempotent-Replayed"] == "true"
    assert f1.json()["result_hash"] == preview["result_hash"] == f2.json()["result_hash"]
    assert f3.status_code == 409 and f3.json()["error"]["code"] == "ALREADY_FINALIZED"

    async with maker() as s:
        assert await s.scalar(select(func.count()).select_from(AllocationResult)) == 2
        lot = await s.get(InventoryLot, lot_id)
        assert (lot.quantity_on_hand, lot.quantity_reserved) == (40, 0)
        actions = (await s.scalars(select(AuditEntry.action).order_by(AuditEntry.id))).all()
        assert "round.finalize" in actions and "request.update" in actions

    # Members see only their own result lines, with explanations.
    res_a = (await a.get(f"/api/rounds/{rid}/results")).json()
    lines = res_a["lots"][0]["lines"]
    assert len(lines) == 1 and lines[0]["allocated"] == 40
    assert lines[0]["explanation"].startswith("Fully filled")
    res_b = (await b.get(f"/api/rounds/{rid}/results")).json()
    assert res_b["lots"][0]["lines"][0]["allocated"] == 20
    assert "Partly filled" in res_b["lots"][0]["lines"][0]["explanation"]

    # Public summary: aggregates only.
    viewer = await client_factory("viewer@test.local")
    public = (await viewer.get("/api/public/rounds")).json()
    assert public[0]["lots"][0] == {
        "name": "Rice",
        "unit": "bags",
        "quantity": 60,
        "allocated_total": 60,
        "organizations_served": 2,
    }
    assert "Alpha" not in str(public) and "Beta" not in str(public)


async def test_cross_organization_access_is_rejected(world, client_factory):
    admin = await client_factory("admin@test.local")
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    rid, lot_id = await _setup_open_round(admin)
    rb = (
        await b.post(
            f"/api/rounds/{rid}/requests",
            json={"lot_id": lot_id, "quantity": 5},
            headers={"Idempotency-Key": "b-create-0002"},
        )
    ).json()
    r = await a.patch(f"/api/requests/{rb['id']}", json={"quantity": 1, "version": 1})
    assert r.status_code == 404
    r = await a.post(f"/api/requests/{rb['id']}/withdraw", json={"version": 1})
    assert r.status_code == 404
    detail = (await a.get(f"/api/rounds/{rid}")).json()
    assert detail["requests"] == []
    # organization_id in the body is not accepted: requests are always for the caller's org
    r = await a.post(
        f"/api/rounds/{rid}/requests",
        json={"lot_id": lot_id, "quantity": 1, "organization_id": world.org_b.id},
        headers={"Idempotency-Key": "a-create-0009"},
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/admin/lots"),
        ("get", "/api/admin/users"),
        ("get", "/api/admin/audit"),
        ("get", "/api/admin/organizations"),
        ("post", "/api/admin/rounds"),
        ("post", "/api/admin/rounds/1/close"),
        ("post", "/api/admin/rounds/1/finalize"),
        ("get", "/api/admin/rounds/1/preview"),
        ("put", "/api/admin/rounds/1/participants/1"),
    ],
)
@pytest.mark.parametrize("email", ["a@test.local", "viewer@test.local"])
async def test_non_admins_cannot_use_admin_endpoints(world, client_factory, method, path, email):
    c = await client_factory(email)
    fn = getattr(c, method)
    r = await (fn(path) if method == "get" else fn(path, json={}))
    assert r.status_code == 403


async def test_viewer_sees_public_only(world, client_factory):
    admin = await client_factory("admin@test.local")
    await _setup_open_round(admin)
    viewer = await client_factory("viewer@test.local")
    assert (await viewer.get("/api/rounds")).status_code == 403
    assert (await viewer.get("/api/rounds/1")).status_code == 403
    assert (await viewer.get("/api/public/rounds")).status_code == 200
    r = await viewer.post(
        "/api/rounds/1/requests",
        json={"lot_id": 1, "quantity": 1},
        headers={"Idempotency-Key": "viewer-0001"},
    )
    assert r.status_code == 403


async def test_unauthenticated_requests_rejected(world, client_factory):
    c = await client_factory()
    for path in ("/api/rounds", "/api/public/rounds", "/api/admin/lots", "/api/sync/versions"):
        assert (await c.get(path)).status_code == 401


async def test_draft_rounds_hidden_from_members(world, client_factory):
    admin = await client_factory("admin@test.local")
    rnd = (
        await admin.post(
            "/api/admin/rounds",
            json={"name": "Draft", "strategy": "fcfs", "request_deadline": _deadline()},
        )
    ).json()
    a = await client_factory("a@test.local")
    assert (await a.get(f"/api/rounds/{rnd['id']}")).status_code == 404
    assert (await a.get("/api/rounds")).json() == []


async def test_ineligible_org_cannot_request_and_gets_zero(world, client_factory):
    admin = await client_factory("admin@test.local")
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    rid, lot_id = await _setup_open_round(admin, strategy="need_weighted")
    await b.post(
        f"/api/rounds/{rid}/requests",
        json={"lot_id": lot_id, "quantity": 20},
        headers={"Idempotency-Key": "b-create-0003"},
    )
    await a.post(
        f"/api/rounds/{rid}/requests",
        json={"lot_id": lot_id, "quantity": 20},
        headers={"Idempotency-Key": "a-create-0003"},
    )
    r = await admin.put(
        f"/api/admin/rounds/{rid}/participants/{world.org_b.id}",
        json={"eligible": False, "weight": 1, "priority": 5, "reason": "missed compliance check"},
    )
    assert r.status_code == 200
    late = await b.post(
        f"/api/rounds/{rid}/requests",
        json={"lot_id": lot_id, "quantity": 1},
        headers={"Idempotency-Key": "b-create-0004"},
    )
    assert late.status_code in (403, 409)
    await admin.post(f"/api/admin/rounds/{rid}/close", json={})
    await admin.post(f"/api/admin/rounds/{rid}/finalize", headers={"Idempotency-Key": "fin-0003"})
    res = (await b.get(f"/api/rounds/{rid}/results")).json()["lots"][0]["lines"][0]
    assert res["allocated"] == 0 and "not eligible" in res["explanation"]
    audit = (await admin.get("/api/admin/audit?target_type=participant")).json()
    assert audit[0]["reason"] == "missed compliance check"
    assert audit[0]["before"]["eligible"] is True and audit[0]["after"]["eligible"] is False


async def test_invalid_transitions_rejected(world, client_factory):
    admin = await client_factory("admin@test.local")
    rnd = (
        await admin.post(
            "/api/admin/rounds",
            json={"name": "Empty", "strategy": "fcfs", "request_deadline": _deadline()},
        )
    ).json()
    rid = rnd["id"]
    r = await admin.post(f"/api/admin/rounds/{rid}/open", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_LOTS"
    assert (await admin.post(f"/api/admin/rounds/{rid}/close", json={})).status_code == 409
    assert (await admin.get(f"/api/admin/rounds/{rid}/preview")).status_code == 409
    r = await admin.post(
        f"/api/admin/rounds/{rid}/finalize", headers={"Idempotency-Key": "f-000001"}
    )
    assert r.status_code == 409
    assert (
        await admin.post(f"/api/admin/rounds/{rid}/cancel", json={"reason": "duplicate"})
    ).status_code == 200
    assert (await admin.post(f"/api/admin/rounds/{rid}/open", json={})).status_code == 409


async def test_lot_reduction_below_reserved_rejected(world, client_factory):
    admin = await client_factory("admin@test.local")
    rid, lot_id = await _setup_open_round(admin, supply=60, stock=100)
    lots = (await admin.get("/api/admin/lots")).json()
    lot = next(x for x in lots if x["id"] == lot_id)
    assert lot["quantity_available"] == 40 and lot["reserved_by_round_id"] == rid
    r = await admin.patch(
        f"/api/admin/lots/{lot_id}",
        json={"version": lot["version"], "quantity_on_hand": 59, "reason": "damaged"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "BELOW_RESERVED"
    r = await admin.patch(
        f"/api/admin/lots/{lot_id}",
        json={"version": lot["version"], "quantity_on_hand": 60, "reason": "damaged"},
    )
    assert r.status_code == 200 and r.json()["quantity_available"] == 0


async def test_versions_endpoint_per_role(world, client_factory):
    admin = await client_factory("admin@test.local")
    rid, lot_id = await _setup_open_round(admin)
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    before_b = (await b.get("/api/sync/versions")).json()["rounds"][0]
    await a.post(
        f"/api/rounds/{rid}/requests",
        json={"lot_id": lot_id, "quantity": 5},
        headers={"Idempotency-Key": "a-create-0005"},
    )
    va = (await a.get("/api/sync/versions")).json()["rounds"][0]
    vb = (await b.get("/api/sync/versions")).json()["rounds"][0]
    vadm = (await admin.get("/api/sync/versions")).json()["rounds"][0]
    assert set(va) == {"round_id", "public_version", "org_version"}
    assert "version" in vadm
    # Org A's activity is invisible in org B's and the public counters.
    assert vb == before_b
    assert va["org_version"] > before_b["org_version"]
