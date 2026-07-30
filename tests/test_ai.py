from __future__ import annotations

import httpx
import pytest

import wechat_summary_bot.ai as ai_module
from wechat_summary_bot.ai import (
    AnalysisCancelled,
    AnalysisMaterial,
    MessageSnapshot,
    ModelRequestOptions,
    ModelUsage,
    OpenAICompatibleClient,
    Pseudonymizer,
    build_analysis_preview,
    run_analysis,
)
from wechat_summary_bot.models import ProviderConfig
from wechat_summary_bot.reports import render_markdown
from wechat_summary_bot.workflows import WorkflowDefinition


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
        workflow=WorkflowDefinition(
            evidence_mode="visible",
            chunk_tokens=2_000,
        ),
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
    assert output.usage.prompt_tokens == 10
    assert output.metrics["logical_model_calls"] == 1
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
    client = OpenAICompatibleClient(provider, "secret")
    result, usage = await client.complete_json(
        system="system",
        user="user",
    )
    assert result["overview"]["text"] == "ok"
    # The successful HTTP response containing invalid JSON was still billable,
    # so its estimated usage is retained instead of silently under-reporting.
    assert usage.prompt_tokens > 7
    assert usage.completion_tokens > 3
    assert client.metrics.request_attempts == 3
    assert client.metrics.retries == 2
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


@pytest.mark.asyncio
async def test_no_evidence_workflow_omits_ids_and_keeps_technical_terms(monkeypatch):
    prompts: list[str] = []

    async def fake_complete(self, *, system: str, user: str):
        prompts.extend([system, user])
        return {
            "overview": {
                "text": "团队已明确原型和评审分工。",
                "evidence_ids": ["evidence-1"],
            },
            "topics": [
                {
                    "title": "原型",
                    "summary": "本周完成原型。",
                    "evidence_ids": ["evidence-1"],
                }
            ],
            "technical_terms": [
                {
                    "term": "原型",
                    "explanation": "用于快速验证方案的早期版本。",
                    "domain": "产品设计",
                }
            ],
        }, ModelUsage(prompt_tokens=20, completion_tokens=8)

    monkeypatch.setattr(OpenAICompatibleClient, "complete_json", fake_complete)
    provider = ProviderConfig(
        name="测试服务",
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        model="test-model",
        max_context_tokens=32_000,
    )
    output = await run_analysis(
        material=material(),
        provider=provider,
        api_key="secret",
        mode="conversation",
        focus="",
        pseudonymize=True,
        workflow=WorkflowDefinition(
            evidence_mode="none",
            explain_terms=True,
            chunk_tokens=2_000,
            custom_instructions="特别关注 Alice 和 wxid_alice 在秘密项目群的分工。",
            final_instructions="说明 Alice 的任务，但不要逐条复述。",
        ),
        is_cancelled=lambda: False,
        on_progress=lambda stage, value: _capture([], stage, value),
    )

    outbound = "\n".join(prompts)
    assert '"evidence_id"' not in outbound
    assert "evidence_ids" not in outbound
    assert "Alice" not in outbound
    assert "wxid_alice" not in outbound
    assert "秘密项目群" not in outbound
    assert (
        output.metrics["workflow"]["custom_instructions"]
        == "特别关注 Alice 和 wxid_alice 在秘密项目群的分工。"
    )
    assert "evidence" not in output.report
    assert "evidence_ids" not in output.report["overview"]
    assert output.report["technical_terms"][0]["domain"] == "产品设计"
    assert output.metrics["logical_model_calls"] == 1


def test_preview_counts_single_chunk_as_one_call():
    provider = ProviderConfig(
        name="测试服务",
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        model="test-model",
        max_context_tokens=32_000,
    )
    preview = build_analysis_preview(
        material=material(),
        provider=provider,
        focus="",
        pseudonymize=True,
        workflow=WorkflowDefinition(chunk_tokens=2_000, retry_limit=1),
    )

    assert preview["chunk_count"] == 1
    assert preview["logical_model_calls"] == 1
    assert preview["max_model_calls_with_retries"] == 2
    assert preview["estimated_total_tokens_max_with_retries"] == preview["estimated_total_tokens_max"] * 2
    assert preview["estimated_total_tokens_max"] >= preview["estimated_prompt_tokens_max"]


