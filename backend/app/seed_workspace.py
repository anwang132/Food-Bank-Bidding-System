"""Demo conversations and planning workspaces, created after the round history in
app/seed.py. Channels, groups, preferences and documents go through the services;
message timestamps are written directly so the conversations read like weeks of
real use. All people and content are fictional."""

from __future__ import annotations

import mimetypes
from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatChannel, ChatMessage, ChatReadState, Organization
from app.schemas import Preferences
from app.services import chat, planning
from app.services.common import Actor, Changes

# Messages older than this are read by everyone in the conversation, as are a
# person's own messages; newer ones show up as unread.
READ_CUTOFF = timedelta(hours=8)

AVERY = "admin@example.org"
PRIYA = "priya.raman@example.org"
DANA = "northside@example.org"
MARIA = "m.alvarez@example.org"
JORDAN = "eastside@example.org"
SAM = "riverside@example.org"
CHRIS = "westgate@example.org"
TAYLOR = "harborpoint@example.org"
MORGAN = "lakeside@example.org"
RILEY = "southview@example.org"
JAMIE = "maplestreet@example.org"

GROUP = "Produce run — North Valley"

# conversation, hours ago, sender, body. Conversations: "global", a region name,
# "group", or a frozenset of two emails for a direct message.
CONVERSATIONS: list[tuple[object, float, str, str]] = [
    ("global", 26 * 24, AVERY,
     "Welcome to the network channel! Use it for anything that affects more than one "
     "food bank: surplus, transport, schedules."),
    ("global", 20 * 24, TAYLOR,
     "Harbor Point has a spare pallet jack if anyone needs one for the holiday rush. "
     "Pickup any weekday before 3pm."),
    ("global", 20 * 24 - 1, JAMIE, "We'd love to borrow it for two weeks. I'll message you."),
    (frozenset({AVERY, TAYLOR}), 20 * 24 - 2, AVERY,
     "Thanks for offering the pallet jack, Taylor. That's exactly what this channel is for."),
    (frozenset({AVERY, TAYLOR}), 20 * 24 - 2.5, TAYLOR, "Anytime!"),
    ("global", 9 * 24, PRIYA,
     "Heads up: Week 43 holiday boxes was cancelled because the donor shipment slipped. "
     "The stock went back to inventory and we'll reschedule."),
    ("South County", 8 * 24, SAM,
     "Riverside has a few families asking for halal protein. Does Southview have a "
     "supplier contact?"),
    ("South County", 8 * 24 - 3, RILEY, "We use Al-Noor Market on 5th. I'll send you the contact."),
    ("North Valley", 6 * 24, MORGAN,
     "North Valley folks: the county is offering a shared refrigerated van on Tuesdays. "
     "Worth splitting?"),
    ("North Valley", 6 * 24 - 2, DANA,
     "Northside is in. We could do the produce pickups for all three of us in one run."),
    ("North Valley", 5 * 24, JAMIE, "Maple Street too. I can drive the first week."),
    ("Bayfront", 4 * 24, CHRIS,
     "Westgate's walk-in cooler passed inspection this morning, so we should be eligible "
     "again from Week 46."),
    ("Bayfront", 4 * 24 - 1, TAYLOR, "Great news, congratulations!"),
    ("Bayfront", 3 * 24, JORDAN,
     "Eastside can lend our extra shelving if Westgate needs it for the new cooler."),
    ("global", 2 * 24, AVERY,
     "Week 45 family staples is open until Friday. Infant formula is limited, so please "
     "request it only for families with infants this month."),
    ("global", 2 * 24 - 0.7, RILEY,
     "Thanks Avery. Southview's youth program doesn't need formula, so we'll skip it."),
    (frozenset({DANA, MARIA}), 2 * 24, MARIA, "Did the intake survey numbers come in?"),
    (frozenset({DANA, MARIA}), 2 * 24 - 0.3, DANA,
     "Yes, I added them as a note in Planning. Formula and peanut butter are the big gaps "
     "this month."),
    ("global", 24, JORDAN,
     "Is anyone else seeing longer lines on Saturdays? We're thinking about adding a "
     "Thursday evening slot."),
    ("global", 20, MORGAN,
     "Yes. Our seniors prefer weekday mornings, but the Saturday crowd has doubled since "
     "September."),
    ("North Valley", 5, MORGAN,
     "The van is confirmed for next Tuesday at 9am. I've started a group for the details."),
    ("group", 4.9, MORGAN,
     "Plan: the van leaves Lakeside at 9:00, Northside at 9:30 and Maple Street at 10:00. "
     "About 20 minutes per stop."),
    (frozenset({AVERY, DANA}), 5, AVERY,
     "Hi Dana, Week 44 produce and grains closed this morning. I'll finalize it with "
     "need-weighted later today. Any last changes on your side?"),
    (frozenset({AVERY, DANA}), 4.5, DANA, "No changes from us. Thanks for checking!"),
    ("group", 4, MARIA,
     "I'll have the dock cleared at Northside. Dana is leading distribution that afternoon."),
    ("global", 3, AVERY,
     "Week 46 pantry restock is open too: pasta, oats and canned tomatoes. The deadline "
     "is in three days."),
    ("group", 1, JAMIE,
     "Works for us. Please bring back the blue crates from last time if you still have them."),
    (frozenset({AVERY, DANA}), 0.5, AVERY,
     "Also, try the planning assistant: it can draft your Week 46 request from the needs "
     "sheet you uploaded."),
]  # fmt: skip


