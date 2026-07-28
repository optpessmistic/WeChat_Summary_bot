from __future__ import annotations

import httpx
import pytest

import wechat_summary_bot.ai as ai_module
from wechat_summary_bot.ai import (
    AnalysisCancelled,
    AnalysisMaterial,
    MessageSnapshot,
    ModelUsage,
    OpenAICompatibleClient,
    run_analysis,
)
from wechat_summary_bot.models import ProviderConfig


def material() -> AnalysisMaterial:
    messages = [
        MessageSnapshot(
            id="evidence-1",
            source_message_id="one",
            server_id="101",
            quote_server_id="",
            sender_username="wxid_alice",
            sender_display_name="Alice",
            created_at=1785200400,
            render_type="text",
            content="Alice 将在周五前完成原型。",
            quote_content="",
            title="",
            url="",
            file_name="",
        ),
        MessageSnapshot(
            id="evidence-2",
            source_message_id="two",
            server_id="102",
            quote_server_id="101",
            sender_username="wxid_bob",
            sender_display_name="Bob",
            created_at=1785200460,
            render_type="quote",
            content="Bob 负责评审。",
            quote_content="Alice 将在周五前完成原型。",
            title="",
            url="",
            file_name="",
        ),
    ]
    return AnalysisMaterial(
        conversation={
            "id": "conversation-1",
            "name": "秘密项目群",
            "username": "secret@chatroom",
            "is_group": True,
            "account": "wxid_me",
        },
        selected_messages=messages,
        all_messages=messages,
        participant_stats={
            "message_count": 2,
            "selected_message_count": 2,
            "active_days": 1,
            "participants": [
                {"name": "Alice", "message_count": 1},
                {"name": "Bob", "message_count": 1},
            ],
        },
        member_display_name="Alice",
    )


@pytest.mark.asyncio
async def test_hierarchical_analysis_pseudonymizes_and_validates(monkeypatch):
    prompts: list[str] = []

    async def fake_complete(self, *, system: str, user: str):
        prompts.append(user)
        report = {
            "overview": {"text": "成员P001 将完成原型。", "evidence_ids": ["evidence-1"]},
            "topics": [
                {"title": "原型", "summary": "按期完成并评审。", "evidence_ids": ["evidence-1", "invented"]}
            ],
            "key_conclusions": [{"text": "已有分工。", "evidence_ids": ["evidence-2"]}],
            "decisions": [],
            "action_items": [
                {
                    "text": "完成原型",
                    "owner": "成员P001",
                    "due": "周五",
                    "status": "待办",
                    "evidence_ids": ["evidence-1"],
                }
            ],
            "open_questions": [{"text": "无证据问题", "evidence_ids": ["invented"]}],
            "timeline": [],
            "member_analysis": {
                "main_points": [{"text": "推进原型", "evidence_ids": ["evidence-1"]}],
                "contributions": [],
                "commitments": [],
                "interactions": [],
            },
            "limitations": [],
        }
        return report, ModelUsage(prompt_tokens=10, completion_tokens=5)

    monkeypatch.setattr(OpenAICompatibleClient, "complete_json", fake_complete)
    provider = ProviderConfig(
        name="测试服务",
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        model="test-model",
        max_context_tokens=32_000,
    )
    progress = []
    output = await run_analysis(
        material=material(),
        provider=provider,
        api_key="secret",
        mode="member",
        focus="整理 Alice 在秘密项目群的分工",
        pseudonymize=True,
        chunk_token_budget=2_000,
        is_cancelled=lambda: False,
        on_progress=lambda stage, value: _capture(progress, stage, value),
    )
    outbound = "\n".join(prompts)
    assert "wxid_alice" not in outbound
    assert "Alice" not in outbound
    assert "秘密项目群" not in outbound
    assert output.report["overview"]["text"] == "Alice 将完成原型。"
    assert output.report["topics"][0]["evidence_ids"] == ["evidence-1"]
    assert output.report["open_questions"] == []
    assert output.report["action_items"][0]["owner"] == "Alice"
    assert output.usage.prompt_tokens >= 20
    assert progress


async def _capture(target, stage, value):
    target.append((stage, value))


@pytest.mark.asyncio
async def test_model_client_retries_rate_limit_and_invalid_json(monkeypatch):
    responses = [
        httpx.Response(
            429,
            request=httpx.Request("POST", "https://api.example.com/v1/chat/completions"),
            json={"error": "rate limited"},
        ),
        httpx.Response(
            200,
            request=httpx.Request(
                "POST",
                "https://api.example.com/v1/chat/completions",
            ),
            json={"choices": [{"message": {"content": "not json"}}]},
        ),
        httpx.Response(
            200,
            request=httpx.Request(
                "POST",
                "https://api.example.com/v1/chat/completions",
            ),
            json={
                "choices": [{"message": {"content": '{"overview":{"text":"ok"}}'}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        ),
    ]

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, *args, **kwargs):
            return responses.pop(0)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(ai_module.asyncio, "sleep", no_wait)
    provider = ProviderConfig(
        name="测试服务",
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        model="test-model",
        max_context_tokens=32_000,
    )
    result, usage = await OpenAICompatibleClient(provider, "secret").complete_json(
        system="system",
        user="user",
    )
    assert result["overview"]["text"] == "ok"
    assert usage.prompt_tokens == 7
    assert usage.completion_tokens == 3
    assert not responses


@pytest.mark.asyncio
async def test_analysis_cancellation_stops_before_final_merge(monkeypatch):
    cancelled = False
    call_count = 0

    async def fake_complete(self, *, system: str, user: str):
        nonlocal cancelled, call_count
        call_count += 1
        cancelled = True
        return {
            "overview": {
                "text": "已有事实",
                "evidence_ids": ["evidence-1"],
            }
        }, ModelUsage()

    monkeypatch.setattr(OpenAICompatibleClient, "complete_json", fake_complete)
    provider = ProviderConfig(
        name="测试服务",
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        model="test-model",
        max_context_tokens=32_000,
    )
    with pytest.raises(AnalysisCancelled):
        await run_analysis(
            material=material(),
            provider=provider,
            api_key="secret",
            mode="conversation",
            focus="",
            pseudonymize=True,
            chunk_token_budget=2_000,
            is_cancelled=lambda: cancelled,
            on_progress=lambda stage, value: _capture([], stage, value),
        )
    assert call_count == 1
