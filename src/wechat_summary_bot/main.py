from __future__ import annotations

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .ai import (
    AnalysisError,
    OpenAICompatibleClient,
    Pseudonymizer,
    build_analysis_preview,
    load_analysis_material,
)
from .config import Settings, get_settings
from .db import create_database, session_scope
from .jobs import TERMINAL_STATUSES, JobManager, job_public
from .models import (
    AnalysisJob,
    Conversation,
    Message,
    Participant,
    ProviderConfig,
    Report,
    SettingRecord,
    WorkflowTemplate,
    new_id,
    now_ts,
)
from .security import SecretStore
from .upstream import UpstreamClient, UpstreamError, validate_loopback_url
from .workflows import WorkflowDefinition, builtin_workflows, default_workflow

SHANGHAI = ZoneInfo("Asia/Shanghai")
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]


class UpstreamSettingRequest(BaseModel):
    url: str


class UpstreamImportRequest(BaseModel):
    account: str | None = None
    username: str = Field(min_length=1, max_length=512)
    start_date: date | None = None
    end_date: date | None = None


class AnalysisRequest(BaseModel):
    conversation_id: str
    mode: Literal["conversation", "member"] = "conversation"
    member_username: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    focus: str = Field(default="", max_length=2_000)
    provider_id: str
    pseudonymize: bool = True
    workflow_id: str | None = Field(default=None, max_length=64)
    workflow: WorkflowDefinition | None = None


class WorkflowTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4_000)
    definition: WorkflowDefinition


class ProviderRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["openai_compatible", "ollama"]
    base_url: str = Field(min_length=5, max_length=1_024)
    model: str = Field(min_length=1, max_length=255)
    max_context_tokens: int = Field(default=32_000, ge=8_000, le=2_000_000)
    is_default: bool = False
    api_key: str | None = Field(default=None, max_length=10_000)


def _run_migrations(settings: Settings) -> None:
    config_path = PROJECT_ROOT / "alembic.ini"
    migrations_path = PROJECT_ROOT / "migrations"
    if not config_path.exists() or not migrations_path.exists():
        config_path = PACKAGE_DIR / "alembic.ini"
        migrations_path = PACKAGE_DIR / "migrations"
    if not config_path.exists() or not migrations_path.exists():
        raise RuntimeError("找不到 Alembic 迁移文件。")
    config = Config(str(config_path))
    config.set_main_option("script_location", str(migrations_path))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    config.attributes["database_url"] = settings.database_url
    command.upgrade(config, "head")


def _date_range(start_date: date | None, end_date: date | None) -> tuple[int | None, int | None]:
    if start_date is None and end_date is None:
        return None, None
    if start_date is not None and end_date is not None and end_date < start_date:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期。")
    start_ts = (
        int(datetime.combine(start_date, time.min, tzinfo=SHANGHAI).timestamp())
        if start_date
        else None
    )
    end_exclusive = (
        int(datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=SHANGHAI).timestamp())
        if end_date
        else None
    )
    return start_ts, end_exclusive


def _provider_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="AI Base URL 无效。")
    if parsed.scheme == "http":
        try:
            validate_loopback_url(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="非本机 AI 服务必须使用 HTTPS。"
            ) from exc
    return raw


def _get_setting(session: Any, key: str, default: str) -> str:
    row = session.get(SettingRecord, key)
    return row.value if row is not None else default


def _set_setting(session: Any, key: str, value: str) -> None:
    row = session.get(SettingRecord, key)
    if row is None:
        session.add(SettingRecord(key=key, value=value, updated_at=now_ts()))
    else:
        row.value = value
        row.updated_at = now_ts()


def _provider_public(provider: ProviderConfig, has_key: bool) -> dict[str, Any]:
    return {
        "id": provider.id,
        "name": provider.name,
        "kind": provider.kind,
        "base_url": provider.base_url,
        "model": provider.model,
        "max_context_tokens": provider.max_context_tokens,
        "is_default": provider.is_default,
        "has_api_key": has_key,
    }


