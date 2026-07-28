from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import ijson
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .config import Settings
from .models import Conversation, ImportRecord, Message, Participant, now_ts

StreamFactory = Callable[[], BinaryIO]

_PLACEHOLDERS = {
    "image": "[图片]",
    "emoji": "[表情]",
    "video": "[视频]",
    "video_thumb": "[视频封面]",
    "voice": "[语音]",
    "voip": "[通话]",
    "redpacket": "[红包]",
    "transfer": "[转账]",
    "location": "[位置]",
    "chathistory": "[合并聊天记录]",
}


class ImportValidationError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_item(stream_factory: StreamFactory, prefix: str, default: Any = None) -> Any:
    with stream_factory() as stream:
        return next(ijson.items(stream, prefix), default)


def _safe_text(value: Any, *, max_length: int = 1_000_000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > max_length:
        return text[:max_length] + "\n[内容过长，已截断]"
    return text


def _message_content(message: dict[str, Any], render_type: str) -> tuple[str, str, str, str]:
    content = _safe_text(message.get("content"))
    title = _safe_text(message.get("title"), max_length=100_000)
    file_name = _safe_text(message.get("fileName"), max_length=10_000)
    url = _safe_text(message.get("url"), max_length=100_000)
    if not content:
        content = title or file_name
    if not content:
        content = _PLACEHOLDERS.get(render_type.lower(), f"[{render_type or '未知消息'}]")
    return content, title, file_name, url


def _source_message_id(message: dict[str, Any], entry_key: str, index: int) -> str:
    explicit = _safe_text(message.get("id"), max_length=500)
    if explicit:
        return explicit
    server_id = _safe_text(message.get("serverId"), max_length=120)
    if server_id and server_id != "0":
        return f"server:{server_id}"
    local_id = _safe_text(message.get("localId"), max_length=120)
    timestamp = int(message.get("createTime") or message.get("timestamp") or 0)
    return f"{entry_key}:{local_id or index}:{timestamp}"


def _validate_json_payload(stream_factory: StreamFactory) -> tuple[int, dict[str, Any], str]:
    schema_version = _first_item(stream_factory, "schemaVersion")
    if int(schema_version or 0) != 1:
        raise ImportValidationError(f"不支持的 JSON schemaVersion：{schema_version!r}，当前仅支持 1。")
    conversation = _first_item(stream_factory, "conversation")
    if not isinstance(conversation, dict):
        raise ImportValidationError("JSON 缺少 conversation 对象。")
    account = _safe_text(_first_item(stream_factory, "account", ""), max_length=255)
    return 1, conversation, account


def _zip_message_entries(path: Path, settings: Settings) -> list[zipfile.ZipInfo]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImportValidationError("ZIP 文件损坏或无法读取。") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > settings.max_zip_entries:
            raise ImportValidationError("ZIP 文件条目过多，已拒绝导入。")
        total_size = 0
        selected: list[zipfile.ZipInfo] = []
        for info in infos:
            pure = PurePosixPath(info.filename.replace("\\", "/"))
            if pure.is_absolute() or ".." in pure.parts:
                raise ImportValidationError("ZIP 包含不安全的路径。")
            total_size += int(info.file_size)
            if total_size > settings.max_uncompressed_bytes:
                raise ImportValidationError("ZIP 解压后体积超过安全上限。")
            if info.compress_size > 0 and info.file_size / info.compress_size > 1_000:
                raise ImportValidationError("ZIP 中存在异常压缩比条目。")
            if not info.is_dir() and pure.name == "messages.json":
                selected.append(info)
        if not selected:
            raise ImportValidationError("ZIP 中没有找到 messages.json。")
        return selected


def _get_or_create_conversation(
    session: Session,
    *,
    account: str,
    external_username: str,
    display_name: str,
    is_group: bool,
    schema_version: int,
) -> Conversation:
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.account == account,
            Conversation.external_username == external_username,
        )
    )
    timestamp = now_ts()
    if conversation is None:
        conversation = Conversation(
            account=account,
            external_username=external_username,
            display_name=display_name,
            is_group=is_group,
            schema_version=schema_version,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(conversation)
        session.flush()
    else:
        conversation.display_name = display_name or conversation.display_name
        conversation.is_group = is_group
        conversation.schema_version = schema_version
        conversation.updated_at = timestamp
    return conversation


def _upsert_participant(
    session: Session,
    cache: dict[str, Participant],
    *,
    conversation_id: str,
    username: str,
    display_name: str,
    created_at: int,
) -> Participant:
    key = username or "__unknown__"
    participant = cache.get(key)
    if participant is None:
        participant = session.scalar(
            select(Participant).where(
                Participant.conversation_id == conversation_id,
                Participant.external_username == key,
            )
        )
    if participant is None:
        participant = Participant(
            conversation_id=conversation_id,
            external_username=key,
            display_name=display_name or key,
            message_count=0,
            first_message_at=created_at,
            last_message_at=created_at,
        )
        session.add(participant)
        session.flush()
    elif display_name and (not participant.display_name or participant.display_name == key):
        participant.display_name = display_name
    cache[key] = participant
    return participant


def _iter_messages(stream_factory: StreamFactory) -> Iterator[dict[str, Any]]:
    with stream_factory() as stream:
        for item in ijson.items(stream, "messages.item"):
            if isinstance(item, dict):
                yield item


def _import_conversation_stream(
    session: Session,
    *,
    stream_factory: StreamFactory,
    archive_hash: str,
    entry_key: str,
    entry_index: int,
) -> tuple[Conversation, int]:
    schema_version, metadata, account = _validate_json_payload(stream_factory)
    external_username = _safe_text(metadata.get("username"), max_length=512)
    display_name = _safe_text(metadata.get("displayName"), max_length=512) or "未命名会话"
    if not external_username:
        external_username = f"anonymous:{archive_hash[:16]}:{entry_index}"
    conversation = _get_or_create_conversation(
        session,
        account=account,
        external_username=external_username,
        display_name=display_name,
        is_group=bool(metadata.get("isGroup")),
        schema_version=schema_version,
    )
    participant_cache: dict[str, Participant] = {}
    imported_count = 0
    first_timestamp: int | None = None
    last_timestamp: int | None = None

    for index, raw in enumerate(_iter_messages(stream_factory), start=1):
        created_at = int(raw.get("createTime") or raw.get("timestamp") or 0)
        if created_at <= 0:
            continue
        render_type = _safe_text(raw.get("renderType") or raw.get("type"), max_length=80) or "unknown"
        content, title, file_name, url = _message_content(raw, render_type)
        sender_username = _safe_text(raw.get("senderUsername"), max_length=512) or "__unknown__"
        sender_name = _safe_text(raw.get("senderDisplayName"), max_length=512) or sender_username
        source_id = _source_message_id(raw, entry_key, index)
        values = {
            "id": __import__("uuid").uuid4().hex,
            "conversation_id": conversation.id,
            "source_message_id": source_id,
            "local_id": _safe_text(raw.get("localId"), max_length=128),
            "server_id": _safe_text(raw.get("serverId"), max_length=128),
            "quote_server_id": _safe_text(raw.get("quoteServerId"), max_length=128),
            "sender_username": sender_username,
            "sender_display_name": sender_name,
            "created_at": created_at,
            "render_type": render_type,
            "is_sent": bool(raw.get("isSent")),
            "content": content,
            "quote_content": _safe_text(raw.get("quoteContent")),
            "title": title,
            "url": url,
            "file_name": file_name,
        }
        result = session.execute(
            sqlite_insert(Message)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["conversation_id", "source_message_id"])
        )
        if result.rowcount:
            imported_count += 1
            participant = _upsert_participant(
                session,
                participant_cache,
                conversation_id=conversation.id,
                username=sender_username,
                display_name=sender_name,
                created_at=created_at,
            )
            participant.message_count += 1
            participant.first_message_at = min(participant.first_message_at or created_at, created_at)
            participant.last_message_at = max(participant.last_message_at or created_at, created_at)
        first_timestamp = created_at if first_timestamp is None else min(first_timestamp, created_at)
        last_timestamp = created_at if last_timestamp is None else max(last_timestamp, created_at)
        if index % 1_000 == 0:
            session.flush()

    if first_timestamp is not None:
        conversation.first_message_at = min(conversation.first_message_at or first_timestamp, first_timestamp)
        conversation.last_message_at = max(
            conversation.last_message_at or last_timestamp or 0,
            last_timestamp or 0,
        )
        conversation.updated_at = now_ts()
    return conversation, imported_count


