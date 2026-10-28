"""Synthetic demo data for a food bank network with several weeks of history.

Everything is created through the same services the API uses, so every invariant,
audit record and version counter is real. Afterwards, timestamps of past rounds are
shifted back in time so the history reads like a running system. Only timestamps
move; quantities and order are untouched.

All people and organizations are fictional. Every account uses DEMO_PASSWORD."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.security import hash_password
from app.seed_workspace import seed_conversations, seed_planning
from app.services import allocation, directory, inventory, rounds
from app.services import requests as request_service
from app.services.common import Actor, Changes

DEMO_PASSWORD = "foodbank-demo"

# slug, name, need weight, priority tier (1 = highest), region
ORGS = [
    ("northside", "Northside Community Pantry", 3, 1, "North Valley"),
    ("eastside", "Eastside Food Shelf", 2, 2, "Bayfront"),
    ("riverside", "Riverside Family Center", 1, 3, "South County"),
    ("westgate", "Westgate Mutual Aid", 2, 2, "Bayfront"),
    ("harbor", "Harbor Point Food Bank", 3, 2, "Bayfront"),
    ("lakeside", "Lakeside Senior Meals", 2, 1, "North Valley"),
    ("southview", "Southview Youth Kitchen", 2, 3, "South County"),
    ("maple", "Maple Street Shelter", 4, 1, "North Valley"),
]

# email, display name, organization slug (members) or role
PEOPLE = [
    ("priya.raman@example.org", "Priya Raman", "admin"),
    ("northside@example.org", "Dana Whitfield", "northside"),
    ("m.alvarez@example.org", "Maria Alvarez", "northside"),
    ("eastside@example.org", "Jordan Okafor", "eastside"),
    ("riverside@example.org", "Sam Lindqvist", "riverside"),
    ("westgate@example.org", "Chris Nakamura", "westgate"),
    ("harborpoint@example.org", "Taylor Brooks", "harbor"),
    ("lakeside@example.org", "Morgan Ellis", "lakeside"),
    ("southview@example.org", "Riley Chen", "southview"),
    ("maplestreet@example.org", "Jamie Ortiz", "maple"),
    ("viewer@example.org", "Casey Morgan", "viewer"),
]

# key, name, description, unit, on hand, best-before in days (None = non-perishable)
LOTS = [
    ("beans", "Canned black beans", "15 oz cans, 24 per case", "cases", 400, 540),
    ("rice", "Brown rice", "25 lb bags", "bags", 260, 300),
    ("apples", "Fresh apples", "Mixed varieties, 40 lb boxes", "boxes", 120, 12),
    ("carrots", "Fresh carrots", "50 lb sacks", "sacks", 85, 18),
    ("formula", "Infant formula", "Powdered, 12 cans per case", "cases", 60, 200),
    ("pb", "Peanut butter", "16 oz jars, 12 per case", "cases", 150, 365),
    ("milk", "Shelf-stable milk", "1 qt cartons, 12 per case", "cases", 140, 150),
    ("pasta", "Whole wheat pasta", "1 lb boxes, 20 per case", "cases", 180, 420),
    ("tuna", "Canned tuna", "5 oz cans, 48 per case", "cases", 90, 700),
    ("oats", "Rolled oats", "42 oz canisters, 12 per case", "cases", 110, 330),
    ("chicken", "Frozen chicken thighs", "40 lb cases, keep frozen", "cases", 70, 120),
    ("tomatoes", "Canned tomatoes", "28 oz cans, 12 per case", "cases", 160, 600),
    ("diapers", "Diapers, size 3", "4 packs of 32 per case", "cases", 45, None),
    ("hygiene", "Hygiene kits", "Soap, toothpaste, toothbrush and shampoo", "kits", 200, None),
]


@dataclass
class _Seeder:
    db: AsyncSession
    admin: Actor
    changes: Changes = field(default_factory=Changes)
    orgs: dict[str, int] = field(default_factory=dict)
    members: dict[str, Actor] = field(default_factory=dict)
    people: dict[str, Actor] = field(default_factory=dict)
    lots: dict[str, int] = field(default_factory=dict)

    async def round(
        self,
        name: str,
        description: str,
        strategy: str,
        offer: list[tuple[str, int]],
        requests: list[tuple[str, str, int]],
        *,
        deadline: datetime,
        open_round: bool = True,
        ineligible: tuple[str, str] | None = None,
        close: bool = False,
        finalize: bool = False,
    ) -> int:
        rnd = await rounds.create_round(
            self.db,
            self.admin,
            self.changes,
            name=name,
            description=description,
            strategy=strategy,
            request_deadline=deadline,
        )
        for key, qty in offer:
            await rounds.reserve_lot(
                self.db, self.admin, self.changes, rnd.id, lot_id=self.lots[key], quantity=qty
            )
        if not open_round:
            return rnd.id
        await rounds.open_round(self.db, self.admin, self.changes, rnd.id, version=None)
        for slug, key, qty in requests:
            await request_service.create_request(
                self.db,
                self.members[slug],
                self.changes,
                rnd.id,
                lot_id=self.lots[key],
                quantity=qty,
            )
        if ineligible:
            slug, reason = ineligible
            org = next(o for o in ORGS if o[0] == slug)
            await rounds.set_participant(
                self.db,
                self.admin,
                self.changes,
                rnd.id,
                self.orgs[slug],
                eligible=False,
                weight=org[2],
                priority=org[3],
                reason=reason,
            )
        if close or finalize:
            await rounds.close_round(self.db, self.admin, self.changes, rnd.id, version=None)
        if finalize:
            await allocation.finalize(self.db, self.admin, self.changes, rnd.id)
        return rnd.id


async def seed(db: AsyncSession) -> dict[str, str]:
    now = datetime.now(UTC)
    owner = User(
        email="admin@example.org",
        display_name="Avery Thompson",
        password_hash=hash_password(DEMO_PASSWORD),
        role="admin",
        is_active=True,
        version=1,
    )
    db.add(owner)
    await db.flush()
    s = _Seeder(db, Actor(owner.id, "admin"))

    s.people[owner.email] = s.admin
    for slug, name, weight, tier, region in ORGS:
        org = await directory.create_organization(
            db,
            s.admin,
            s.changes,
            name=name,
            default_weight=weight,
            default_priority=tier,
            region=region,
        )
        s.orgs[slug] = org.id
    for email, display, kind in PEOPLE:
        role = kind if kind in ("admin", "viewer") else "member"
        user = await directory.create_user(
            db,
            s.admin,
            s.changes,
            email=email,
            display_name=display,
            password=DEMO_PASSWORD,
            role=role,
            organization_id=s.orgs.get(kind),
        )
        s.people[email] = Actor(user.id, role, s.orgs.get(kind))
        if role == "member" and kind not in s.members:
            s.members[kind] = s.people[email]
    former = await directory.create_user(
        db,
        s.admin,
        s.changes,
        email="l.fischer@example.org",
        display_name="Lena Fischer",
        password=DEMO_PASSWORD,
        role="member",
        organization_id=s.orgs["eastside"],
    )
    await directory.update_user(
        db,
        s.admin,
        s.changes,
        former.id,
        version=1,
        is_active=False,
        reason="Left Eastside Food Shelf",
    )

    today = date.today()
    for key, name, desc, unit, qty, days in LOTS:
        lot = await inventory.create_lot(
            db,
            s.admin,
            s.changes,
            name=name,
            description=desc,
            unit=unit,
            quantity_on_hand=qty,
            best_before=today + timedelta(days=days) if days else None,
        )
        s.lots[key] = lot.id

    later = now + timedelta(days=7)
    history: list[tuple[int, float, bool]] = []  # (round id, days ago it ended, cancelled)

    rid = await s.round(
        "Week 38 pantry staples",
        "Shelf-stable staples from the regional grocery drive.",
        "fcfs",
        [("beans", 80), ("rice", 60)],
        [
            ("harbor", "beans", 30),
            ("northside", "beans", 25),
            ("lakeside", "rice", 15),
            ("eastside", "beans", 20),
            ("riverside", "rice", 20),
            ("maple", "beans", 20),
            ("southview", "rice", 25),
            ("westgate", "rice", 15),
        ],
        deadline=later,
        finalize=True,
    )
    history.append((rid, 42, False))
    rid = await s.round(
        "Week 40 protein drive",
        "Protein donated by two local distributors.",
        "priority",
        [("tuna", 40), ("chicken", 30)],
        [
            ("southview", "tuna", 20),
            ("harbor", "chicken", 20),
            ("maple", "tuna", 15),
            ("lakeside", "chicken", 10),
            ("eastside", "tuna", 12),
            ("northside", "chicken", 15),
            ("westgate", "tuna", 10),
        ],
        deadline=later,
        finalize=True,
    )
    history.append((rid, 28, False))
    rid = await s.round(
        "Week 41 produce share",
        "Fresh produce; please request only what you can distribute within a week.",
        "need_weighted",
        [("apples", 50), ("carrots", 40)],
        [
            ("northside", "apples", 20),
            ("southview", "carrots", 15),
            ("riverside", "apples", 15),
            ("harbor", "apples", 20),
            ("eastside", "carrots", 10),
            ("lakeside", "apples", 10),
            ("westgate", "carrots", 20),
            ("maple", "apples", 15),
            ("maple", "carrots", 10),
        ],
        deadline=later,
        finalize=True,
    )
    history.append((rid, 21, False))

    # Identical demand under each strategy: 60 cases, three requests of 40.
    for strategy, label in (
        ("fcfs", "first come, first served"),
        ("need_weighted", "need-weighted"),
        ("priority", "priority tiers"),
    ):
        rid = await s.round(
            f"Comparison: beans by {label}",
            "Same 60 cases and the same three requests under each strategy.",
            strategy,
            [("beans", 60)],
            [("eastside", "beans", 40), ("riverside", "beans", 40), ("northside", "beans", 40)],
            deadline=later,
            finalize=True,
        )
        history.append((rid, 14, False))

    rid = await s.round(
        "Week 43 holiday boxes",
        "Holiday meal boxes.",
        "fcfs",
        [("tomatoes", 40)],
        [("eastside", "tomatoes", 10), ("harbor", "tomatoes", 15)],
        deadline=later,
    )
    await rounds.cancel_round(
        db, s.admin, s.changes, rid, reason="Donor shipment delayed; stock returned to inventory"
    )
    history.append((rid, 9, True))

    closed = await s.round(
        "Week 44 produce and grains",
        "Closed: preview, compare strategies, then finalize.",
        "need_weighted",
        [("rice", 100), ("apples", 60)],
        [
            ("westgate", "rice", 50),
            ("westgate", "apples", 20),
            ("northside", "rice", 60),
            ("northside", "apples", 30),
            ("eastside", "rice", 30),
            ("eastside", "apples", 25),
            ("harbor", "rice", 25),
            ("riverside", "rice", 20),
            ("riverside", "apples", 10),
            ("lakeside", "apples", 12),
        ],
        deadline=later,
        ineligible=("westgate", "Storage inspection pending"),
        close=True,
    )

    staples = await s.round(
        "Week 45 family staples",
        "Baby care and family staples. Infant formula is reserved for organizations "
        "serving families with infants.",
        "priority",
        [("formula", 40), ("pb", 80), ("milk", 60), ("diapers", 30)],
        [
            ("eastside", "pb", 30),
            ("riverside", "formula", 15),
            ("lakeside", "milk", 20),
            ("maple", "diapers", 12),
            ("southview", "pb", 25),
            ("harbor", "milk", 25),
            ("maple", "formula", 10),
        ],
        deadline=now + timedelta(days=5),
    )
    restock = await s.round(
        "Week 46 pantry restock",
        "Dry goods from the fall food drive.",
        "fcfs",
        [("pasta", 100), ("oats", 60), ("tomatoes", 80)],
        [
            ("harbor", "pasta", 40),
            ("northside", "oats", 20),
            ("westgate", "pasta", 30),
            ("eastside", "tomatoes", 25),
            ("southview", "oats", 15),
        ],
        deadline=now + timedelta(days=3),
    )
    draft = await s.round(
        "Week 47 hygiene and baby care",
        "Draft: not visible to members yet.",
        "need_weighted",
        [("hygiene", 150)],
        [],
        deadline=now + timedelta(days=14),
        open_round=False,
    )
    await db.flush()

    await _retime_directory(db, now - timedelta(days=80))
    for round_id, ended, cancelled in history:
        end = now - timedelta(days=ended)
        await _retime_round(
            db,
            round_id,
            created=end - timedelta(days=8),
            opened=end - timedelta(days=7),
            deadline=end - timedelta(hours=3),
            closed=None if cancelled else end - timedelta(hours=2),
            allocated=None if cancelled else end,
            cancelled=end if cancelled else None,
        )
    await _retime_round(
        db,
        closed,
        created=now - timedelta(days=5),
        opened=now - timedelta(days=4),
        deadline=now - timedelta(hours=7),
        closed=now - timedelta(hours=6),
    )
    await _retime_round(
        db, staples, created=now - timedelta(days=3), opened=now - timedelta(days=2)
    )
    await _retime_round(
        db, restock, created=now - timedelta(days=1, hours=6), opened=now - timedelta(days=1)
    )
    await _retime_round(
        db, draft, created=now - timedelta(hours=5), opened=now - timedelta(hours=5)
    )
    await seed_conversations(db, s.people, now)
    await seed_planning(db, s.people, now)
    await db.commit()
    return {
        "admin": "admin@example.org",
        "member": "northside@example.org",
        "viewer": "viewer@example.org",
        "password": DEMO_PASSWORD,
    }


async def _retime_directory(db: AsyncSession, start: datetime) -> None:
    params = {"start": start}
    await db.execute(
        text("UPDATE organizations SET created_at = :start, updated_at = :start"), params
    )
    await db.execute(text("UPDATE users SET created_at = :start, updated_at = :start"), params)
    await db.execute(
        text("UPDATE inventory_lots SET created_at = :start, updated_at = :start"), params
    )
    await db.execute(
        text(
            "UPDATE audit_log SET created_at = :start + (id * interval '3 minutes') "
            "WHERE round_id IS NULL"
        ),
        params,
    )


# Optional timestamps are bound as NULL, so they need an explicit type.
_RETIME_ROUND = text("""
    UPDATE rounds SET
        created_at = :created,
        opened_at = CASE WHEN opened_at IS NULL THEN NULL ELSE :opened END,
        request_deadline = COALESCE(CAST(:deadline AS timestamptz), request_deadline),
        closed_at = CAST(:closed AS timestamptz),
        allocated_at = CAST(:allocated AS timestamptz),
        cancelled_at = CAST(:cancelled AS timestamptz),
        updated_at = COALESCE(CAST(:allocated AS timestamptz), CAST(:cancelled AS timestamptz),
                              CAST(:closed AS timestamptz), :opened)
    WHERE id = :id""")

# Requests arrive in accepted order, spread over the first day of the round.
_RETIME_REQUESTS = text("""
    UPDATE allocation_requests SET
        created_at = :opened + accepted_seq * interval '37 minutes',
        updated_at = :opened + accepted_seq * interval '37 minutes',
        withdrawn_at = CASE WHEN withdrawn_at IS NULL THEN NULL
            ELSE :opened + accepted_seq * interval '37 minutes' + interval '25 minutes' END
    WHERE round_id = :id""")

_RETIME_ROUND_LOTS = text("""
    UPDATE round_lots SET released_at = CASE status
        WHEN 'consumed' THEN CAST(:allocated AS timestamptz)
        WHEN 'released' THEN CAST(:cancelled AS timestamptz)
        ELSE NULL END
    WHERE round_id = :id""")

_RETIME_SNAPSHOT = text("""
    UPDATE round_snapshots SET
        created_at = COALESCE(CAST(:closed AS timestamptz), created_at),
        finalized_at = CASE WHEN finalized_at IS NULL THEN NULL
            ELSE CAST(:allocated AS timestamptz) END
    WHERE round_id = :id""")

_RETIME_PARTICIPANTS = text(
    "UPDATE round_participants SET updated_at = :created WHERE round_id = :id"
)

_RETIME_AUDIT = text("""
    UPDATE audit_log a SET created_at = CASE
        WHEN a.action = 'request.create' THEN
            (SELECT r.created_at FROM allocation_requests r WHERE r.id = a.target_id::bigint)
        WHEN a.action LIKE 'request.%' THEN
            (SELECT r.updated_at FROM allocation_requests r WHERE r.id = a.target_id::bigint)
        WHEN a.action = 'round.open' THEN :opened
        WHEN a.action = 'round.participant_set' THEN
            COALESCE(CAST(:closed AS timestamptz), :opened) - interval '1 hour'
        WHEN a.action = 'round.close' THEN CAST(:closed AS timestamptz)
        WHEN a.action = 'round.finalize' THEN CAST(:allocated AS timestamptz)
        WHEN a.action = 'round.cancel' THEN CAST(:cancelled AS timestamptz)
        ELSE :created + (a.id % 60) * interval '1 minute'
    END
    WHERE a.round_id = :id""")


async def _retime_round(
    db: AsyncSession,
    round_id: int,
    *,
    created: datetime,
    opened: datetime,
    deadline: datetime | None = None,
    closed: datetime | None = None,
    allocated: datetime | None = None,
    cancelled: datetime | None = None,
) -> None:
    """Move one round's timeline into the past, keeping its internal order."""
    params = {
        "id": round_id,
        "created": created,
        "opened": opened,
        "deadline": deadline,
        "closed": closed,
        "allocated": allocated,
        "cancelled": cancelled,
    }
    for statement in (
        _RETIME_ROUND,
        _RETIME_REQUESTS,
        _RETIME_ROUND_LOTS,
        _RETIME_SNAPSHOT,
        _RETIME_PARTICIPANTS,
        _RETIME_AUDIT,
    ):
        await db.execute(statement, params)