def _workflow_public(template: WorkflowTemplate) -> dict[str, Any]:
    definition = WorkflowDefinition.model_validate_json(template.definition_json)
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "definition": definition.model_dump(mode="json"),
        "is_builtin": template.is_builtin,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def _seed_builtin_workflows(session: Any) -> None:
    timestamp = now_ts()
    for builtin in builtin_workflows():
        definition_json = builtin.definition.model_dump_json()
        template = session.get(WorkflowTemplate, builtin.id)
        if template is None:
            session.add(
                WorkflowTemplate(
                    id=builtin.id,
                    name=builtin.name,
                    description=builtin.description,
                    definition_json=definition_json,
                    is_builtin=True,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            continue
        if not template.is_builtin:
            raise RuntimeError(f"内置工作流 ID 被自定义模板占用：{builtin.id}")
        if (
            template.name != builtin.name
            or template.description != builtin.description
            or template.definition_json != definition_json
        ):
            template.name = builtin.name
            template.description = builtin.description
            template.definition_json = definition_json
            template.updated_at = timestamp


def _resolve_workflow(
    session: Any,
    *,
    workflow_id: str | None,
    inline: WorkflowDefinition | None,
) -> tuple[str | None, str, WorkflowDefinition]:
    selected_id = workflow_id
    template: WorkflowTemplate | None = None
    if selected_id:
        template = session.get(WorkflowTemplate, selected_id)
        if template is None:
            raise HTTPException(status_code=404, detail="工作流模板不存在。")

    if inline is not None:
        definition = inline.model_copy(deep=True)
        if template is None:
            return None, "临时自定义工作流", definition
        stored = WorkflowDefinition.model_validate_json(template.definition_json)
        name = template.name if stored == definition else f"{template.name}（已编辑）"
        return template.id, name, definition

    if template is not None:
        return (
            template.id,
            template.name,
            WorkflowDefinition.model_validate_json(template.definition_json),
        )

    default_id = "builtin-balanced"
    template = session.get(WorkflowTemplate, default_id)
    if template is not None:
        return (
            template.id,
            template.name,
            WorkflowDefinition.model_validate_json(template.definition_json),
        )
    return default_id, "均衡总结", default_workflow()


def _clean_workflow_template_body(
    body: WorkflowTemplateRequest,
) -> tuple[str, str, str]:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="工作流名称不能为空。")
    return name, body.description.strip(), body.definition.model_dump_json()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine, session_factory = create_database(settings)
    secret_store = SecretStore()
    manager = JobManager(
        settings=settings,
        session_factory=session_factory,
        secret_store=secret_store,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        settings.ensure_directories()
        _run_migrations(settings)
        manager.recover_interrupted_jobs()
        with session_scope(session_factory) as session:
            if session.scalar(select(func.count(ProviderConfig.id))) == 0:
                session.add_all(
                    [
                        ProviderConfig(
                            name="OpenAI 兼容云端",
                            kind="openai_compatible",
                            base_url="https://api.openai.com/v1",
                            model="gpt-4.1-mini",
                            max_context_tokens=32_000,
                            is_default=True,
                        ),
                        ProviderConfig(
                            name="Ollama 本地",
                            kind="ollama",
                            base_url="http://127.0.0.1:11434/v1",
                            model="qwen3:8b",
                            max_context_tokens=32_000,
                            is_default=False,
                        ),
                    ]
                )
            if session.get(SettingRecord, "upstream_url") is None:
                _set_setting(session, "upstream_url", settings.upstream_url)
            _seed_builtin_workflows(session)
        yield
        for task in list(manager.tasks.values()):
            task.cancel()
        if manager.tasks:
            await asyncio.gather(*manager.tasks.values(), return_exceptions=True)
        engine.dispose()

    app = FastAPI(
        title="微信脉络",
        description="本地优先的微信聊天 AI 总结工具",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "test"],
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.job_manager = manager
    app.state.secret_store = secret_store

    static_dir = PACKAGE_DIR / "static"
    template_dir = PACKAGE_DIR / "templates"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    templates = Jinja2Templates(directory=template_dir)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "wechat-summary-bot", "version": "0.2.0"}

    @app.get("/api/settings")
    async def get_app_settings() -> dict[str, Any]:
        with session_factory() as session:
            upstream_url = _get_setting(session, "upstream_url", settings.upstream_url)
            providers = list(session.scalars(select(ProviderConfig).order_by(ProviderConfig.created_at)))
        return {
            "upstream_url": upstream_url,
            "providers": [_provider_public(item, secret_store.has(item.id)) for item in providers],
        }

    @app.put("/api/settings/upstream")
    async def save_upstream_setting(body: UpstreamSettingRequest) -> dict[str, Any]:
        try:
            value = validate_loopback_url(body.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with session_scope(session_factory) as session:
            _set_setting(session, "upstream_url", value)
        return {"status": "success", "upstream_url": value}

    @app.get("/api/workflows")
    async def list_workflows() -> dict[str, Any]:
        with session_factory() as session:
            rows = list(
                session.scalars(
                    select(WorkflowTemplate).order_by(
                        WorkflowTemplate.is_builtin.desc(),
                        WorkflowTemplate.created_at,
                        WorkflowTemplate.name,
                    )
                )
            )
            builtin_order = {
                "builtin-fast": 0,
                "builtin-balanced": 1,
                "builtin-detailed": 2,
            }
            rows.sort(
                key=lambda item: (
                    0 if item.is_builtin else 1,
                    builtin_order.get(item.id, 99) if item.is_builtin else item.created_at,
                    item.name,
                )
            )
            workflows = [_workflow_public(item) for item in rows]
        return {"workflows": workflows}

    @app.post("/api/workflows", status_code=201)
    async def create_workflow(body: WorkflowTemplateRequest) -> dict[str, Any]:
        name, description, definition_json = _clean_workflow_template_body(body)
        with session_scope(session_factory) as session:
            template = WorkflowTemplate(
                id=new_id(),
                name=name,
                description=description,
                definition_json=definition_json,
                is_builtin=False,
            )
            session.add(template)
            session.flush()
            public = _workflow_public(template)
        return {"status": "success", "workflow": public}

    @app.put("/api/workflows/{workflow_id}")
    async def update_workflow(
        workflow_id: str,
        body: WorkflowTemplateRequest,
    ) -> dict[str, Any]:
        name, description, definition_json = _clean_workflow_template_body(body)
        with session_scope(session_factory) as session:
            template = session.get(WorkflowTemplate, workflow_id)
            if template is None:
                raise HTTPException(status_code=404, detail="工作流模板不存在。")
            if template.is_builtin:
                raise HTTPException(
                    status_code=403,
                    detail="内置工作流不可修改，请先复制为自定义模板。",
                )
            template.name = name
            template.description = description
            template.definition_json = definition_json
            template.updated_at = now_ts()
            session.flush()
            public = _workflow_public(template)
        return {"status": "success", "workflow": public}

    @app.post("/api/workflows/{workflow_id}/duplicate", status_code=201)
    async def duplicate_workflow(workflow_id: str) -> dict[str, Any]:
        with session_scope(session_factory) as session:
            source = session.get(WorkflowTemplate, workflow_id)
            if source is None:
                raise HTTPException(status_code=404, detail="工作流模板不存在。")
            template = WorkflowTemplate(
                id=new_id(),
                name=f"{source.name[:116]} 副本",
                description=source.description,
                definition_json=source.definition_json,
                is_builtin=False,
            )
            session.add(template)
            session.flush()
            public = _workflow_public(template)
        return {"status": "success", "workflow": public}

    @app.delete("/api/workflows/{workflow_id}")
    async def delete_workflow(workflow_id: str) -> dict[str, Any]:
        with session_scope(session_factory) as session:
            template = session.get(WorkflowTemplate, workflow_id)
            if template is None:
                raise HTTPException(status_code=404, detail="工作流模板不存在。")
            if template.is_builtin:
                raise HTTPException(
                    status_code=403,
                    detail="内置工作流不可删除。",
                )
            session.delete(template)
        return {"status": "success"}

    def current_upstream_url() -> str:
        with session_factory() as session:
            return _get_setting(session, "upstream_url", settings.upstream_url)

    @app.get("/api/upstream/accounts")
    async def upstream_accounts() -> dict[str, Any]:
        try:
            return await UpstreamClient(current_upstream_url(), settings).accounts()
        except (UpstreamError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/upstream/targets")
    async def upstream_targets(account: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return await UpstreamClient(current_upstream_url(), settings).targets(account)
        except (UpstreamError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/imports/upstream", status_code=202)
    async def create_upstream_import(body: UpstreamImportRequest) -> dict[str, Any]:
        start_ts, end_exclusive = _date_range(body.start_date, body.end_date)
        job_id = manager.start_upstream_import(
            upstream_url=current_upstream_url(),
            account=body.account,
            username=body.username,
            start_time=start_ts,
            end_time=(end_exclusive - 1) if end_exclusive is not None else None,
        )
        return {"status": "accepted", "job_id": job_id}

    @app.post("/api/imports/upload", status_code=202)
    async def upload_import(
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        filename = Path((file.filename or "messages.json").replace("\\", "/")).name
        suffix = Path(filename).suffix.lower()
        if suffix not in {".json", ".zip"}:
            raise HTTPException(status_code=400, detail="只支持 .zip 或 .json 文件。")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="upload_",
                suffix=suffix,
                dir=settings.temp_dir,
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                total = 0
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="上传文件超过安全上限。")
                    stream.write(chunk)
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise
        assert temp_path is not None
        job_id = manager.start_upload_import(path=temp_path, filename=filename)
        return {"status": "accepted", "job_id": job_id}

    @app.get("/api/jobs")
    async def list_jobs(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
        with session_factory() as session:
            jobs = list(
                session.scalars(
                    select(AnalysisJob).order_by(AnalysisJob.created_at.desc()).limit(limit)
                )
            )
        return {"jobs": [job_public(job) for job in jobs]}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        with session_factory() as session:
            job = session.get(AnalysisJob, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="任务不存在。")
            return job_public(job)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        with session_factory() as session:
            if session.get(AnalysisJob, job_id) is None:
                raise HTTPException(status_code=404, detail="任务不存在。")

        async def generate():
            last_payload = ""
            while not await request.is_disconnected():
                with session_factory() as session:
                    job = session.get(AnalysisJob, job_id)
                    if job is None:
                        yield 'event: error\ndata: {"error":"任务不存在"}\n\n'
                        return
                    public = job_public(job)
                payload = json.dumps(public, ensure_ascii=False)
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                if public["status"] in TERMINAL_STATUSES:
                    return
                await asyncio.sleep(0.6)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.delete("/api/jobs/{job_id}")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        if not manager.cancel(job_id):
            raise HTTPException(status_code=404, detail="任务不存在。")
        return {"status": "success"}

    @app.get("/api/conversations")
    async def list_conversations() -> dict[str, Any]:
        with session_factory() as session:
            rows = session.execute(
                select(
                    Conversation,
                    func.count(Message.id).label("message_count"),
                    func.count(func.distinct(Message.sender_username)).label("participant_count"),
                )
                .outerjoin(Message, Message.conversation_id == Conversation.id)
                .group_by(Conversation.id)
                .order_by(Conversation.updated_at.desc())
            ).all()
        return {
            "conversations": [
                {
                    "id": conversation.id,
                    "account": conversation.account,
                    "username": conversation.external_username,
                    "display_name": conversation.display_name,
                    "is_group": conversation.is_group,
                    "first_message_at": conversation.first_message_at,
                    "last_message_at": conversation.last_message_at,
                    "message_count": int(message_count or 0),
                    "participant_count": int(participant_count or 0),
                }
                for conversation, message_count, participant_count in rows
            ]
        }

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="会话不存在。")
            participants = list(
                session.scalars(
                    select(Participant)
                    .where(Participant.conversation_id == conversation_id)
                    .order_by(Participant.message_count.desc(), Participant.display_name)
                )
            )
            message_count = session.scalar(
                select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
            )
        return {
            "id": conversation.id,
            "account": conversation.account,
            "username": conversation.external_username,
            "display_name": conversation.display_name,
            "is_group": conversation.is_group,
            "first_message_at": conversation.first_message_at,
            "last_message_at": conversation.last_message_at,
            "message_count": int(message_count or 0),
            "participants": [
                {
                    "username": item.external_username,
                    "display_name": item.display_name,
                    "message_count": item.message_count,
                    "first_message_at": item.first_message_at,
                    "last_message_at": item.last_message_at,
                }
                for item in participants
            ],
        }

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str) -> dict[str, Any]:
        with session_scope(session_factory) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="会话不存在。")
            session.delete(conversation)
        return {"status": "success"}

    @app.post("/api/providers")
    async def save_provider(body: ProviderRequest) -> dict[str, Any]:
        base_url = _provider_url(body.base_url)
        persisted = True
        with session_scope(session_factory) as session:
            provider = session.get(ProviderConfig, body.id) if body.id else None
            if provider is None:
                provider = ProviderConfig(
                    name=body.name,
                    kind=body.kind,
                    base_url=base_url,
                    model=body.model,
                    max_context_tokens=body.max_context_tokens,
                    is_default=body.is_default,
                )
                session.add(provider)
                session.flush()
            else:
                provider.name = body.name
                provider.kind = body.kind
                provider.base_url = base_url
                provider.model = body.model
                provider.max_context_tokens = body.max_context_tokens
                provider.is_default = body.is_default
                provider.updated_at = now_ts()
            if body.is_default:
                session.execute(
                    update(ProviderConfig)
                    .where(ProviderConfig.id != provider.id)
                    .values(is_default=False)
                )
            provider_id = provider.id
        if body.api_key is not None:
            persisted = secret_store.set(provider_id, body.api_key)
        with session_factory() as session:
            provider = session.get(ProviderConfig, provider_id)
            assert provider is not None
            public = _provider_public(provider, secret_store.has(provider.id))
        return {"status": "success", "provider": public, "secret_persisted": persisted}

    @app.post("/api/providers/{provider_id}/test")
    async def test_provider(provider_id: str) -> dict[str, Any]:
        with session_factory() as session:
            provider = session.get(ProviderConfig, provider_id)
            if provider is None:
                raise HTTPException(status_code=404, detail="AI 服务配置不存在。")
            session.expunge(provider)
        client = OpenAICompatibleClient(provider, secret_store.get(provider.id))
        try:
            result, usage = await client.complete_json(
                system="你是连接测试程序，只返回 JSON。",
                user='请返回 {"ok": true}，不要添加其他内容。',
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            await client.aclose()
        return {
            "status": "success",
            "model_response": result,
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            },
        }

    @app.delete("/api/providers/{provider_id}")
    async def delete_provider(provider_id: str) -> dict[str, Any]:
        with session_scope(session_factory) as session:
            provider = session.get(ProviderConfig, provider_id)
            if provider is None:
                raise HTTPException(status_code=404, detail="AI 服务配置不存在。")
            session.delete(provider)
        secret_store.delete(provider_id)
        return {"status": "success"}

    @app.post("/api/analyses", status_code=202)
    async def create_analysis(body: AnalysisRequest) -> dict[str, Any]:
        start_ts, end_ts = _date_range(body.start_date, body.end_date)
        with session_factory() as session:
            conversation = session.get(Conversation, body.conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="会话不存在。")
            if body.mode == "member" and not body.member_username:
                raise HTTPException(status_code=400, detail="指定成员模式需要选择成员。")
            if session.get(ProviderConfig, body.provider_id) is None:
                raise HTTPException(status_code=404, detail="AI 服务配置不存在。")
            workflow_id, workflow_name, workflow = _resolve_workflow(
                session,
                workflow_id=body.workflow_id,
                inline=body.workflow,
            )
        job_id = manager.start_analysis(
            {
                "conversation_id": body.conversation_id,
                "mode": body.mode,
                "member_username": body.member_username,
                "start_time": start_ts,
                "end_time": end_ts,
                "focus": body.focus,
                "provider_id": body.provider_id,
                "pseudonymize": body.pseudonymize,
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
                "workflow": workflow.model_dump(mode="json"),
            }
        )
        return {"status": "accepted", "job_id": job_id}

    @app.post("/api/analyses/preview")
    async def preview_analysis(body: AnalysisRequest) -> dict[str, Any]:
        start_ts, end_ts = _date_range(body.start_date, body.end_date)
        try:
            with session_factory() as session:
                provider = session.get(ProviderConfig, body.provider_id)
                if provider is None:
                    raise HTTPException(
                        status_code=404,
                        detail="AI 服务配置不存在。",
                    )
                workflow_id, workflow_name, workflow = _resolve_workflow(
                    session,
                    workflow_id=body.workflow_id,
                    inline=body.workflow,
                )
                material = load_analysis_material(
                    session,
                    conversation_id=body.conversation_id,
                    mode=body.mode,
                    member_username=body.member_username,
                    start_time=start_ts,
                    end_time=end_ts,
                )
                pseudonymizer = (
                    Pseudonymizer(
                        material.selected_messages,
                        material.conversation,
                    )
                    if body.pseudonymize
                    else None
                )
                preview = build_analysis_preview(
                    material=material,
                    provider=provider,
                    focus=body.focus,
                    pseudonymizer=pseudonymizer,
                    workflow=workflow,
                )
        except AnalysisError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            **preview,
            "selected_message_count": len(material.selected_messages),
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "workflow": workflow.model_dump(mode="json"),
        }

    @app.get("/api/reports")
    async def list_reports() -> dict[str, Any]:
        with session_factory() as session:
            rows = session.execute(
                select(Report, Conversation)
                .join(Conversation, Conversation.id == Report.conversation_id)
                .order_by(Report.created_at.desc())
            ).all()
        return {
            "reports": [
                {
                    "id": report.id,
                    "job_id": report.job_id,
                    "conversation_id": report.conversation_id,
                    "conversation_name": conversation.display_name,
                    "prompt_tokens": report.prompt_tokens,
                    "completion_tokens": report.completion_tokens,
                    "created_at": report.created_at,
                }
                for report, conversation in rows
            ]
        }

    @app.get("/api/reports/{report_id}")
    async def get_report(report_id: str) -> dict[str, Any]:
        with session_factory() as session:
            report = session.get(Report, report_id)
            if report is None:
                raise HTTPException(status_code=404, detail="报告不存在。")
            return {
                "id": report.id,
                "job_id": report.job_id,
                "conversation_id": report.conversation_id,
                "report": json.loads(report.report_json),
                "markdown": report.markdown,
                "prompt_tokens": report.prompt_tokens,
                "completion_tokens": report.completion_tokens,
                "metrics": json.loads(report.metrics_json or "{}"),
                "created_at": report.created_at,
            }

    @app.delete("/api/reports/{report_id}")
    async def delete_report(report_id: str) -> dict[str, Any]:
        with session_scope(session_factory) as session:
            report = session.get(Report, report_id)
            if report is None:
                raise HTTPException(status_code=404, detail="报告不存在。")
            session.delete(report)
        return {"status": "success"}

    @app.get("/api/reports/{report_id}/download")
    async def download_report(report_id: str, format: Literal["markdown", "json"] = "markdown"):
        with session_factory() as session:
            report = session.get(Report, report_id)
            if report is None:
                raise HTTPException(status_code=404, detail="报告不存在。")
            if format == "json":
                content = json.dumps(json.loads(report.report_json), ensure_ascii=False, indent=2)
                media_type = "application/json"
                filename = f"wechat-report-{report.id[:8]}.json"
            else:
                content = report.markdown
                media_type = "text/markdown"
                filename = f"wechat-report-{report.id[:8]}.md"
        return PlainTextResponse(
            content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception(_request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": f"服务器内部错误：{exc}"})

    return app


app = create_app()
