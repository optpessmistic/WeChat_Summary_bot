from __future__ import annotations

import json

import pytest

from wechat_summary_bot.ai import (
    AnalysisError,
    MessageSnapshot,
    chunk_messages,
    load_analysis_material,
)
from wechat_summary_bot.db import session_scope
from wechat_summary_bot.models import Conversation, Message, Participant


def test_member_mode_includes_three_neighbors_and_quoted_message(database):
    _engine, factory = database
    with session_scope(factory) as session:
        conversation = Conversation(
            account="wxid_me",
            external_username="team@chatroom",
            display_name="项目群",
            is_group=True,
        )
        session.add(conversation)
        session.flush()
        session.add(
            Participant(
                conversation_id=conversation.id,
                external_username="wxid_target",
                display_name="目标成员",
                message_count=1,
            )
        )
        for index in range(9):
            session.add(
                Message(
                    conversation_id=conversation.id,
                    source_message_id=f"message-{index}",
                    server_id=str(100 + index),
                    quote_server_id="100" if index == 4 else "",
                    sender_username="wxid_target" if index == 4 else f"wxid_{index}",
                    sender_display_name="目标成员" if index == 4 else f"成员{index}",
                    created_at=1_700_000_000 + index,
                    render_type="text",
                    content=f"消息 {index}",
                )
            )
        conversation_id = conversation.id

    with factory() as session:
        material = load_analysis_material(
            session,
            conversation_id=conversation_id,
            mode="member",
            member_username="wxid_target",
            start_time=None,
            end_time=None,
        )

    assert [message.server_id for message in material.selected_messages] == [
        str(100 + index) for index in range(8)
    ]
    assert material.participant_stats["target_message_count"] == 1
    assert material.participant_stats["context_message_count"] == 7


def test_oversized_message_remains_valid_json_after_truncation():
    message = MessageSnapshot(
        id="message-1",
        source_message_id="source-1",
        server_id="1",
        quote_server_id="",
        sender_username="wxid_sender",
        sender_display_name="发送者",
        created_at=1_700_000_000,
        render_type="text",
        content="很长的内容" * 10_000,
        quote_content="",
        title="",
        url="",
        file_name="",
    )
    chunks, _tokens, limitations = chunk_messages(
        [message],
        token_budget=2_000,
        pseudonymizer=None,
    )
    parsed = json.loads(chunks[0][0])
    assert parsed["evidence_id"] == "message-1"
    assert "正文已截断" in parsed["content"]
    assert limitations


def test_empty_time_range_is_rejected(database):
    _engine, factory = database
    with session_scope(factory) as session:
        conversation = Conversation(
            account="wxid_me",
            external_username="wxid_alice",
            display_name="Alice",
            is_group=False,
        )
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id
    with factory() as session, pytest.raises(AnalysisError, match="没有可分析"):
        load_analysis_material(
            session,
            conversation_id=conversation_id,
            mode="conversation",
            member_username=None,
            start_time=None,
            end_time=None,
        )
