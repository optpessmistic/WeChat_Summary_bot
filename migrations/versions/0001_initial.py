"""Initial local library schema.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False, server_default=""),
        sa.Column("archive_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("schema_version", sa.Integer(), nullable=True),
        sa.Column("account", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.Integer(), nullable=True),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account", sa.String(255), nullable=False, server_default=""),
        sa.Column("external_username", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_message_at", sa.Integer(), nullable=True),
        sa.Column("last_message_at", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("account", "external_username", name="uq_conversation_account_username"),
    )
    op.create_table(
        "provider_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("base_url", sa.String(1024), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("max_context_tokens", sa.Integer(), nullable=False, server_default="32000"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "participants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_username", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_message_at", sa.Integer(), nullable=True),
        sa.Column("last_message_at", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "conversation_id",
            "external_username",
            name="uq_participant_conversation_username",
        ),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_message_id", sa.String(512), nullable=False),
        sa.Column("local_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("server_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("quote_server_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("sender_username", sa.String(512), nullable=False, server_default=""),
        sa.Column("sender_display_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("render_type", sa.String(80), nullable=False, server_default="unknown"),
        sa.Column("is_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("quote_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("file_name", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("conversation_id", "source_message_id", name="uq_message_conversation_source"),
    )
    op.create_index("ix_messages_conversation_time", "messages", ["conversation_id", "created_at"])
    op.create_index(
        "ix_messages_conversation_sender_time",
        "messages",
        ["conversation_id", "sender_username", "created_at"],
    )
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_type", sa.String(20), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(120), nullable=False, server_default=""),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_analysis_jobs_status_created", "analysis_jobs", ["status", "created_at"])
    op.create_table(
        "reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("analysis_jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_index("ix_analysis_jobs_status_created", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")
    op.drop_index("ix_messages_conversation_sender_time", table_name="messages")
    op.drop_index("ix_messages_conversation_time", table_name="messages")
    op.drop_table("messages")
    op.drop_table("participants")
    op.drop_table("settings")
    op.drop_table("provider_configs")
    op.drop_table("conversations")
    op.drop_table("imports")
