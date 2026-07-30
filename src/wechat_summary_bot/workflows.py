from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SectionName = Literal[
    "overview",
    "topics",
    "key_conclusions",
    "decisions",
    "action_items",
    "open_questions",
    "timeline",
]

DEFAULT_SECTIONS: list[SectionName] = [
    "overview",
    "topics",
    "key_conclusions",
    "decisions",
    "action_items",
    "open_questions",
    "timeline",
]


class WorkflowDefinition(BaseModel):
    """Validated, serializable snapshot of one analysis workflow."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    evidence_mode: Literal["none", "visible"] = "none"
    explain_terms: bool = True
    sections: list[SectionName] = Field(default_factory=lambda: list(DEFAULT_SECTIONS))
    chunk_tokens: int = Field(default=24_000, ge=2_000, le=900_000)
    map_parallelism: int = Field(default=2, ge=1, le=4)
    reduce_batch_size: int = Field(default=5, ge=2, le=8)
    final_refine: bool = False
    output_detail: Literal["brief", "normal", "detailed"] = "normal"
    thinking_mode: Literal["disabled", "auto", "enabled"] = "disabled"
    reasoning_effort: Literal["low", "medium", "high", "max"] = "high"
    map_max_output_tokens: int = Field(default=1_000, ge=256, le=16_000)
    reduce_max_output_tokens: int = Field(default=1_600, ge=256, le=24_000)
    final_max_output_tokens: int = Field(default=2_800, ge=512, le=32_000)
    retry_limit: int = Field(default=1, ge=0, le=2)
    request_timeout_seconds: int = Field(default=180, ge=30, le=600)
    custom_instructions: str = Field(default="", max_length=4_000)
    map_instructions: str = Field(default="", max_length=8_000)
    reduce_instructions: str = Field(default="", max_length=8_000)
    final_instructions: str = Field(default="", max_length=8_000)

    @field_validator("sections")
    @classmethod
    def validate_sections(cls, value: list[SectionName]) -> list[SectionName]:
        unique = list(dict.fromkeys(value))
        if not unique:
            raise ValueError("至少选择一个报告栏目。")
        return unique


@dataclass(frozen=True, slots=True)
class BuiltinWorkflow:
    id: str
    name: str
    description: str
    definition: WorkflowDefinition


def builtin_workflows() -> tuple[BuiltinWorkflow, ...]:
    return (
        BuiltinWorkflow(
            id="builtin-fast",
            name="快速省钱",
            description="关闭模型思考和证据引用，扩大分块并并发提炼，生成简短总结。",
            definition=WorkflowDefinition(
                sections=["overview", "topics", "key_conclusions", "action_items"],
                chunk_tokens=48_000,
                map_parallelism=3,
                reduce_batch_size=6,
                output_detail="brief",
                thinking_mode="disabled",
                map_max_output_tokens=700,
                reduce_max_output_tokens=1_000,
                final_max_output_tokens=1_800,
                retry_limit=1,
            ),
        ),
        BuiltinWorkflow(
            id="builtin-balanced",
            name="均衡总结",
            description="紧凑提炼后分层合并，默认不引用原文，并生成技术术语简释。",
            definition=WorkflowDefinition(),
        ),
        BuiltinWorkflow(
            id="builtin-detailed",
            name="细致可追溯",
            description="保留证据、使用更小分块并执行最终润色，耗时和 token 较高。",
            definition=WorkflowDefinition(
                evidence_mode="visible",
                chunk_tokens=12_000,
                map_parallelism=1,
                reduce_batch_size=4,
                final_refine=True,
                output_detail="detailed",
                thinking_mode="enabled",
                reasoning_effort="high",
                map_max_output_tokens=1_800,
                reduce_max_output_tokens=2_600,
                final_max_output_tokens=4_200,
                retry_limit=2,
            ),
        ),
    )


def default_workflow() -> WorkflowDefinition:
    return builtin_workflows()[1].definition.model_copy(deep=True)
