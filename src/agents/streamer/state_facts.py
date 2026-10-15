"""状态跃迁事实提取器——规则式（无 LLM）的"当前状态"客观事实块。

决策交接契约要 `topic_summary` 是可核对的客观状态陈述，主播表达侧也缺一段
不依赖 Planner 转述的事实底稿。本模块从决策窗已有素材里只挑**状态跃迁类**
条目：身体遭遇线里的死亡/重生/临时任务开始与结束、游戏线里的交付上报，以及
叙事行里按关键词命中的睡觉/起床类进展；外加本轮观察工具（如 minecraft_glance）
回执的关键字段。不是全量事件转储——高频的挨打、聊天、剧情叙事不进本块。

每行保留原有到达时间标记，逐句都能对应到上下文中的事件或工具观察。
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Sequence, Tuple

__all__ = ["extract_state_facts"]

#: 状态跃迁类事件种类：身体遭遇线（game.body.*）的死亡/重生/临时任务开始与结束，
#: 游戏进展线（game.*）的交付/升级上报（任务结束或卡住待定夺）
_STATE_KINDS = frozenset({"died", "respawned", "reflex_started", "reflex_finished", "report"})

#: 叙事行按关键词命中状态跃迁（睡觉/起床/死亡/重生/任务开始与结束常以这些词出现在
#: milestone / attention_required 行里）；挨打、聊天、剧情叙述不含这些词，不进本块
_STATE_KEYWORDS: Tuple[str, ...] = ("睡", "醒", "起床", "倒下", "死亡", "重生", "交付", "完成", "开始做")

#: 叙事行形态：``[新·刚刚] [minecraft·died] 角色倒下了（已结束）``（行首到达时间标记可无"新"）
_LINE_RE = re.compile(r"^\[(?P<age>[^\]]+)\]\s*\[(?P<game>[^·\]]+)·(?P<kind>[^\]]+)\]\s*(?P<body>.*)$")

#: 状态行最多保留条数（防长窗事件堆积把事实块变成转储）
_MAX_STATE_LINES = 8

#: 回执摘要里背包最多列出的物品种数
_MAX_INVENTORY_ITEMS = 8


def _is_state_line(kind: str, body: str) -> bool:
    """一条叙事行是否状态跃迁类：种类在册，或正文命中状态关键词。"""
    return kind in _STATE_KINDS or any(keyword in body for keyword in _STATE_KEYWORDS)


def _render_tool_receipt(name: str, data: Any) -> str:
    """观察工具回执 → 一行关键字段摘要；非现场速览形态的结果返回空串。

    只认 minecraft_glance 形态的回执（body / inventory / work 键）：身体标量、
    背包物品数量与手头工作是有信息量的状态；生物列表、设施列表是即时场景，
    不属"状态"，不进本块。
    """
    if not isinstance(data, dict):
        return ""
    parts: List[str] = []
    body = data.get("body")
    if isinstance(body, dict) and body:
        scalars = [
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in body.items()
            if not isinstance(value, (dict, list))
        ]
        if scalars:
            parts.append("身体：" + "，".join(scalars))
    inventory = data.get("inventory")
    if isinstance(inventory, list) and inventory:
        items = [str(item) for item in inventory[:_MAX_INVENTORY_ITEMS]]
        suffix = "（未列全）" if len(inventory) > _MAX_INVENTORY_ITEMS else ""
        parts.append("当前背包：" + "、".join(items) + suffix)
    work = data.get("work")
    if isinstance(work, dict) and work:
        parts.append(f"手头工作：{json.dumps(work, ensure_ascii=False)[:200]}")
    if not parts:
        return ""
    return f"[{name}回执] " + "；".join(parts)


def extract_state_facts(
    game_narrative: str,
    body_narrative: str,
    tool_receipts: Sequence[Tuple[str, Any]] = (),
) -> str:
    """从游戏叙事、身体近况与本轮工具回执中提取状态跃迁事实块。

    Args:
        game_narrative: 游戏叙事全文（``[到达时间] [game·类型] 内容`` 逐行）。
        body_narrative: 身体近况全文（形态同上，``game.body.*`` 摘要）。
        tool_receipts: 本轮 ReAct 循环里观察工具的 (工具名, 结构化结果) 序列。

    Returns:
        客观事实文本（每行一条，保留原到达时间标记）；无可提取事实时为空串。
    """
    lines: List[str] = []
    for text in (game_narrative, body_narrative):
        for raw in str(text or "").splitlines():
            line = raw.strip()
            match = _LINE_RE.match(line)
            if match is None:
                continue
            if _is_state_line(match.group("kind"), match.group("body")):
                lines.append(line)
    if len(lines) > _MAX_STATE_LINES:
        lines = lines[-_MAX_STATE_LINES:]
    for name, data in tool_receipts:
        row = _render_tool_receipt(name, data)
        if row:
            lines.append(row)
    return "\n".join(lines)
