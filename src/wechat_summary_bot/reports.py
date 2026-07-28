from __future__ import annotations

from typing import Any


def _evidence_suffix(item: dict[str, Any]) -> str:
    ids = [str(value) for value in item.get("evidence_ids") or []]
    return " " + " ".join(f"[证据 {value[:8]}]" for value in ids) if ids else ""


def _list_section(lines: list[str], title: str, items: list[dict[str, Any]], field: str) -> None:
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
        lines.append(f"- {text}{suffix}{_evidence_suffix(item)}")
    lines.append("")


def render_markdown(report: dict[str, Any]) -> str:
    metadata = report.get("metadata") or {}
    title = str(metadata.get("conversation_name") or "微信聊天总结")
    lines = [
        f"# {title} — AI 聊天总结",
        "",
        f"- 分析模式：{metadata.get('mode', '')}",
        f"- 指定成员：{metadata.get('member') or '无'}",
        f"- 模型：{metadata.get('provider', '')} / {metadata.get('model', '')}",
        f"- 假名化：{'是' if metadata.get('pseudonymized') else '否'}",
        "",
        "## 总体概览",
        "",
        str((report.get("overview") or {}).get("text") or "暂无概览")
        + _evidence_suffix(report.get("overview") or {}),
        "",
    ]
    _list_section(lines, "主要主题", report.get("topics") or [], "summary")
    _list_section(lines, "关键结论", report.get("key_conclusions") or [], "text")
    _list_section(lines, "决定", report.get("decisions") or [], "text")
    _list_section(lines, "待办", report.get("action_items") or [], "text")
    _list_section(lines, "未决问题", report.get("open_questions") or [], "text")
    _list_section(lines, "时间线", report.get("timeline") or [], "event")

    member = report.get("member_analysis") or {}
    if member:
        _list_section(lines, "成员主要观点", member.get("main_points") or [], "text")
        _list_section(lines, "成员贡献", member.get("contributions") or [], "text")
        _list_section(lines, "成员承诺", member.get("commitments") or [], "text")
        _list_section(lines, "互动结果", member.get("interactions") or [], "text")

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