async def seed_conversations(db: AsyncSession, people: dict[str, Actor], now: datetime) -> None:
    changes = Changes()  # demo data: no live clients to notify
    await chat.ensure_network_channels(db, people[AVERY])
    channels: dict[object, ChatChannel] = {}
    for channel in await db.scalars(select(ChatChannel)):
        channels["global" if channel.kind == "global" else channel.region] = channel
    group = await chat.create_group(
        db,
        people[MORGAN],
        changes,
        name=GROUP,
        member_ids=[people[DANA].user_id, people[MARIA].user_id, people[JAMIE].user_id],
    )
    channels["group"] = group
    for conversation, *_ in CONVERSATIONS:
        if isinstance(conversation, frozenset) and conversation not in channels:
            first, second = sorted(conversation)
            channels[conversation] = await chat.open_direct(
                db, people[first], changes, people[second].user_id
            )

    group_created = now - timedelta(hours=5)
    await db.execute(
        text("UPDATE chat_messages SET created_at = :at WHERE channel_id = :id"),
        {"at": group_created, "id": group.id},
    )
    for conversation, hours, sender, body in sorted(CONVERSATIONS, key=lambda m: -m[1]):
        db.add(
            ChatMessage(
                channel_id=channels[conversation].id,
                kind="user",
                sender_id=people[sender].user_id,
                body=body,
                created_at=now - timedelta(hours=hours),
            )
        )
        await db.flush()  # one at a time, so ids follow time

    opened = now - timedelta(days=80)
    for channel in {c.id: c for c in channels.values()}.values():
        count, first, last = (
            await db.execute(
                select(
                    func.count(), func.min(ChatMessage.created_at), func.max(ChatMessage.created_at)
                ).where(ChatMessage.channel_id == channel.id)
            )
        ).one()
        channel.version = 1 + count
        channel.created_at = (
            group_created
            if channel.id == group.id
            else (first if channel.kind == "direct" else opened)
        )
        channel.last_activity_at = last or channel.created_at
        await _mark_read(db, channel, now - READ_CUTOFF)


async def _mark_read(db: AsyncSession, channel: ChatChannel, cutoff: datetime) -> None:
    for user_id in await chat.recipients(db, channel):
        last = await db.scalar(
            select(func.max(ChatMessage.id)).where(
                ChatMessage.channel_id == channel.id,
                (ChatMessage.created_at <= cutoff) | (ChatMessage.sender_id == user_id),
            )
        )
        if last:
            db.add(ChatReadState(channel_id=channel.id, user_id=user_id, last_read_message_id=last))
    await db.flush()


