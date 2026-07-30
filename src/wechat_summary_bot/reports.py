from __future__ import annotations

from typing import Any


def _evidence_suffix(item: dict[str, Any], *, enabled: bool) -> str:
    if not enabled:
        return ""
    ids = [str(value) for value in item.get("evidence_ids") or []]
    return " " + " ".join(f"[证据 {value[:8]}]" for value in ids) if ids else ""


def _list_section(
    lines: list[str],
    title: str,
    items: list[dict[str, Any]],
    field: str,
    *,
    evidence_enabled: bool,
) -> None:
    lines.extend([f"## {title}", ""])
    if not items:
        lines.extend(["暂无有充分证据的内容。", ""])
        return
    for item in items:
        text = str(item.get(field) or item.get("text") or "").strip()
        item_title = str(item.get("title") or "").strip()
        if item_title and item_title != text:
            text = f"**{item_title}**：{text}"
        extra: list[str] = []
        for key, label in (
            ("time", "时间"),
            ("owner", "负责人"),
            ("due", "期限"),
            ("status", "状态"),
        ):
            if item.get(key):
                extra.append(f"{label}：{item[key]}")
        suffix = f"（{'；'.join(extra)}）" if extra else ""
        lines.append(f"- {text}{suffix}{_evidence_suffix(item, enabled=evidence_enabled)}")
    lines.append("")


def render_markdown(report: dict[str, Any]) -> str:
    metadata = report.get("metadata") or {}
    title = str(metadata.get("conversation_name") or "微信聊天总结")
    evidence_enabled = metadata.get("evidence_mode") == "visible"
    if "evidence_mode" not in metadata:
        evidence_enabled = bool(report.get("evidence"))
    selected_sections = set(
        metadata.get("sections")
        or (
            "overview",
            "topics",
            "key_conclusions",
            "decisions",
            "action_items",
            "open_questions",
            "timeline",
        )
    )
    lines = [
        f"# {title} — AI 聊天总结",
        "",
        f"- 分析模式：{metadata.get('mode', '')}",
        f"- 指定成员：{metadata.get('member') or '无'}",
        f"- 模型：{metadata.get('provider', '')} / {metadata.get('model', '')}",
        f"- 工作流：{metadata.get('workflow_name') or '均衡总结'}",
        f"- 假名化：{'是' if metadata.get('pseudonymized') else '否'}",
        f"- 证据引用：{'显示' if evidence_enabled else '不生成'}",
        "",
    ]
    if "overview" in selected_sections:
        lines.extend(
            [
                "## 总体概览",
                "",
                str((report.get("overview") or {}).get("text") or "暂无概览")
                + _evidence_suffix(
                    report.get("overview") or {},
                    enabled=evidence_enabled,
                ),
                "",
            ]
        )
    section_specs = (
        ("topics", "主要主题", "summary"),
        ("key_conclusions", "关键结论", "text"),
        ("decisions", "决定", "text"),
        ("action_items", "待办", "text"),
        ("open_questions", "未决问题", "text"),
        ("timeline", "时间线", "event"),
    )
    for key, section_title, field in section_specs:
        if key in selected_sections:
            _list_section(
                lines,
                section_title,
                report.get(key) or [],
                field,
                evidence_enabled=evidence_enabled,
            )

    member = report.get("member_analysis") or {}
    if member:
        _list_section(
            lines,
            "成员主要观点",
            member.get("main_points") or [],
            "text",
            evidence_enabled=evidence_enabled,
        )
        _list_section(
            lines,
            "成员贡献",
            member.get("contributions") or [],
            "text",
            evidence_enabled=evidence_enabled,
        )
        _list_section(
            lines,
            "成员承诺",
            member.get("commitments") or [],
            "text",
            evidence_enabled=evidence_enabled,
        )
        _list_section(
            lines,
            "互动结果",
            member.get("interactions") or [],
            "text",
            evidence_enabled=evidence_enabled,
        )

    technical_terms = report.get("technical_terms") or []
    if technical_terms:
        lines.extend(["## 技术名词简释", ""])
        for item in technical_terms:
            term = str(item.get("term") or "").strip()
            explanation = str(item.get("explanation") or "").strip()
            domain = str(item.get("domain") or "未明确领域").strip()
            lines.append(f"- **{term}**（{domain}）：{explanation}")
        lines.append("")

    stats = report.get("participant_statistics") or {}
    lines.extend(
        [
            "## 参与统计",
            "",
            f"- 总消息数：{stats.get('message_count', 0)}",
            f"- 进入模型上下文的消息数：{stats.get('selected_message_count', 0)}",
            f"- 活跃天数：{stats.get('active_days', 0)}",
        ]
    )
    for participant in stats.get("participants") or []:
        lines.append(f"- {participant.get('name', '')}：{participant.get('message_count', 0)} 条")
    lines.append("")

    limitations = report.get("limitations") or []
    if limitations:
        lines.extend(["## 限制", ""])
        lines.extend(f"- {value}" for value in limitations)
        lines.append("")

    if evidence_enabled:
        lines.extend(["## 证据附录", ""])
        evidence_map = {str(item.get("id")): item for item in report.get("evidence") or []}
        for evidence_id, evidence in evidence_map.items():
            excerpt = str(evidence.get("excerpt") or "").replace("\n", " ")
            lines.append(
                f"- **证据 {evidence_id[:8]}** · {evidence.get('time', '')} · "
                f"{evidence.get('sender', '')}：{excerpt}"
            )
        lines.append("")
    return "\n".join(lines)
