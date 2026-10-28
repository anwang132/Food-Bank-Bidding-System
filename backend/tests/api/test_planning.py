"""Planning workspace: scoping, preferences, uploads, notes and sample plans."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import AuditEntry

NEEDS_CSV = b"Item,Cases needed\nBrown rice,30\nPeanut butter,12\n"


async def upload(client, name, data, *, org=None, content_type="text/csv"):
    query = f"?filename={name}" + (f"&organization_id={org}" if org else "")
    return await client.http.post(
        f"/api/planning/documents{query}",
        content=data,
        headers=client._headers({"Content-Type": content_type}),
    )


async def test_scoping_by_role(world, client_factory):
    a = await client_factory("a@test.local")
    admin = await client_factory("admin@test.local")
    viewer = await client_factory("viewer@test.local")
    assert (await viewer.get("/api/planning/profile")).status_code == 403
    mine = (await a.get("/api/planning/profile")).json()
    assert mine["organization_id"] == world.org_a.id and mine["version"] == 0
    other = await a.get(f"/api/planning/profile?organization_id={world.org_b.id}")
    assert other.status_code == 403
    assert (await admin.get("/api/planning/profile")).status_code == 422
    r = await admin.get(f"/api/planning/documents?organization_id={world.org_b.id}")
    assert r.status_code == 200 and r.json() == []
    assert (await admin.get("/api/planning/plans?organization_id=999")).status_code == 404


async def test_preferences_save_with_version_checks(world, client_factory, maker):
    a = await client_factory("a@test.local")
    a2 = await client_factory("a2@test.local")
    prefs = {
        "households_served": 600,
        "priority_categories": ["protein", "produce", "protein"],
        "avoid_categories": ["protein", "hygiene"],
        "cold_storage_capacity": 40,
        "max_share_percent": 35,
        "dietary_notes": "Halal",
    }
    r = await a.put("/api/planning/profile", json={"version": 0, "preferences": prefs})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["version"] == 1 and saved["updated_by_name"] == "a"
    # Duplicates and contradictions are cleaned up.
    assert saved["preferences"]["priority_categories"] == ["protein", "produce"]
    assert saved["preferences"]["avoid_categories"] == ["hygiene"]

    stale = await a2.put("/api/planning/profile", json={"version": 0, "preferences": prefs})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    bad = await a.put(
        "/api/planning/profile",
        json={"version": 1, "preferences": {"priority_categories": ["caviar"]}},
    )
    assert bad.status_code == 422
    async with maker() as s:
        audit = await s.scalar(
            select(AuditEntry).where(AuditEntry.action == "planning.preferences_update")
        )
    assert audit.after["households_served"] == 600


async def test_upload_note_detail_download_delete(world, client_factory, maker):
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    r = await upload(a, "needs.csv", NEEDS_CSV)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["kind"] == "upload" and doc["size_bytes"] == len(NEEDS_CSV)
    assert [(n["item"], n["quantity"]) for n in doc["analysis"]["needs"]] == [
        ("Brown rice", 30),
        ("Peanut butter", 12),
    ]
    note = await a.post(
        "/api/planning/notes", json={"title": "Survey", "body": "- Infant formula: 9 cases"}
    )
    assert note.status_code == 201 and note.json()["analysis"]["format"] == "note"

    listed = (await a.get("/api/planning/documents")).json()
    assert [d["title"] for d in listed] == ["Survey", "needs.csv"]
    detail = (await a.get(f"/api/planning/documents/{doc['id']}")).json()
    assert detail["text"].startswith("Item,Cases needed")

    dl = await a.get(f"/api/planning/documents/{doc['id']}/download")
    assert dl.status_code == 200 and dl.content == NEEDS_CSV
    assert dl.headers["content-disposition"] == "attachment; filename*=UTF-8''needs.csv"
    assert dl.headers["content-security-policy"] == "sandbox"
    assert dl.headers["x-content-type-options"] == "nosniff"
    no_file = await a.get(f"/api/planning/documents/{note.json()['id']}/download")
    assert no_file.status_code == 404

    # Another organization cannot see, download or delete it.
    for r in (
        await b.get(f"/api/planning/documents/{doc['id']}"),
        await b.get(f"/api/planning/documents/{doc['id']}/download"),
        await b.delete(f"/api/planning/documents/{doc['id']}"),
    ):
        assert r.status_code == 404
    assert (await a.delete(f"/api/planning/documents/{doc['id']}")).status_code == 204
    assert len((await a.get("/api/planning/documents")).json()) == 1
    async with maker() as s:
        actions = list(
            await s.scalars(
                select(AuditEntry.action)
                .where(AuditEntry.target_type == "planning_document")
                .order_by(AuditEntry.id)
            )
        )
    assert actions == [
        "planning.document_upload",
        "planning.note_create",
        "planning.document_delete",
    ]


async def test_upload_limits_and_unsafe_names(world, client_factory, app):
    a = await client_factory("a@test.local")
    limit = app.state.settings.max_upload_bytes
    too_big = await upload(a, "big.csv", b"x" * (limit + 1))
    assert too_big.status_code == 413
    assert (await upload(a, "empty.csv", b"")).status_code == 422

    async def chunks():
        for _ in range(3):
            yield b"y" * (limit // 2)

    streamed = await a.http.post(
        "/api/planning/documents?filename=stream.txt", content=chunks(), headers=a._headers()
    )
    assert streamed.status_code == 413

    r = await upload(a, "..%2F..%2Fetc%2Fpasswd.md", b"# hi", content_type="text/html; x")
    assert r.status_code == 201
    assert r.json()["filename"] == "passwd.md"
    r = await upload(a, "photo.png", b"\x89PNG\r\n\x1a\n\x00", content_type="image/png")
    assert r.json()["analysis"]["format"] == "binary" and r.json()["content_type"] == "image/png"


async def test_plans_use_profile_documents_and_open_rounds(world, client_factory):
    admin = await client_factory("admin@test.local")
    a = await client_factory("a@test.local")
    lots = []
    for name, qty in (("Brown rice", 100), ("Hygiene kits", 50), ("Peanut butter", 80)):
        r = await admin.post(
            "/api/admin/lots", json={"name": name, "unit": "cases", "quantity_on_hand": qty}
        )
        lots.append(r.json()["id"])
    deadline = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    rnd = (
        await admin.post(
            "/api/admin/rounds",
            json={"name": "Week 9", "strategy": "need_weighted", "request_deadline": deadline},
        )
    ).json()
    for lot_id in lots:
        await admin.post(
            f"/api/admin/rounds/{rnd['id']}/lots", json={"lot_id": lot_id, "quantity": 50}
        )
    await admin.post(f"/api/admin/rounds/{rnd['id']}/open", json={})
    await a.post(
        f"/api/rounds/{rnd['id']}/requests",
        json={"lot_id": lots[0], "quantity": 10},
        headers={"Idempotency-Key": "plan-test-0001"},
    )
    await a.put(
        "/api/planning/profile",
        json={
            "version": 0,
            "preferences": {
                "households_served": 500,
                "priority_categories": ["protein"],
                "avoid_categories": ["hygiene"],
            },
        },
    )
    await upload(a, "needs.csv", NEEDS_CSV)

    out = (await a.get("/api/planning/plans")).json()
    assert out["inputs"] == {
        "preferences_version": 1,
        "documents": 1,
        "needs": 2,
        "open_rounds": 1,
        "lots": 3,
    }
    assert [p["key"] for p in out["plans"]] == ["balanced", "priorities", "lean"]
    balanced = out["plans"][0]
    lines = {line["lot_name"]: line for line in balanced["rounds"][0]["lines"]}
    assert set(lines) == {"Brown rice", "Peanut butter"}  # hygiene avoided
    assert lines["Brown rice"]["suggested"] == 20  # stated 30, capped at 40% of 50
    assert lines["Brown rice"]["current_quantity"] == 10
    assert lines["Brown rice"]["current_request_version"] == 1
    assert balanced["rounds"][0]["tip"].startswith("Need-weighted")
    # Admins see the same plans for that food bank.
    as_admin = await admin.get(f"/api/planning/plans?organization_id={world.org_a.id}")
    assert as_admin.json()["plans"] == out["plans"]

    suggestion = (await a.get("/api/planning/profile/suggestion")).json()
    assert suggestion["based_on_rounds"] == 1
    assert suggestion["preferences"]["priority_categories"] == ["grains"]
