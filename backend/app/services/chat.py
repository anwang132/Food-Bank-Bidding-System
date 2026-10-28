"""Messaging: direct messages, group chats, one network-wide channel and one local
channel per region (docs/DECISIONS.md D17).

Who can read a channel:

* viewers have no access to messaging at all;
* ``global``: every active admin and member;
* ``local``: members of organizations in that region, plus admins (moderation);
* ``group`` and ``direct``: the members listed in ``chat_members`` only.

A channel the actor cannot read is reported as not found, so channel ids do not
reveal which conversations exist. Every visible change bumps ``chat_channels.version``
and emits a ``chat`` event naming the users who may read the channel.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import ColumnElement, and_, delete, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Forbidden, InvalidState, NotFound, TooManyRequests, ValidationFailed
from app.models import (
    ChatChannel,
    ChatMember,
    ChatMessage,
    ChatReadState,
    Organization,
    User,
)
from app.schemas import ChannelOut, MessageOut, MessagePage, Person
from app.services.common import Actor, Changes, Event, audit, db_now

CHAT_ROLES = ("admin", "member")
MAX_GROUP_MEMBERS = 100
NETWORK_TITLE = "All food banks"


def require_chat(actor: Actor) -> None:
    if actor.role not in CHAT_ROLES:
        raise Forbidden("Messaging is available to food bank staff and administrators")


async def region_of(db: AsyncSession, actor: Actor) -> str | None:
    if actor.organization_id is None:
        return None
    return await db.scalar(
        select(Organization.region).where(Organization.id == actor.organization_id)
    )


def _readable(actor: Actor, region: str | None) -> ColumnElement[bool]:
    is_member = exists().where(
        ChatMember.channel_id == ChatChannel.id, ChatMember.user_id == actor.user_id
    )
    conditions: list[ColumnElement[bool]] = [ChatChannel.kind == "global", is_member]
    if actor.is_admin:
        conditions.append(ChatChannel.kind == "local")
    elif region is not None:
        conditions.append((ChatChannel.kind == "local") & (ChatChannel.region == region))
    return or_(*conditions)


# ---------------------------------------------------------------- provisioning


async def ensure_local_channel(db: AsyncSession, region: str) -> None:
    await db.execute(
        pg_insert(ChatChannel)
        .values(kind="local", region=region, version=1)
        .on_conflict_do_nothing(index_elements=["region"], index_where=text("kind = 'local'"))
    )


async def ensure_network_channels(db: AsyncSession, actor: Actor) -> None:
    """Create the global channel and the actor's local channel(s) if missing. Uses
    ON CONFLICT DO NOTHING against partial unique indexes, so concurrent first
    visits converge on one row each."""
    await db.execute(
        pg_insert(ChatChannel)
        .values(kind="global", version=1)
        .on_conflict_do_nothing(index_elements=["kind"], index_where=text("kind = 'global'"))
    )
    if actor.is_admin:
        regions = list(
            await db.scalars(
                select(Organization.region)
                .where(Organization.region.is_not(None), Organization.is_active)
                .distinct()
            )
        )
    else:
        region = await region_of(db, actor)
        regions = [region] if region else []
    for region_name in regions:
        if region_name:
            await ensure_local_channel(db, region_name)


# ---------------------------------------------------------------- reads


async def _readable_channel(
    db: AsyncSession, actor: Actor, channel_id: int, *, lock: bool = False
) -> ChatChannel:
    require_chat(actor)
    stmt = select(ChatChannel).where(
        ChatChannel.id == channel_id, _readable(actor, await region_of(db, actor))
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    channel = await db.scalar(stmt)
    if channel is None:
        raise NotFound("Conversation not found")
    return channel


async def _people(db: AsyncSession, user_ids: Iterable[int]) -> dict[int, Person]:
    ids = set(user_ids)
    if not ids:
        return {}
    rows = await db.execute(
        select(User.id, User.display_name, User.role, Organization.name)
        .outerjoin(Organization, Organization.id == User.organization_id)
        .where(User.id.in_(ids))
    )
    return {
        uid: Person(id=uid, display_name=name, role=role, organization_name=org)
        for uid, name, role, org in rows
    }


def _message_out(msg: ChatMessage, people: dict[int, Person]) -> MessageOut:
    deleted = msg.deleted_at is not None
    return MessageOut(
        id=msg.id,
        channel_id=msg.channel_id,
        kind=msg.kind,
        sender=people.get(msg.sender_id) if msg.sender_id is not None else None,
        body="" if deleted else msg.body,
        created_at=msg.created_at,
        deleted=deleted,
        client_id=msg.client_id,
    )


def _network_audience(region: str | None) -> ColumnElement[bool]:
    """Active users who can read the global channel (region None) or a local one."""
    active = User.is_active & User.role.in_(CHAT_ROLES)
    if region is None:
        return active
    in_region = User.organization_id.in_(
        select(Organization.id).where(Organization.region == region)
    )
    return active & ((User.role == "admin") | in_region)


async def recipients(db: AsyncSession, channel: ChatChannel) -> tuple[int, ...]:
    if channel.kind in ("group", "direct"):
        stmt = select(ChatMember.user_id).where(ChatMember.channel_id == channel.id)
    else:
        stmt = select(User.id).where(_network_audience(channel.region))
    return tuple(sorted(await db.scalars(stmt)))


async def list_channels(db: AsyncSession, actor: Actor) -> list[ChannelOut]:
    require_chat(actor)
    region = await region_of(db, actor)
    channels = list(
        await db.scalars(
            select(ChatChannel)
            .where(_readable(actor, region))
            # An empty direct conversation shows only for the person who opened it.
            .where(
                ~(
                    (ChatChannel.kind == "direct")
                    & (ChatChannel.version == 1)
                    & (ChatChannel.created_by != actor.user_id)
                )
            )
            .order_by(ChatChannel.last_activity_at.desc(), ChatChannel.id.desc())
        )
    )
    ids = [c.id for c in channels]
    if not ids:
        return []
    me = actor.user_id
    reads: dict[int, int] = {
        cid: last
        for cid, last in await db.execute(
            select(ChatReadState.channel_id, ChatReadState.last_read_message_id).where(
                ChatReadState.user_id == me, ChatReadState.channel_id.in_(ids)
            )
        )
    }
    unread: dict[int, int] = {
        cid: n
        for cid, n in await db.execute(
            select(ChatMessage.channel_id, func.count())
            .outerjoin(
                ChatReadState,
                and_(
                    ChatReadState.channel_id == ChatMessage.channel_id,
                    ChatReadState.user_id == me,
                ),
            )
            .where(
                ChatMessage.channel_id.in_(ids),
                ChatMessage.id > func.coalesce(ChatReadState.last_read_message_id, 0),
                ChatMessage.deleted_at.is_(None),
                or_(ChatMessage.sender_id.is_(None), ChatMessage.sender_id != me),
            )
            .group_by(ChatMessage.channel_id)
        )
    }
    last_messages = {
        m.channel_id: m
        for m in await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.channel_id.in_(ids))
            .order_by(ChatMessage.channel_id, ChatMessage.id.desc())
            .ext(distinct_on(ChatMessage.channel_id))
        )
    }
    members: dict[int, list[int]] = {}
    for cid, uid in await db.execute(
        select(ChatMember.channel_id, ChatMember.user_id)
        .where(ChatMember.channel_id.in_(ids))
        .order_by(ChatMember.joined_at, ChatMember.user_id)
    ):
        members.setdefault(cid, []).append(uid)
    people = await _people(
        db,
        [uid for uids in members.values() for uid in uids]
        + [m.sender_id for m in last_messages.values() if m.sender_id is not None],
    )
    audience_sizes: dict[str | None, int] = {}
    for c in channels:
        if c.kind in ("global", "local") and c.region not in audience_sizes:
            audience_sizes[c.region] = (
                await db.scalar(
                    select(func.count()).select_from(User).where(_network_audience(c.region))
                )
                or 0
            )

    out = []
    for c in channels:
        member_people = [people[u] for u in members.get(c.id, []) if u in people]
        if c.kind == "global":
            title, subtitle = NETWORK_TITLE, "Every food bank and the coordination team"
        elif c.kind == "local":
            title, subtitle = f"{c.region}", f"Food banks in the {c.region} region"
        elif c.kind == "group":
            title, subtitle = c.name or "Group", f"{len(member_people)} members"
        else:
            other = next((p for p in member_people if p.id != me), None)
            title = other.display_name if other else "Direct message"
            subtitle = (other.organization_name or "Administrator") if other else ""
        last = last_messages.get(c.id)
        out.append(
            ChannelOut(
                id=c.id,
                kind=c.kind,
                title=title,
                subtitle=subtitle,
                region=c.region,
                members=member_people if c.kind in ("group", "direct") else None,
                member_count=(
                    len(member_people)
                    if c.kind in ("group", "direct")
                    else audience_sizes.get(c.region, 0)
                ),
                last_message=_message_out(last, people) if last else None,
                last_activity_at=c.last_activity_at,
                last_read_message_id=reads.get(c.id, 0),
                unread_count=unread.get(c.id, 0),
                version=c.version,
            )
        )
    return out


async def list_messages(
    db: AsyncSession, actor: Actor, channel_id: int, *, before: int | None, limit: int
) -> MessagePage:
    await _readable_channel(db, actor, channel_id)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.channel_id == channel_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        stmt = stmt.where(ChatMessage.id < before)
    rows = list(await db.scalars(stmt))
    has_more = len(rows) > limit
    rows = rows[:limit][::-1]
    people = await _people(db, [m.sender_id for m in rows if m.sender_id is not None])
    return MessagePage(messages=[_message_out(m, people) for m in rows], has_more=has_more)


async def list_people(db: AsyncSession, actor: Actor) -> list[Person]:
    require_chat(actor)
    rows = await db.execute(
        select(User.id, User.display_name, User.role, Organization.name)
        .outerjoin(Organization, Organization.id == User.organization_id)
        .where(User.is_active, User.role.in_(CHAT_ROLES), User.id != actor.user_id)
        .order_by(User.display_name, User.id)
    )
    return [
        Person(id=uid, display_name=name, role=role, organization_name=org)
        for uid, name, role, org in rows
    ]


async def fingerprint(db: AsyncSession, actor: Actor) -> str | None:
    """Cheap summary of everything chat-related the actor can see. The client's
    periodic check compares it to detect missed notifications."""
    if actor.role not in CHAT_ROLES:
        return None
    region = await region_of(db, actor)
    count, versions = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(ChatChannel.version), 0)).where(
                _readable(actor, region)
            )
        )
    ).one()
    reads = await db.scalar(
        select(func.coalesce(func.sum(ChatReadState.last_read_message_id), 0)).where(
            ChatReadState.user_id == actor.user_id
        )
    )
    return f"{count}.{versions}.{reads}"


# ---------------------------------------------------------------- writes


async def _touch(db: AsyncSession, changes: Changes, channel: ChatChannel, event_type: str) -> None:
    channel.version += 1
    channel.last_activity_at = await db_now(db)
    changes.add(
        Event("chat", event_type, channel_id=channel.id, recipients=await recipients(db, channel))
    )


async def _system_message(db: AsyncSession, channel: ChatChannel, body: str) -> None:
    db.add(ChatMessage(channel_id=channel.id, kind="system", body=body[:4000]))
    await db.flush()


async def _set_read(db: AsyncSession, user_id: int, channel_id: int, message_id: int) -> None:
    stmt = pg_insert(ChatReadState).values(
        channel_id=channel_id, user_id=user_id, last_read_message_id=message_id
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["channel_id", "user_id"],
            set_={
                "last_read_message_id": func.greatest(
                    ChatReadState.last_read_message_id, stmt.excluded.last_read_message_id
                )
            },
        )
    )


async def _display_name(db: AsyncSession, user_id: int) -> str:
    return await db.scalar(select(User.display_name).where(User.id == user_id)) or "Someone"


async def _chat_users(db: AsyncSession, actor: Actor, user_ids: Iterable[int]) -> list[User]:
    ids = set(user_ids) - {actor.user_id}
    users = list(
        await db.scalars(
            select(User)
            .where(User.id.in_(ids), User.is_active, User.role.in_(CHAT_ROLES))
            .order_by(User.display_name, User.id)
        )
    )
    if len(users) != len(ids):
        raise ValidationFailed(
            "Some people cannot be messaged",
            details={"user_ids": sorted(ids - {u.id for u in users})},
        )
    return users


async def post_message(
    db: AsyncSession,
    actor: Actor,
    changes: Changes,
    channel_id: int,
    *,
    body: str,
    client_id: str | None,
    per_minute: int,
) -> tuple[ChatMessage, bool]:
    """Returns (message, created). A retry with the same ``client_id`` returns the
    original message instead of posting twice."""
    channel = await _readable_channel(db, actor, channel_id)
    if client_id is not None:
        previous = await _by_client_id(db, actor, client_id)
        if previous is not None:
            return _check_replay(previous, channel_id), False
    now = await db_now(db)
    recent = await db.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.sender_id == actor.user_id, ChatMessage.created_at > now - _MINUTE)
    )
    if (recent or 0) >= per_minute:
        raise TooManyRequests(
            "You're sending messages too quickly. Wait a moment and try again.",
            headers={"Retry-After": "60"},
        )
    # A concurrent retry blocks on the unique index, then inserts nothing.
    new_id = await db.scalar(
        pg_insert(ChatMessage)
        .values(
            channel_id=channel_id,
            kind="user",
            sender_id=actor.user_id,
            body=body,
            client_id=client_id,
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_chat_messages_client")
        .returning(ChatMessage.id)
    )
    if new_id is None:
        assert client_id is not None
        previous = await _by_client_id(db, actor, client_id)
        assert previous is not None
        return _check_replay(previous, channel_id), False
    channel = await _readable_channel(db, actor, channel_id, lock=True)
    await _touch(db, changes, channel, "chat.message")
    await _set_read(db, actor.user_id, channel_id, new_id)
    message = await db.get(ChatMessage, new_id)
    assert message is not None
    return message, True


_MINUTE = timedelta(minutes=1)


async def _by_client_id(db: AsyncSession, actor: Actor, client_id: str) -> ChatMessage | None:
    return await db.scalar(
        select(ChatMessage).where(
            ChatMessage.sender_id == actor.user_id, ChatMessage.client_id == client_id
        )
    )


def _check_replay(previous: ChatMessage, channel_id: int) -> ChatMessage:
    if previous.channel_id != channel_id:
        raise ValidationFailed(
            "This message id was already used in another conversation",
            code="CLIENT_ID_REUSED",
        )
    return previous


async def message_out(db: AsyncSession, message: ChatMessage) -> MessageOut:
    ids = [message.sender_id] if message.sender_id is not None else []
    return _message_out(message, await _people(db, ids))


async def delete_message(
    db: AsyncSession, actor: Actor, changes: Changes, message_id: int
) -> ChatMessage:
    """Senders can delete their own messages; admins can remove any message in a
    channel they can read (moderation, audited). Deleting twice is a no-op."""
    message = await db.get(ChatMessage, message_id)
    if message is None:
        raise NotFound("Message not found")
    channel = await _readable_channel(db, actor, message.channel_id, lock=True)
    if message.kind == "system":
        raise Forbidden("System messages cannot be deleted")
    own = message.sender_id == actor.user_id
    if not own and not actor.is_admin:
        raise Forbidden("You can only delete your own messages")
    if message.deleted_at is not None:
        return message
    message.deleted_at = await db_now(db)
    message.deleted_by = actor.user_id
    if not own:
        # The body is deliberately not copied into the audit log.
        audit(
            db,
            actor,
            "chat.message_moderate",
            "chat_message",
            message.id,
            before={"channel_id": channel.id, "sender_id": message.sender_id},
            reason="Removed by an administrator",
        )
    await _touch(db, changes, channel, "chat.message_deleted")
    return message


async def mark_read(
    db: AsyncSession, actor: Actor, changes: Changes, channel_id: int, message_id: int
) -> int:
    channel = await _readable_channel(db, actor, channel_id)
    latest = await db.scalar(
        select(func.max(ChatMessage.id)).where(
            ChatMessage.channel_id == channel_id, ChatMessage.id <= message_id
        )
    )
    if latest is None:
        return 0
    await _set_read(db, actor.user_id, channel_id, latest)
    # Lets the user's other tabs clear their unread badges.
    changes.add(Event("chat", "chat.read", channel_id=channel.id, recipients=(actor.user_id,)))
    return latest


async def open_direct(
    db: AsyncSession, actor: Actor, changes: Changes, other_user_id: int
) -> ChatChannel:
    require_chat(actor)
    if other_user_id == actor.user_id:
        raise ValidationFailed("You cannot start a conversation with yourself")
    await _chat_users(db, actor, [other_user_id])
    low, high = sorted((actor.user_id, other_user_id))
    key = f"{low}:{high}"
    await db.execute(
        pg_insert(ChatChannel)
        .values(kind="direct", direct_key=key, created_by=actor.user_id, version=1)
        .on_conflict_do_nothing(index_elements=["direct_key"])
    )
    channel = await db.scalar(select(ChatChannel).where(ChatChannel.direct_key == key))
    assert channel is not None
    await db.execute(
        pg_insert(ChatMember)
        .values([{"channel_id": channel.id, "user_id": uid} for uid in (low, high)])
        .on_conflict_do_nothing()
    )
    return channel


async def create_group(
    db: AsyncSession, actor: Actor, changes: Changes, *, name: str, member_ids: list[int]
) -> ChatChannel:
    require_chat(actor)
    others = await _chat_users(db, actor, member_ids)
    if len(others) + 1 > MAX_GROUP_MEMBERS:
        raise ValidationFailed(f"Groups can have at most {MAX_GROUP_MEMBERS} members")
    channel = ChatChannel(kind="group", name=name, created_by=actor.user_id, version=1)
    db.add(channel)
    await db.flush()
    db.add_all(
        ChatMember(channel_id=channel.id, user_id=uid)
        for uid in [actor.user_id, *(u.id for u in others)]
    )
    me = await _display_name(db, actor.user_id)
    await _system_message(
        db,
        channel,
        f"{me} created {name} with {_names([u.display_name for u in others])}",
    )
    await _touch(db, changes, channel, "chat.changed")
    return channel


async def add_members(
    db: AsyncSession, actor: Actor, changes: Changes, channel_id: int, user_ids: list[int]
) -> ChatChannel:
    channel = await _readable_channel(db, actor, channel_id, lock=True)
    if channel.kind != "group":
        raise InvalidState("People can only be added to group conversations")
    current = set(
        await db.scalars(select(ChatMember.user_id).where(ChatMember.channel_id == channel.id))
    )
    if actor.user_id not in current:
        raise Forbidden("Only members of this group can add people")
    new = await _chat_users(db, actor, set(user_ids) - current)
    if not new:
        return channel
    if len(current) + len(new) > MAX_GROUP_MEMBERS:
        raise ValidationFailed(f"Groups can have at most {MAX_GROUP_MEMBERS} members")
    db.add_all(ChatMember(channel_id=channel.id, user_id=u.id) for u in new)
    await db.flush()
    me = await _display_name(db, actor.user_id)
    await _system_message(db, channel, f"{me} added {_names([u.display_name for u in new])}")
    await _touch(db, changes, channel, "chat.changed")
    return channel


async def leave_group(db: AsyncSession, actor: Actor, changes: Changes, channel_id: int) -> None:
    channel = await _readable_channel(db, actor, channel_id, lock=True)
    if channel.kind != "group":
        raise InvalidState("You can only leave group conversations")
    audience = await recipients(db, channel)
    await db.execute(
        delete(ChatMember).where(
            ChatMember.channel_id == channel.id, ChatMember.user_id == actor.user_id
        )
    )
    me = await _display_name(db, actor.user_id)
    await _system_message(db, channel, f"{me} left the group")
    channel.version += 1
    channel.last_activity_at = await db_now(db)
    # Notify the leaver too, so their other tabs drop the conversation.
    changes.add(Event("chat", "chat.changed", channel_id=channel.id, recipients=audience))


def _names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"
