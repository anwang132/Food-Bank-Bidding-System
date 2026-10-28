"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-26 23:08:36.417077

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the full schema. Constraints mirror app/models.py."""
    op.create_table(
        "inventory_lots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=False),
        sa.Column("quantity_on_hand", sa.Integer(), nullable=False),
        sa.Column("quantity_reserved", sa.Integer(), nullable=False),
        sa.Column("best_before", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 120", name=op.f("ck_inventory_lots_name_length")
        ),
        sa.CheckConstraint(
            "char_length(unit) BETWEEN 1 AND 30", name=op.f("ck_inventory_lots_unit_length")
        ),
        sa.CheckConstraint(
            "quantity_on_hand >= 0", name=op.f("ck_inventory_lots_on_hand_nonnegative")
        ),
        sa.CheckConstraint(
            "quantity_reserved <= quantity_on_hand",
            name=op.f("ck_inventory_lots_reserved_within_on_hand"),
        ),
        sa.CheckConstraint(
            "quantity_reserved >= 0", name=op.f("ck_inventory_lots_reserved_nonnegative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_lots")),
    )
    op.create_table(
        "login_failures",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_failures")),
    )
    op.create_index(
        "ix_login_failures_email_time", "login_failures", ["email", "attempted_at"], unique=False
    )
    op.create_table(
        "organizations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("default_weight", sa.Integer(), nullable=False),
        sa.Column("default_priority", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 2 AND 120", name=op.f("ck_organizations_name_length")
        ),
        sa.CheckConstraint(
            "default_priority BETWEEN 1 AND 10",
            name=op.f("ck_organizations_default_priority_range"),
        ),
        sa.CheckConstraint(
            "default_weight BETWEEN 1 AND 1000", name=op.f("ck_organizations_default_weight_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("name", name=op.f("uq_organizations_name")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(role = 'member') = (organization_id IS NOT NULL)",
            name=op.f("ck_users_member_has_organization"),
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'member', 'viewer')", name=op.f("ck_users_role_valid")
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_users_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"], unique=False)
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(key) BETWEEN 8 AND 128", name=op.f("ck_idempotency_keys_key_length")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_idempotency_keys_actor_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("actor_user_id", "operation", "key", name="uq_idempotency_scope"),
    )
    op.create_table(
        "rounds",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("request_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_request_seq", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("public_version", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'open', 'closed', 'allocated', 'cancelled')",
            name=op.f("ck_rounds_status_valid"),
        ),
        sa.CheckConstraint(
            "strategy IN ('fcfs', 'need_weighted', 'priority')",
            name=op.f("ck_rounds_strategy_valid"),
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 2 AND 120", name=op.f("ck_rounds_name_length")
        ),
        sa.CheckConstraint("next_request_seq >= 1", name=op.f("ck_rounds_next_seq_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_rounds_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rounds")),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("round_id", sa.BigInteger(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_log_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["round_id"], ["rounds.id"], name=op.f("fk_audit_log_round_id_rounds")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_created", "audit_log", ["created_at"], unique=False)
    op.create_index("ix_audit_round", "audit_log", ["round_id"], unique=False)
    op.create_index("ix_audit_target", "audit_log", ["target_type", "target_id"], unique=False)
    op.create_table(
        "round_lots",
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("lot_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("allocated_quantity", sa.Integer(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'consumed') = (allocated_quantity IS NOT NULL)",
            name=op.f("ck_round_lots_consumed_has_outcome"),
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'released', 'consumed')",
            name=op.f("ck_round_lots_status_valid"),
        ),
        sa.CheckConstraint(
            "allocated_quantity IS NULL OR (allocated_quantity BETWEEN 0 AND quantity)",
            name=op.f("ck_round_lots_allocated_within_reserved"),
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_round_lots_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["lot_id"], ["inventory_lots.id"], name=op.f("fk_round_lots_lot_id_inventory_lots")
        ),
        sa.ForeignKeyConstraint(
            ["round_id"], ["rounds.id"], name=op.f("fk_round_lots_round_id_rounds")
        ),
        sa.PrimaryKeyConstraint("round_id", "lot_id", name=op.f("pk_round_lots")),
    )
    op.create_index(
        "uq_round_lots_active_lot",
        "round_lots",
        ["lot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'reserved'"),
    )
    op.create_table(
        "round_participants",
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority BETWEEN 1 AND 10", name=op.f("ck_round_participants_priority_range")
        ),
        sa.CheckConstraint(
            "weight BETWEEN 1 AND 1000", name=op.f("ck_round_participants_weight_range")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_round_participants_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["round_id"], ["rounds.id"], name=op.f("fk_round_participants_round_id_rounds")
        ),
        sa.PrimaryKeyConstraint("round_id", "organization_id", name=op.f("pk_round_participants")),
    )
    op.create_table(
        "round_snapshots",
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("strategy", sa.String(length=32), nullable=False),
        sa.Column("strategy_version", sa.Integer(), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("finalized_by", sa.BigInteger(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "strategy IN ('fcfs', 'need_weighted', 'priority')",
            name=op.f("ck_round_snapshots_strategy_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_round_snapshots_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["finalized_by"], ["users.id"], name=op.f("fk_round_snapshots_finalized_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["round_id"], ["rounds.id"], name=op.f("fk_round_snapshots_round_id_rounds")
        ),
        sa.PrimaryKeyConstraint("round_id", name=op.f("pk_round_snapshots")),
    )
    op.create_table(
        "allocation_requests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("lot_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("accepted_seq", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'withdrawn') = (withdrawn_at IS NOT NULL)",
            name=op.f("ck_allocation_requests_withdrawn_has_time"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'withdrawn')", name=op.f("ck_allocation_requests_status_valid")
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 1000000", name=op.f("ck_allocation_requests_quantity_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_allocation_requests_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_allocation_requests_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "lot_id"],
            ["round_lots.round_id", "round_lots.lot_id"],
            name=op.f("fk_allocation_requests_round_id_round_lots"),
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "organization_id"],
            ["round_participants.round_id", "round_participants.organization_id"],
            name=op.f("fk_allocation_requests_round_id_round_participants"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_allocation_requests_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_allocation_requests")),
        sa.UniqueConstraint("round_id", "accepted_seq", name="uq_requests_round_seq"),
    )
    op.create_index(
        "ix_requests_org", "allocation_requests", ["organization_id", "round_id"], unique=False
    )
    op.create_index(
        "uq_requests_active",
        "allocation_requests",
        ["round_id", "lot_id", "organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "allocation_results",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("round_id", sa.BigInteger(), nullable=False),
        sa.Column("lot_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("allocated_quantity", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "allocated_quantity BETWEEN 0 AND requested_quantity",
            name=op.f("ck_allocation_results_allocated_within_request"),
        ),
        sa.CheckConstraint(
            "requested_quantity > 0", name=op.f("ck_allocation_results_requested_positive")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_allocation_results_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["allocation_requests.id"],
            name=op.f("fk_allocation_results_request_id_allocation_requests"),
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "lot_id"],
            ["round_lots.round_id", "round_lots.lot_id"],
            name=op.f("fk_allocation_results_round_id_round_lots"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_allocation_results")),
        sa.UniqueConstraint("request_id", name=op.f("uq_allocation_results_request_id")),
    )
    op.create_index(
        "ix_results_round_org", "allocation_results", ["round_id", "organization_id"], unique=False
    )


def downgrade() -> None:
    """Drop everything (destroys all data)."""
    op.drop_index("ix_results_round_org", table_name="allocation_results")
    op.drop_table("allocation_results")
    op.drop_index(
        "uq_requests_active",
        table_name="allocation_requests",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index("ix_requests_org", table_name="allocation_requests")
    op.drop_table("allocation_requests")
    op.drop_table("round_snapshots")
    op.drop_table("round_participants")
    op.drop_index(
        "uq_round_lots_active_lot",
        table_name="round_lots",
        postgresql_where=sa.text("status = 'reserved'"),
    )
    op.drop_table("round_lots")
    op.drop_index("ix_audit_target", table_name="audit_log")
    op.drop_index("ix_audit_round", table_name="audit_log")
    op.drop_index("ix_audit_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("rounds")
    op.drop_table("idempotency_keys")
    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("organizations")
    op.drop_index("ix_login_failures_email_time", table_name="login_failures")
    op.drop_table("login_failures")
    op.drop_table("inventory_lots")
