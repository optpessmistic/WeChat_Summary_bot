from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from wechat_summary_bot.db import session_scope
from wechat_summary_bot.importer import ImportValidationError, import_archive
from wechat_summary_bot.models import Conversation, Message, Participant


def sample_payload(*, group: bool = True) -> dict:
    return {
        "schemaVersion": 1,
        "exportedAt": "2026-07-28T12:00:00",
        "account": "wxid_me",
        "conversation": {
            "username": "team@chatroom" if group else "wxid_alice",
            "displayName": "项目群" if group else "Alice",
            "isGroup": group,
        },
        "filters": {"startTime": None, "endTime": None, "messageTypes": None},
        "messages": [
            {
                "id": "message:1",
                "localId": 1,
                "serverId": 101,
                "createTime": 1785200400,
                "renderType": "text",
                "senderUsername": "wxid_alice",
                "senderDisplayName": "Alice",
                "content": "周五前完成原型。",
            },
            {
                "id": "message:2",
                "localId": 2,
                "serverId": 102,
                "createTime": 1785200460,
                "renderType": "quote",
                "senderUsername": "wxid_bob",
                "senderDisplayName": "Bob",
                "content": "收到，我负责评审。",
                "quoteServerId": 101,
                "quoteContent": "周五前完成原型。",
            },
            {
                "id": "message:3",
                "localId": 3,
                "serverId": 103,
                "createTime": 1785200520,
                "renderType": "image",
                "senderUsername": "wxid_bob",
                "senderDisplayName": "Bob",
                "content": "",
            },
        ],
    }


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_import_json_and_deduplicate(settings, database, tmp_path):
    _engine, factory = database
    source = write_json(tmp_path / "messages.json", sample_payload())
    with session_scope(factory) as session:
        first = import_archive(
            session, path=source, source_type="upload", filename=source.name, settings=settings
        )
    assert first["messages_added"] == 3
    assert not first["duplicate"]

    with session_scope(factory) as session:
        second = import_archive(
            session, path=source, source_type="upload", filename=source.name, settings=settings
        )
    assert second["duplicate"]

    with factory() as session:
        assert session.scalar(select(func.count(Conversation.id))) == 1
        assert session.scalar(select(func.count(Message.id))) == 3
        participants = list(session.scalars(select(Participant).order_by(Participant.display_name)))
        assert [(item.display_name, item.message_count) for item in participants] == [
            ("Alice", 1),
            ("Bob", 2),
        ]
        image = session.scalar(select(Message).where(Message.render_type == "image"))
        assert image is not None
        assert image.content == "[图片]"


def test_import_multiple_conversations_zip(settings, database, tmp_path):
    _engine, factory = database
    archive_path = tmp_path / "export.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("0001_group/messages.json", json.dumps(sample_payload(), ensure_ascii=False))
        archive.writestr(
            "0002_single/messages.json",
            json.dumps(sample_payload(group=False), ensure_ascii=False),
        )
    with session_scope(factory) as session:
        result = import_archive(
            session,
            path=archive_path,
            source_type="upload",
            filename=archive_path.name,
            settings=settings,
        )
    assert result["conversation_count"] == 2
    assert result["messages_added"] == 6


def test_rejects_zip_path_traversal(settings, database, tmp_path):
    _engine, factory = database
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../messages.json", json.dumps(sample_payload()))
    with pytest.raises(ImportValidationError, match="不安全"):
        with session_scope(factory) as session:
            import_archive(
                session,
                path=archive_path,
                source_type="upload",
                filename=archive_path.name,
                settings=settings,
            )


def test_rejects_unknown_schema(settings, database, tmp_path):
    _engine, factory = database
    payload = sample_payload()
    payload["schemaVersion"] = 99
    source = write_json(tmp_path / "messages.json", payload)
    with pytest.raises(ImportValidationError, match="schemaVersion"):
        with session_scope(factory) as session:
            import_archive(
                session, path=source, source_type="upload", filename=source.name, settings=settings
            )


def test_rejects_corrupt_zip(settings, database, tmp_path):
    _engine, factory = database
    source = tmp_path / "broken.zip"
    source.write_bytes(b"this is not a zip archive")
    with pytest.raises(ImportValidationError, match="损坏"):
        with session_scope(factory) as session:
            import_archive(
                session,
                path=source,
                source_type="upload",
                filename=source.name,
                settings=settings,
            )


def test_rejects_suspicious_compression_ratio(settings, database, tmp_path):
    _engine, factory = database
    source = tmp_path / "bomb.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("messages.json", b"0" * (2 * 1024 * 1024))
    with pytest.raises(ImportValidationError, match="压缩比"):
        with session_scope(factory) as session:
            import_archive(
                session,
                path=source,
                source_type="upload",
                filename=source.name,
                settings=settings,
            )