def import_archive(
    session: Session,
    *,
    path: Path,
    source_type: str,
    filename: str,
    settings: Settings,
) -> dict[str, Any]:
    if not path.is_file():
        raise ImportValidationError("待导入文件不存在。")
    if path.stat().st_size > settings.max_upload_bytes:
        raise ImportValidationError("上传文件超过安全上限。")
    is_zip = zipfile.is_zipfile(path)
    if str(filename).lower().endswith(".zip") and not is_zip:
        raise ImportValidationError("ZIP 文件损坏或无法读取。")
    archive_hash = sha256_file(path)
    existing = session.scalar(select(ImportRecord).where(ImportRecord.archive_hash == archive_hash))
    if existing is not None and existing.status == "succeeded":
        details = json.loads(existing.details_json or "{}")
        return {**details, "duplicate": True, "import_id": existing.id}
    if existing is None:
        record = ImportRecord(
            source_type=source_type,
            filename=filename,
            archive_hash=archive_hash,
            status="running",
        )
        session.add(record)
        session.flush()
    else:
        record = existing
        record.status = "running"
        record.details_json = "{}"
        record.completed_at = None

    conversations: list[dict[str, Any]] = []
    total_messages = 0
    try:
        if is_zip:
            entries = _zip_message_entries(path, settings)
            with zipfile.ZipFile(path) as archive:
                for entry_index, info in enumerate(entries, start=1):
                    def stream_factory(info: zipfile.ZipInfo = info) -> BinaryIO:
                        return archive.open(info, "r")

                    conversation, count = _import_conversation_stream(
                        session,
                        stream_factory=stream_factory,
                        archive_hash=archive_hash,
                        entry_key=info.filename,
                        entry_index=entry_index,
                    )
                    if not record.account:
                        record.account = conversation.account
                    conversations.append(
                        {"id": conversation.id, "name": conversation.display_name, "messages_added": count}
                    )
                    total_messages += count
        else:
            def stream_factory() -> BinaryIO:
                return path.open("rb")

            conversation, count = _import_conversation_stream(
                session,
                stream_factory=stream_factory,
                archive_hash=archive_hash,
                entry_key=filename,
                entry_index=1,
            )
            record.account = conversation.account
            conversations.append(
                {"id": conversation.id, "name": conversation.display_name, "messages_added": count}
            )
            total_messages += count
        details = {
            "conversations": conversations,
            "conversation_count": len(conversations),
            "messages_added": total_messages,
        }
        record.status = "succeeded"
        record.schema_version = 1
        record.details_json = json.dumps(details, ensure_ascii=False)
        record.completed_at = now_ts()
        session.flush()
        return {**details, "duplicate": False, "import_id": record.id}
    except Exception:
        record.status = "failed"
        record.completed_at = now_ts()
        raise