# ---------------------------------------------------------------- planning

PROFILES: dict[str, Preferences] = {
    DANA: Preferences(
        households_served=1200,
        priority_categories=["protein", "produce", "baby"],
        dry_storage_capacity=250,
        cold_storage_capacity=60,
        frozen_storage_capacity=20,
        max_share_percent=40,
        dietary_notes="Many halal households. Prefer low-sodium canned goods.",
    ),
    MORGAN: Preferences(
        households_served=400,
        priority_categories=["dairy", "produce", "grains"],
        avoid_categories=["baby"],
        cold_storage_capacity=40,
        max_share_percent=30,
        dietary_notes="Seniors: soft foods, low-sodium and diabetic-friendly options.",
    ),
    TAYLOR: Preferences(
        households_served=900,
        priority_categories=["protein", "grains"],
        dry_storage_capacity=300,
        frozen_storage_capacity=40,
    ),
    JAMIE: Preferences(
        households_served=250,
        priority_categories=["baby", "hygiene", "protein"],
        max_share_percent=50,
    ),
}

INTAKE_NOTE = """Survey of 312 households at Saturday distribution.

Biggest gaps this month:
- Infant formula: 12 cases
- Peanut butter: 30 cases
- Shelf-stable milk: 20 cases

Several families asked for halal protein and low-sodium canned vegetables.
"""

NEEDS_CSV = b"""Item,Category,Cases needed,Notes
Whole wheat pasta,Grains,35,Weekend backpack program
Rolled oats,Grains,20,Breakfast program
Canned tomatoes,Canned,25,
Frozen chicken thighs,Protein,15,Only if freezer space allows
Fresh apples,Produce,25,School snack partnership
"""

STORAGE_MD = b"""# Storage and distribution notes

| Area | Capacity | Notes |
|---|---|---|
| Dry storage | 250 cases | Back room shelving |
| Walk-in cooler | 60 cases | Shared with Maple Street on Tuesdays |
| Chest freezers | 20 cases | Two units |

Distribution days are Tuesday and Saturday. Volunteers: 14 regulars.
"""

# author, kind, title or filename, content, days ago
DOCUMENTS: list[tuple[str, str, str, bytes, float]] = [
    (DANA, "upload", "northside-storage-notes.md", STORAGE_MD, 12),
    (MARIA, "note", "Intake survey: October", INTAKE_NOTE.encode(), 3),
    (DANA, "upload", "northside-needs-november.csv", NEEDS_CSV, 2),
]


async def seed_planning(db: AsyncSession, people: dict[str, Actor], now: datetime) -> None:
    for email, prefs in PROFILES.items():
        actor = people[email]
        org = await db.get(Organization, actor.organization_id)
        assert org is not None
        await planning.save_profile(db, actor, org, version=0, preferences=prefs)
    await db.execute(
        text("UPDATE planning_profiles SET updated_at = :at"), {"at": now - timedelta(days=12)}
    )

    for email, kind, title, content, days in DOCUMENTS:
        actor = people[email]
        org = await db.get(Organization, actor.organization_id)
        assert org is not None
        if kind == "note":
            doc = await planning.add_note(db, actor, org, title=title, body=content.decode())
        else:
            doc = await planning.upload_document(
                db,
                actor,
                org,
                filename=title,
                content_type=mimetypes.guess_type(title)[0],
                data=content,
            )
        await db.execute(
            text("UPDATE planning_documents SET created_at = :at WHERE id = :id"),
            {"at": now - timedelta(days=days), "id": doc.id},
        )
    # Planning audit entries take the time of the change they record.
    await db.execute(
        text("""
            UPDATE audit_log a SET created_at = d.created_at
            FROM planning_documents d
            WHERE a.target_type = 'planning_document' AND a.target_id = d.id::text"""),
    )
    await db.execute(
        text("""
            UPDATE audit_log a SET created_at = p.updated_at
            FROM planning_profiles p
            WHERE a.action = 'planning.preferences_update'
              AND a.target_id = p.organization_id::text"""),
    )
