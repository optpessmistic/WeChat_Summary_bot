from __future__ import annotations

import pytest
from pydantic import ValidationError

from wechat_summary_bot.workflows import WorkflowDefinition, builtin_workflows, default_workflow


def test_builtin_workflows_cover_cost_quality_tradeoffs():
    fast, balanced, detailed = builtin_workflows()

    assert [fast.id, balanced.id, detailed.id] == [
        "builtin-fast",
        "builtin-balanced",
        "builtin-detailed",
    ]
    assert fast.definition.evidence_mode == "none"
    assert fast.definition.thinking_mode == "disabled"
    assert fast.definition.chunk_tokens > balanced.definition.chunk_tokens
    assert balanced.definition.explain_terms is True
    assert balanced.definition.retry_limit == 1
    assert detailed.definition.evidence_mode == "visible"
    assert detailed.definition.final_refine is True
    assert detailed.definition.retry_limit == 2


def test_default_workflow_returns_an_independent_copy():
    first = default_workflow()
    second = default_workflow()

    first.sections.remove("timeline")

    assert "timeline" in second.sections


@pytest.mark.parametrize(
    "payload",
    [
        {"sections": []},
        {"map_parallelism": 5},
        {"retry_limit": 3},
        {"unknown_stage": "arbitrary-code"},
    ],
)
def test_workflow_definition_rejects_unsafe_or_invalid_shape(payload):
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(payload)
