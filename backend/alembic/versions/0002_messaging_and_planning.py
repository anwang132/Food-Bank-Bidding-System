"""messaging and planning

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27 13:15:49.554212

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Chat channels and messages, planning profiles and documents, org regions."""
    op.create_table(
        "chat_channels",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=True),
        sa.Column("region", sa.String(length=60), nullable=True),
        sa.Column("direct_key", sa.String(length=41), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "(kind = 'direct') = (direct_key IS NOT NULL)",
            name=op.f("ck_chat_channels_direct_has_key"),
        ),
        sa.CheckConstraint(
            "(kind = 'group') = (name IS NOT NULL)", name=op.f("ck_chat_channels_group_has_name")
        ),
        sa.CheckConstraint(
            "(kind = 'local') = (region IS NOT NULL)",
            name=op.f("ck_chat_channels_local_has_region"),
        ),
        sa.CheckConstraint(
            "kind IN ('global', 'local', 'group', 'direct')",
            name=op.f("ck_chat_channels_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_chat_channels_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_channels")),
        sa.UniqueConstraint("direct_key", name=op.f("uq_chat_channels_direct_key")),
    )
    op.create_index(
        "uq_chat_channels_global",
        "chat_channels",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'global'"),
    )
    op.create_index(
        "uq_chat_channels_local_region",
        "chat_channels",
        ["region"],
        unique=True,
        postgresql_where=sa.text("kind = 'local'"),
    )
    op.create_table(
        "planning_documents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'upload') = (content IS NOT NULL)",
            name=op.f("ck_planning_documents_upload_has_content"),
        ),
        sa.CheckConstraint(
            "kind IN ('upload', 'note')", name=op.f("ck_planning_documents_kind_valid")
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name=op.f("ck_planning_documents_title_length")
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_planning_documents_size_nonnegative")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_planning_documents_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_planning_documents_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planning_documents")),
    )
    op.create_index(
        "ix_planning_documents_org", "planning_documents", ["organization_id", "id"], unique=False
    )
    op.create_table(
        "planning_profiles",
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("preferences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_planning_profiles_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_planning_profiles_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("organization_id", name=op.f("pk_planning_profiles")),
    )
    op.create_table(
        "chat_members",
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["chat_channels.id"],
            name=op.f("fk_chat_members_channel_id_chat_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_chat_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", "user_id", name=op.f("pk_chat_members")),
    )
    op.create_index(op.f("ix_chat_members_user_id"), "chat_members", ["user_id"], unique=False)
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(kind = 'user') = (sender_id IS NOT NULL)",
            name=op.f("ck_chat_messages_user_has_sender"),
        ),
        sa.CheckConstraint("kind IN ('user', 'system')", name=op.f("ck_chat_messages_kind_valid")),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 4000", name=op.f("ck_chat_messages_body_length")
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["chat_channels.id"],
            name=op.f("fk_chat_messages_channel_id_chat_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"], ["users.id"], name=op.f("fk_chat_messages_deleted_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"], ["users.id"], name=op.f("fk_chat_messages_sender_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
        sa.UniqueConstraint("sender_id", "client_id", name="uq_chat_messages_client"),
    )
    op.create_index("ix_chat_messages_channel", "chat_messages", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_chat_messages_sender_time", "chat_messages", ["sender_id", "created_at"], unique=False
    )
    op.create_table(
        "chat_read_states",
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("last_read_message_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["chat_channels.id"],
            name=op.f("fk_chat_read_states_channel_id_chat_channels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_chat_read_states_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("channel_id", "user_id", name=op.f("pk_chat_read_states")),
    )
    op.add_column("organizations", sa.Column("region", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "region")
    op.drop_table("chat_read_states")
    op.drop_index("ix_chat_messages_sender_time", table_name="chat_messages")
    op.drop_index("ix_chat_messages_channel", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_chat_members_user_id"), table_name="chat_members")
    op.drop_table("chat_members")
    op.drop_table("planning_profiles")
    op.drop_index("ix_planning_documents_org", table_name="planning_documents")
    op.drop_table("planning_documents")
    op.drop_index(
        "uq_chat_channels_local_region",
        table_name="chat_channels",
        postgresql_where=sa.text("kind = 'local'"),
    )
    op.drop_index(
        "uq_chat_channels_global",
        table_name="chat_channels",
        postgresql_where=sa.text("kind = 'global'"),
    )
    op.drop_table("chat_channels")
