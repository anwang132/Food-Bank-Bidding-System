"""Messaging: access rules, idempotent posting, unread state, moderation."""

from sqlalchemy import select

from app.models import AuditEntry, Organization


async def _regions(maker, world, a="North", b="South"):
    async with maker() as s:
        (await s.get(Organization, world.org_a.id)).region = a
        (await s.get(Organization, world.org_b.id)).region = b
        await s.commit()


async def _channels(client):
    r = await client.get("/api/chat/channels")
    assert r.status_code == 200, r.text
    return {(c["kind"], c["title"]): c for c in r.json()}


async def test_viewers_have_no_messaging(world, client_factory):
    viewer = await client_factory("viewer@test.local")
    for r in (
        await viewer.get("/api/chat/channels"),
        await viewer.get("/api/chat/people"),
        await viewer.post("/api/chat/direct", json={"user_id": world.member_a.id}),
    ):
        assert r.status_code == 403
    versions = (await viewer.get("/api/sync/versions")).json()
    assert versions["chat"] is None
    member = await client_factory("a@test.local")
    people = (await member.get("/api/chat/people")).json()
    assert world.viewer.id not in {p["id"] for p in people}
    r = await member.post("/api/chat/direct", json={"user_id": world.viewer.id})
    assert r.status_code == 422


async def test_global_and_local_channels_follow_regions(world, client_factory, maker):
    await _regions(maker, world)
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    admin = await client_factory("admin@test.local")

    a_channels = await _channels(a)
    assert set(a_channels) == {("global", "All food banks"), ("local", "North")}
    assert a_channels[("global", "All food banks")]["member_count"] == 4  # admin + 3 members
    assert a_channels[("local", "North")]["member_count"] == 3  # admin + 2 Alpha members
    assert set(await _channels(b)) == {("global", "All food banks"), ("local", "South")}
    # Admins moderate every region.
    assert set(await _channels(admin)) == {
        ("global", "All food banks"),
        ("local", "North"),
        ("local", "South"),
    }

    north = a_channels[("local", "North")]["id"]
    r = await a.post(f"/api/chat/channels/{north}/messages", json={"body": "  North only  "})
    assert r.status_code == 201 and r.json()["body"] == "North only"
    # Another region's channel looks like it does not exist.
    assert (await b.get(f"/api/chat/channels/{north}/messages")).status_code == 404
    r = await b.post(f"/api/chat/channels/{north}/messages", json={"body": "hi"})
    assert r.status_code == 404
    assert (await admin.get(f"/api/chat/channels/{north}/messages")).status_code == 200


async def test_direct_messages_are_private_and_unique_per_pair(world, client_factory):
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    a2 = await client_factory("a2@test.local")
    admin = await client_factory("admin@test.local")

    first = (await a.post("/api/chat/direct", json={"user_id": world.member_b.id})).json()
    again = (await b.post("/api/chat/direct", json={"user_id": world.member_a.id})).json()
    assert first["id"] == again["id"]
    assert (
        await a.post("/api/chat/direct", json={"user_id": world.member_a.id})
    ).status_code == 422

    # An empty conversation shows only for the person who opened it.
    assert ("direct", "b") in await _channels(a)
    assert ("direct", "a") not in await _channels(b)

    cid = first["id"]
    r = await a.post(
        f"/api/chat/channels/{cid}/messages",
        json={"body": "Spare pallets?", "client_id": "msg-0001"},
    )
    assert r.status_code == 201
    b_view = await _channels(b)
    assert b_view[("direct", "a")]["unread_count"] == 1
    assert b_view[("direct", "a")]["last_message"]["body"] == "Spare pallets?"
    assert b_view[("direct", "a")]["subtitle"] == "Alpha Pantry"
    # Neither a colleague nor an admin can read someone else's direct messages.
    assert (await a2.get(f"/api/chat/channels/{cid}/messages")).status_code == 404
    assert (await admin.get(f"/api/chat/channels/{cid}/messages")).status_code == 404


async def test_retry_with_client_id_posts_once(world, client_factory):
    a = await client_factory("a@test.local")
    gid = (await a.post("/api/chat/direct", json={"user_id": world.member_b.id})).json()["id"]
    body = {"body": "Hello", "client_id": "retry-0001"}
    r1 = await a.post(f"/api/chat/channels/{gid}/messages", json=body)
    r2 = await a.post(f"/api/chat/channels/{gid}/messages", json=body)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.headers["Idempotent-Replayed"] == "true" and r1.json()["id"] == r2.json()["id"]
    msgs = (await a.get(f"/api/chat/channels/{gid}/messages")).json()["messages"]
    assert [m["body"] for m in msgs] == ["Hello"]
    other = (await a.post("/api/chat/direct", json={"user_id": world.admin.id})).json()["id"]
    r3 = await a.post(f"/api/chat/channels/{other}/messages", json=body)
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "CLIENT_ID_REUSED"


