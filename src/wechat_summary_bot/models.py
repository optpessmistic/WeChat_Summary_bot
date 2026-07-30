from __future__ import annotations

import time
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def now_ts() -> int:
    return int(time.time())


class Base(DeclarativeBase):
    pass


class ImportRecord(Base):
    __tablename__ = "imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_type: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(512), default="")
    archive_hash: Mapped[str] = mapped_column(String(64), unique=True)
    schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="running")
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[int] = mapped_column(Integer, default=now_ts)
    completed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("account", "external_username", name="uq_conversation_account_username"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account: Mapped[str] = mapped_column(String(255), default="")
    external_username: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(512))
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    first_message_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_message_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now_ts)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ts)

    participants: Mapped[list[Participant]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "external_username", name="uq_participant_conversation_username"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    external_username: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(512))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    first_message_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_message_at: Mapped[int | None] = mapped_column(Integer, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="participants")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "source_message_id", name="uq_message_conversation_source"
        ),
        Index("ix_messages_conversation_time", "conversation_id", "created_at"),
        Index(
            "ix_messages_conversation_sender_time",
            "conversation_id",
            "sender_username",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    source_message_id: Mapped[str] = mapped_column(String(512))
    local_id: Mapped[str] = mapped_column(String(128), default="")
    server_id: Mapped[str] = mapped_column(String(128), default="")
    quote_server_id: Mapped[str] = mapped_column(String(128), default="")
    sender_username: Mapped[str] = mapped_column(String(512), default="")
    sender_display_name: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[int] = mapped_column(Integer)
    render_type: Mapped[str] = mapped_column(String(80), default="unknown")
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[str] = mapped_column(Text, default="")
    quote_content: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    file_name: Mapped[str] = mapped_column(Text, default="")

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (Index("ix_analysis_jobs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_type: Mapped[str] = mapped_column(String(20))
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stage: Mapped[str] = mapped_column(String(120), default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=now_ts)
    started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("analysis_jobs.id", ondelete="CASCADE"), unique=True
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    report_json: Mapped[str] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[int] = mapped_column(Integer, default=now_ts)


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    definition_json: Mapped[str] = mapped_column(Text)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=now_ts)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ts)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30))
    base_url: Mapped[str] = mapped_column(String(1024))
    model: Mapped[str] = mapped_column(String(255))
    max_context_tokens: Mapped[int] = mapped_column(Integer, default=32_000)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(Integer, default=now_ts)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ts)


class SettingRecord(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[int] = mapped_column(Integer, default=now_ts)
