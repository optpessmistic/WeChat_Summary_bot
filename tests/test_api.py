from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from sqlalchemy import select

from wechat_summary_bot.db import session_scope
from wechat_summary_bot.main import create_app
from wechat_summary_bot.models import AnalysisJob, Conversation, ProviderConfig, Report


def payload() -> dict:
    return {
        "schemaVersion": 1,
        "account": "wxid_me",
        "conversation": {"username": "wxid_alice", "displayName": "Alice", "isGroup": False},
        "messages": [
            {
                "id": "m1",
                "createTime": 1785200400,
                "renderType": "text",
                "senderUsername": "wxid_alice",
                "senderDisplayName": "Alice",
                "content": "测试消息",
            }
        ],
    }


@pytest.mark.asyncio
async def test_health_and_upload_flow(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            assert (await client.get("/api/health")).json()["status"] == "ok"
            response = await client.post(
                "/api/imports/upload",
                files={
                    "file": (
                        "messages.json",
                        json.dumps(payload()).encode(),
                        "application/json",
                    )
                },
            )
            assert response.status_code == 202
            job_id = response.json()["job_id"]
            deadline = time.time() + 5
            while time.time() < deadline:
                job = (await client.get(f"/api/jobs/{job_id}")).json()
                if job["status"] in {"succeeded", "failed"}:
                    break
                await asyncio.sleep(0.05)
            assert job["status"] == "succeeded", job
            conversations = (
                await client.get("/api/conversations")
            ).json()["conversations"]
            assert len(conversations) == 1
            assert conversations[0]["message_count"] == 1
            providers = (await client.get("/api/settings")).json()["providers"]
            preview = await client.post(
                "/api/analyses/preview",
                json={
                    "conversation_id": conversations[0]["id"],
                    "mode": "conversation",
                    "provider_id": providers[0]["id"],
                    "pseudonymize": True,
                },
            )
            assert preview.status_code == 200
            assert preview.json()["selected_message_count"] == 1
            assert preview.json()["estimated_input_tokens"] > 0
            with session_scope(app.state.session_factory) as session:
                report_job = AnalysisJob(
                    job_type="analysis",
                    conversation_id=conversations[0]["id"],
                    status="succeeded",
                    stage="报告已生成",
                    progress=100,
                )
                session.add(report_job)
                session.flush()
                report = Report(
                    job_id=report_job.id,
                    conversation_id=conversations[0]["id"],
                    report_json='{"overview":{"text":"测试"}}',
                    markdown="# 测试",
                    metrics_json='{"model_calls": 2}',
                )
                session.add(report)
                session.flush()
                report_id = report.id
            report_response = await client.get(f"/api/reports/{report_id}")
            assert report_response.json()["metrics"] == {"model_calls": 2}
            delete_response = await client.delete(f"/api/reports/{report_id}")
            assert delete_response.status_code == 200
            assert (await client.get("/api/reports")).json()["reports"] == []


@pytest.mark.asyncio
async def test_rejects_unknown_upload_type(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/imports/upload",
                files={"file": ("chat.txt", b"hello", "text/plain")},
            )
            assert response.status_code == 400


@pytest.mark.asyncio
async def test_provider_api_never_returns_api_key(settings, monkeypatch):
    app = create_app(settings)
    monkeypatch.setattr(app.state.secret_store, "set", lambda _provider_id, _secret: True)
    monkeypatch.setattr(app.state.secret_store, "has", lambda _provider_id: True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            secret = "super-secret-provider-key"
            response = await client.post(
                "/api/providers",
                json={
                    "name": "测试云端",
                    "kind": "openai_compatible",
                    "base_url": "https://api.example.com/v1",
                    "model": "test-model",
                    "max_context_tokens": 32_000,
                    "is_default": True,
                    "api_key": secret,
                },
            )
            assert response.status_code == 200
            assert secret not in response.text
            settings_response = await client.get("/api/settings")
            assert secret not in settings_response.text
            assert all(
                "api_key" not in provider
                for provider in settings_response.json()["providers"]
            )


@pytest.mark.asyncio
async def test_workflow_templates_crud_and_builtin_guards(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            workflows = (await client.get("/api/workflows")).json()["workflows"]
            assert {item["id"] for item in workflows} == {
                "builtin-fast",
                "builtin-balanced",
                "builtin-detailed",
            }
            balanced = next(item for item in workflows if item["id"] == "builtin-balanced")
            assert balanced["definition"]["evidence_mode"] == "none"

            forbidden_update = await client.put(
                "/api/workflows/builtin-balanced",
                json={
                    "name": "不能修改",
                    "description": "",
                    "definition": balanced["definition"],
                },
            )
            assert forbidden_update.status_code == 403
            assert (await client.delete("/api/workflows/builtin-balanced")).status_code == 403

            duplicate_response = await client.post(
                "/api/workflows/builtin-balanced/duplicate"
            )
            assert duplicate_response.status_code == 201
            duplicate = duplicate_response.json()["workflow"]
            assert duplicate["is_builtin"] is False
            updated_definition = {**duplicate["definition"], "chunk_tokens": 30_000}
            update_response = await client.put(
                f"/api/workflows/{duplicate['id']}",
                json={
                    "name": "我的工作流",
                    "description": "用于测试",
                    "definition": updated_definition,
                },
            )
            assert update_response.status_code == 200
            updated = update_response.json()["workflow"]
            assert updated["name"] == "我的工作流"
            assert updated["definition"]["chunk_tokens"] == 30_000

            assert (await client.delete(f"/api/workflows/{duplicate['id']}")).status_code == 200
            remaining_ids = {
                item["id"]
                for item in (await client.get("/api/workflows")).json()["workflows"]
            }
            assert duplicate["id"] not in remaining_ids


@pytest.mark.asyncio
async def test_analysis_job_snapshots_resolved_workflow(settings, monkeypatch):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        with session_scope(app.state.session_factory) as session:
            conversation = Conversation(
                account="wxid_me",
                external_username="wxid_chat",
                display_name="工作群",
                is_group=True,
            )
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id
            provider_id = session.scalar(select(ProviderConfig.id))
        captured: list[dict] = []

        def fake_start_analysis(input_data):
            captured.append(input_data)
            return "job-for-test"

        monkeypatch.setattr(app.state.job_manager, "start_analysis", fake_start_analysis)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            old_request = await client.post(
                "/api/analyses",
                json={
                    "conversation_id": conversation_id,
                    "provider_id": provider_id,
                },
            )
            assert old_request.status_code == 202
            assert captured[-1]["workflow_id"] == "builtin-balanced"
            assert captured[-1]["workflow_name"] == "均衡总结"
            assert captured[-1]["workflow"]["schema_version"] == 1

            definition = {
                **captured[-1]["workflow"],
                "chunk_tokens": 33_000,
                "sections": ["overview", "topics"],
            }
            edited_request = await client.post(
                "/api/analyses",
                json={
                    "conversation_id": conversation_id,
                    "provider_id": provider_id,
                    "workflow_id": "builtin-balanced",
                    "workflow": definition,
                },
            )
            assert edited_request.status_code == 202
            assert captured[-1]["workflow_name"] == "均衡总结（已编辑）"
            assert captured[-1]["workflow"]["chunk_tokens"] == 33_000