async def test_groups_membership_and_system_messages(world, client_factory):
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    a2 = await client_factory("a2@test.local")
    r = await a.post(
        "/api/chat/groups", json={"name": "Van share", "member_ids": [world.member_b.id]}
    )
    assert r.status_code == 201
    gid = r.json()["id"]
    assert (await a2.get(f"/api/chat/channels/{gid}/messages")).status_code == 404

    r = await b.post(f"/api/chat/channels/{gid}/members", json={"user_ids": [world.member_a2.id]})
    assert r.status_code == 200
    page = (await a2.get(f"/api/chat/channels/{gid}/messages")).json()
    assert [m["kind"] for m in page["messages"]] == ["system", "system"]
    assert page["messages"][0]["body"] == "a created Van share with b"
    assert page["messages"][1]["body"] == "b added a2"
    assert (await _channels(a2))[("group", "Van share")]["member_count"] == 3

    assert (await a2.post(f"/api/chat/channels/{gid}/leave")).status_code == 204
    assert (await a2.get(f"/api/chat/channels/{gid}/messages")).status_code == 404
    bad = await a.post("/api/chat/groups", json={"name": "X", "member_ids": [world.viewer.id]})
    assert bad.status_code == 422


async def test_unread_counts_pagination_and_read_state(world, client_factory, maker):
    await _regions(maker, world)
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    gid = (await _channels(a))[("global", "All food banks")]["id"]
    ids = []
    for i in range(5):
        r = await a.post(f"/api/chat/channels/{gid}/messages", json={"body": f"m{i}"})
        ids.append(r.json()["id"])
    before = (await b.get("/api/sync/versions")).json()["chat"]
    assert (await _channels(b))[("global", "All food banks")]["unread_count"] == 5
    assert (await _channels(a))[("global", "All food banks")]["unread_count"] == 0

    page = (await b.get(f"/api/chat/channels/{gid}/messages?limit=2")).json()
    assert [m["body"] for m in page["messages"]] == ["m3", "m4"] and page["has_more"]
    older = (await b.get(f"/api/chat/channels/{gid}/messages?limit=10&before={ids[3]}")).json()
    assert [m["body"] for m in older["messages"]] == ["m0", "m1", "m2"]
    assert not older["has_more"]

    r = await b.post(f"/api/chat/channels/{gid}/read", json={"message_id": ids[2]})
    assert r.json()["last_read_message_id"] == ids[2]
    # Reading never moves backwards.
    await b.post(f"/api/chat/channels/{gid}/read", json={"message_id": ids[0]})
    state = (await _channels(b))[("global", "All food banks")]
    assert state["unread_count"] == 2 and state["last_read_message_id"] == ids[2]
    assert (await b.get("/api/sync/versions")).json()["chat"] != before


async def test_delete_own_and_moderation_is_audited(world, client_factory, maker):
    a = await client_factory("a@test.local")
    b = await client_factory("b@test.local")
    admin = await client_factory("admin@test.local")
    gid = (await _channels(a))[("global", "All food banks")]["id"]
    mine = (await a.post(f"/api/chat/channels/{gid}/messages", json={"body": "oops"})).json()
    theirs = (await b.post(f"/api/chat/channels/{gid}/messages", json={"body": "spam"})).json()

    assert (await a.delete(f"/api/chat/messages/{theirs['id']}")).status_code == 403
    r = await a.delete(f"/api/chat/messages/{mine['id']}")
    assert r.status_code == 200 and r.json()["deleted"] and r.json()["body"] == ""
    assert (await a.delete(f"/api/chat/messages/{mine['id']}")).status_code == 200

    assert (await admin.delete(f"/api/chat/messages/{theirs['id']}")).status_code == 200
    msgs = (await b.get(f"/api/chat/channels/{gid}/messages")).json()["messages"]
    assert [(m["deleted"], m["body"]) for m in msgs] == [(True, ""), (True, "")]
    async with maker() as s:
        entries = (
            await s.scalars(select(AuditEntry).where(AuditEntry.action.like("chat.%")))
        ).all()
    assert [(e.action, e.target_id) for e in entries] == [
        ("chat.message_moderate", str(theirs["id"]))
    ]
    assert "spam" not in str(entries[0].before)


async def test_message_validation_and_rate_limit(world, client_factory, app):
    app.state.settings.chat_messages_per_minute = 3
    a = await client_factory("a@test.local")
    gid = (await _channels(a))[("global", "All food banks")]["id"]
    url = f"/api/chat/channels/{gid}/messages"
    assert (await a.post(url, json={"body": "   "})).status_code == 422
    assert (await a.post(url, json={"body": "x" * 4001})).status_code == 422
    assert (await a.post(url, json={"body": "hi", "client_id": "bad id!"})).status_code == 422
    for i in range(3):
        assert (await a.post(url, json={"body": f"m{i}"})).status_code == 201
    r = await a.post(url, json={"body": "one too many"})
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"
