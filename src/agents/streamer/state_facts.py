"""状态跃迁事实提取器——规则式（无 LLM）的"当前状态"客观事实块。

决策交接契约要 `topic_summary` 是可核对的客观状态陈述，主播表达侧也缺一段
不依赖 Planner 转述的事实底稿。本模块从**本轮新入账的游戏事件批次**（决策窗
边界批量写进时间线的那一批行）里只挑**状态跃迁类**条目：身体遭遇线里的死亡/
重生/临时任务开始与结束、游戏线里的交付上报，以及按关键词命中的睡觉/起床类
进展；外加本轮观察工具（如 minecraft_glance）回执的关键字段。不是全量事件
转储——高频的挨打、聊天、剧情叙事不进本块。

每行保留时间线里的绝对钟点标记，逐句都能对应到对话历史中的游戏条目或工具观察。
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Sequence, Tuple

__all__ = ["extract_state_facts"]

#: 状态跃迁类事件种类：身体遭遇线（game.body.*）的死亡/重生/临时任务开始与结束，
#: 游戏进展线（game.*）的交付/升级上报（任务结束或卡住待定夺）
_STATE_KINDS = frozenset({"died", "respawned", "reflex_started", "reflex_finished", "report"})

#: 事件行按关键词命中状态跃迁（睡觉/起床/死亡/重生/任务开始与结束常以这些词出现在
#: milestone / attention_required 行里）；挨打、聊天、剧情叙述不含这些词，不进本块
_STATE_KEYWORDS: Tuple[str, ...] = ("睡", "醒", "起床", "倒下", "死亡", "重生", "交付", "完成", "开始做")

#: 时间线行形态：``[19:42 游戏·minecraft·died] 角色倒下了（已结束）``、
#: ``[19:42 遭遇·minecraft·attacked] 身体挨了一阵打``、``[19:42 我在 minecraft 里开始做] …``
_LINE_RE = re.compile(r"^\[(?P<clock>\d{1,2}:\d{2})\s+(?P<label>[^]]+)\]\s*(?P<body>.*)$")

#: 委派行标签形态：``我在 <game> 里开始做``（也是状态跃迁：一件事开始了）
_DELEGATION_LABEL_PREFIX = "我在 "

#: 状态行最多保留条数（防批次堆积把事实块变成转储）
_MAX_STATE_LINES = 8

#: 回执摘要里背包最多列出的物品种数
_MAX_INVENTORY_ITEMS = 8


def _is_state_line(label: str, body: str) -> bool:
    """一条事件行是否状态跃迁类：种类在册，或正文命中状态关键词；聊天行不参与。"""
    if label.startswith(_DELEGATION_LABEL_PREFIX):
        return True
    parts = label.split("·")
    # 事件行标签形如 游戏·<game>·<kind> / 遭遇·<game>·<kind>；聊天行标签只有游戏名
    if len(parts) != 3 or parts[0] not in ("游戏", "遭遇"):
        return False
    kind = parts[-1]
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
    game_events: Sequence[str],
    tool_receipts: Sequence[Tuple[str, Any]] = (),
) -> str:
    """从本轮新入账的游戏事件行与本轮工具回执中提取状态跃迁事实块。

    Args:
        game_events: 本轮新入账批次的时间线行（``[HH:MM 标签] 正文`` 逐行）。
        tool_receipts: 本轮 ReAct 循环里观察工具的 (工具名, 结构化结果) 序列。

    Returns:
        客观事实文本（每行一条，保留原钟点标记）；无可提取事实时为空串。
    """
    lines: List[str] = []
    for raw in game_events:
        line = raw.strip()
        match = _LINE_RE.match(line)
        if match is None:
            continue
        if _is_state_line(match.group("label"), match.group("body")):
            lines.append(line)
    if len(lines) > _MAX_STATE_LINES:
        lines = lines[-_MAX_STATE_LINES:]
    for name, data in tool_receipts:
        row = _render_tool_receipt(name, data)
        if row:
            lines.append(row)
    return "\n".join(lines)