def test_markdown_without_evidence_has_glossary_but_no_appendix():
    markdown = render_markdown(
        {
            "metadata": {
                "conversation_name": "测试群",
                "evidence_mode": "none",
                "sections": ["overview"],
            },
            "overview": {"text": "一段不带引用的总结。"},
            "technical_terms": [
                {
                    "term": "API",
                    "explanation": "软件之间交换数据的接口。",
                    "domain": "软件工程",
                }
            ],
            "participant_statistics": {},
            "evidence": [
                {
                    "id": "must-not-render",
                    "time": "",
                    "sender": "",
                    "excerpt": "不应显示",
                }
            ],
        }
    )

    assert "技术名词简释" in markdown
    assert "证据附录" not in markdown
    assert "must-not-render" not in markdown


def test_pseudonymizer_registers_aliases_and_account():
    base = material()
    renamed = MessageSnapshot(
        id="evidence-3",
        source_message_id="three",
        server_id="103",
        quote_server_id="",
        sender_username="wxid_alice",
        sender_display_name="Alice 新昵称",
        created_at=1785200520,
        render_type="text",
        content="新昵称消息",
        quote_content="",
        title="",
        url="",
        file_name="",
    )
    pseudonymizer = Pseudonymizer(
        [*base.selected_messages, renamed],
        base.conversation,
    )

    assert pseudonymizer.original_to_token["Alice"] == "成员P001"
    assert pseudonymizer.original_to_token["Alice 新昵称"] == "成员P001"
    assert pseudonymizer.original_to_token["wxid_me"] == "本人P000"
    text = pseudonymizer.replace_text("Alice、Alice 新昵称和账号 wxid_me 正在秘密项目群讨论。")
    assert "Alice" not in text
    assert "wxid_me" not in text
    assert "秘密项目群" not in text


def test_pseudonymizer_keeps_distinct_usernames_separate_when_names_match():
    base = material()
    duplicate_name = MessageSnapshot(
        id="evidence-3",
        source_message_id="three",
        server_id="103",
        quote_server_id="",
        sender_username="wxid_other_alice",
        sender_display_name="Alice",
        created_at=1785200520,
        render_type="text",
        content="同名成员消息",
        quote_content="",
        title="",
        url="",
        file_name="",
    )
    pseudonymizer = Pseudonymizer(
        [*base.selected_messages, duplicate_name],
        base.conversation,
    )

    assert pseudonymizer.original_to_token["wxid_alice"] == "成员P001"
    assert pseudonymizer.original_to_token["wxid_other_alice"] != "成员P001"
    assert pseudonymizer.sender(duplicate_name) == "成员P003"


def test_all_workflow_instruction_fields_are_pseudonymized():
    base = material()
    pseudonymizer = Pseudonymizer(
        base.selected_messages,
        base.conversation,
    )
    workflow = WorkflowDefinition(
        custom_instructions="Alice / wxid_alice / 秘密项目群",
        map_instructions="提炼 Alice",
        reduce_instructions="合并 wxid_alice",
        final_instructions="输出秘密项目群",
    )
    outbound_workflow = ai_module._workflow_for_model(workflow, pseudonymizer)

    for field_name in (
        "custom_instructions",
        "map_instructions",
        "reduce_instructions",
        "final_instructions",
    ):
        value = getattr(outbound_workflow, field_name)
        assert "Alice" not in value
        assert "wxid_alice" not in value
        assert "秘密项目群" not in value
    assert workflow.custom_instructions == "Alice / wxid_alice / 秘密项目群"


@pytest.mark.asyncio
async def test_deepseek_request_disables_thinking_and_caps_output(monkeypatch):
    bodies: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, *args, **kwargs):
            bodies.append(kwargs["json"])
            return httpx.Response(
                200,
                request=httpx.Request(
                    "POST",
                    "https://api.deepseek.com/v1/chat/completions",
                ),
                json={
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeAsyncClient)
    provider = ProviderConfig(
        name="DeepSeek",
        kind="openai_compatible",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-v4-pro",
        max_context_tokens=32_000,
    )
    client = OpenAICompatibleClient(provider, "secret")
    client.configure(
        ModelRequestOptions(
            stage="map",
            max_output_tokens=700,
            thinking_mode="disabled",
            retry_limit=0,
        )
    )
    result, _usage = await client.complete_json(system="system", user="user")
    await client.aclose()

    assert result == {"ok": True}
    assert bodies[0]["max_tokens"] == 700
    assert bodies[0]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in bodies[0]
