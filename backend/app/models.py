"""ORM models. Constraints here are the last line of defence for domain invariants;
the migration in alembic/versions is the authoritative DDL."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("admin", "member", "viewer")
ROUND_STATUSES = ("draft", "open", "closed", "allocated", "cancelled")
STRATEGIES = ("fcfs", "need_weighted", "priority")
ROUND_LOT_STATUSES = ("reserved", "released", "consumed")
REQUEST_STATUSES = ("active", "withdrawn")
CHANNEL_KINDS = ("global", "local", "group", "direct")
MESSAGE_KINDS = ("user", "system")
DOCUMENT_KINDS = ("upload", "note")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Organization(Timestamped, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 2 AND 120", name="name_length"),
        CheckConstraint("default_weight BETWEEN 1 AND 1000", name="default_weight_range"),
        CheckConstraint("default_priority BETWEEN 1 AND 10", name="default_priority_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    default_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    default_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # Organizations in the same region share a local chat channel.
    region: Mapped[str | None] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class User(Timestamped, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", ROLES), name="role_valid"),
        CheckConstraint(
            "(role = 'member') = (organization_id IS NOT NULL)", name="member_has_organization"
        ),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("organizations.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginFailure(Base):
    __tablename__ = "login_failures"
    __table_args__ = (Index("ix_login_failures_email_time", "email", "attempted_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InventoryLot(Timestamped, Base):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        CheckConstraint("quantity_on_hand >= 0", name="on_hand_nonnegative"),
        CheckConstraint("quantity_reserved >= 0", name="reserved_nonnegative"),
        CheckConstraint("quantity_reserved <= quantity_on_hand", name="reserved_within_on_hand"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="name_length"),
        CheckConstraint("char_length(unit) BETWEEN 1 AND 30", name="unit_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_before: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Round(Timestamped, Base):
    __tablename__ = "rounds"
    __table_args__ = (
        CheckConstraint(_in("status", ROUND_STATUSES), name="status_valid"),
        CheckConstraint(_in("strategy", STRATEGIES), name="strategy_valid"),
        CheckConstraint("char_length(name) BETWEEN 2 AND 120", name="name_length"),
        CheckConstraint("next_request_seq >= 1", name="next_seq_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    request_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_request_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    # version: every change (admin view). public_version: changes visible to everyone.
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    public_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allocated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoundLot(Base):
    __tablename__ = "round_lots"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(_in("status", ROUND_LOT_STATUSES), name="status_valid"),
        CheckConstraint(
            "(status = 'consumed') = (allocated_quantity IS NOT NULL)", name="consumed_has_outcome"
        ),
        CheckConstraint(
            "allocated_quantity IS NULL OR (allocated_quantity BETWEEN 0 AND quantity)",
            name="allocated_within_reserved",
        ),
        # A lot can be committed to at most one active round.
        Index(
            "uq_round_lots_active_lot",
            "lot_id",
            unique=True,
            postgresql_where=text("status = 'reserved'"),
        ),
    )

    round_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rounds.id"), primary_key=True)
    lot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("inventory_lots.id"), primary_key=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    allocated_quantity: Mapped[int | None] = mapped_column(Integer)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoundParticipant(Base):
    __tablename__ = "round_participants"
    __table_args__ = (
        CheckConstraint("weight BETWEEN 1 AND 1000", name="weight_range"),
        CheckConstraint("priority BETWEEN 1 AND 10", name="priority_range"),
    )

    round_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rounds.id"), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id"), primary_key=True
    )
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    # Bumped on any change visible to this organization in this round (its requests,
    # its eligibility). Lets members detect gaps without seeing other orgs' activity.
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AllocationRequest(Timestamped, Base):
    __tablename__ = "allocation_requests"
    __table_args__ = (
        CheckConstraint("quantity BETWEEN 1 AND 1000000", name="quantity_range"),
        CheckConstraint(_in("status", REQUEST_STATUSES), name="status_valid"),
        CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL)", name="withdrawn_has_time"
        ),
        ForeignKeyConstraint(["round_id", "lot_id"], ["round_lots.round_id", "round_lots.lot_id"]),
        ForeignKeyConstraint(
            ["round_id", "organization_id"],
            ["round_participants.round_id", "round_participants.organization_id"],
        ),
        UniqueConstraint("round_id", "accepted_seq", name="uq_requests_round_seq"),
        Index(
            "uq_requests_active",
            "round_id",
            "lot_id",
            "organization_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_requests_org", "organization_id", "round_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    round_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    accepted_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoundSnapshot(Base):
    __tablename__ = "round_snapshots"
    __table_args__ = (CheckConstraint(_in("strategy", STRATEGIES), name="strategy_valid"),)

    round_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rounds.id"), primary_key=True)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    result_hash: Mapped[str | None] = mapped_column(String(64))
    finalized_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AllocationResult(Base):
    __tablename__ = "allocation_results"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="requested_positive"),
        CheckConstraint(
            "allocated_quantity BETWEEN 0 AND requested_quantity", name="allocated_within_request"
        ),
        ForeignKeyConstraint(["round_id", "lot_id"], ["round_lots.round_id", "round_lots.lot_id"]),
        Index("ix_results_round_org", "round_id", "organization_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    round_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("allocation_requests.id"), unique=True, nullable=False
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id"), nullable=False
    )
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "operation", "key", name="uq_idempotency_scope"),
        CheckConstraint("char_length(key) BETWEEN 8 AND 128", name="key_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_target", "target_type", "target_id"),
        Index("ix_audit_round", "round_id"),
        Index("ix_audit_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    round_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("rounds.id"))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))


# ---------------------------------------------------------------- messaging


class ChatChannel(Base):
    """A conversation. ``global`` and ``local`` channels have implicit membership
    (every admin and member, or the organizations of one region); ``group`` and
    ``direct`` channels list their members in ``chat_members``."""

    __tablename__ = "chat_channels"
    __table_args__ = (
        CheckConstraint(_in("kind", CHANNEL_KINDS), name="kind_valid"),
        CheckConstraint("(kind = 'local') = (region IS NOT NULL)", name="local_has_region"),
        CheckConstraint("(kind = 'direct') = (direct_key IS NOT NULL)", name="direct_has_key"),
        CheckConstraint("(kind = 'group') = (name IS NOT NULL)", name="group_has_name"),
        Index(
            "uq_chat_channels_global",
            "kind",
            unique=True,
            postgresql_where=text("kind = 'global'"),
        ),
        Index(
            "uq_chat_channels_local_region",
            "region",
            unique=True,
            postgresql_where=text("kind = 'local'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(60))
    # "<low user id>:<high user id>", so a pair has exactly one direct channel.
    direct_key: Mapped[str | None] = mapped_column(String(41), unique=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Bumped on every visible change (message, deletion, membership).
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)


class ChatMember(Base):
    __tablename__ = "chat_members"

    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_channels.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatReadState(Base):
    __tablename__ = "chat_read_states"

    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_channels.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    last_read_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(_in("kind", MESSAGE_KINDS), name="kind_valid"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 4000", name="body_length"),
        CheckConstraint("(kind = 'user') = (sender_id IS NOT NULL)", name="user_has_sender"),
        # A client retry with the same id returns the original message.
        UniqueConstraint("sender_id", "client_id", name="uq_chat_messages_client"),
        Index("ix_chat_messages_channel", "channel_id", "id"),
        Index("ix_chat_messages_sender_time", "sender_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_channels.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    sender_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))


# ---------------------------------------------------------------- planning


class PlanningProfile(Base):
    """A food bank's stated planning preferences (validated by schemas.Preferences)."""

    __tablename__ = "planning_profiles"

    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id"), primary_key=True
    )
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlanningDocument(Base):
    """An uploaded file or a typed note that informs planning. ``analysis`` holds what
    the parser extracted (format, table preview, needs, mentioned categories)."""

    __tablename__ = "planning_documents"
    __table_args__ = (
        CheckConstraint(_in("kind", DOCUMENT_KINDS), name="kind_valid"),
        CheckConstraint("(kind = 'upload') = (content IS NOT NULL)", name="upload_has_content"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title_length"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        Index("ix_planning_documents_org", "organization_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("organizations.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    # Original bytes; deferred so listings never load them.
    content: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
