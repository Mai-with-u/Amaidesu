"""游戏事件账本：游戏/身体/聊天事件的"入账 → 折叠 → 出口"全链承载。

主播侧的游戏事件链（入账与折叠、把时间线行反解析回事实、行标签字面量）
集中在本模块单一承载，生产代码零手写行标签字面量：

- **入账**：``record_*`` 方法接收结构化事件字段，内部做术语脱敏（任务号/观测
  引用等内部标识不进主播上下文），构造时间线行标签（``游戏·<game>·<type>`` /
  ``遭遇·<game>·<kind>`` / 委派行）并暂存结构化条目；账本外生产代码零手写标签
  字面量。
- **折叠**：待入账阶段同摘要在折叠窗内连发时并入最近一条只记数（"另 N 次相同"）；
  条目一经决策窗边界写进时间线就不再改写。
- **出口**：``drain`` 在决策窗边界产出本批时间线行与本批状态跃迁事实块。事实块
  直接从结构化条目渲染（保留原钟点标记），不做"字符串行 → 正则 → 事实"的反解析。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, List, Sequence, Tuple

from src.modules.utils.time_utils import now_ms

from .term_sanitizer import sanitize_internal_terms
from .timeline import GameEventBatch, TimelineTurn, note_turn

__all__ = ["GameLedger"]

# 游戏事件同摘要折叠窗（毫秒）：待入账队列里连续到达、内容相同的条目并入一条只记数。
# 覆盖游戏/身体侧按重试节奏连发同一失败摘要的典型间隔（秒级到一分多钟）
_GAME_EVENT_FOLD_WINDOW_MS = 90_000

# 待入账队列上限（条）。决策窗长期不发生时防无界增长；溢出丢最旧
_PENDING_GAME_EVENTS_MAX = 200

#: 状态跃迁类事件种类：身体遭遇线（game.body.*）的死亡/重生/临时任务开始与结束，
#: 游戏进展线（game.*）的交付/升级上报（任务结束或卡住待定夺）
_STATE_KINDS = frozenset({"died", "respawned", "reflex_started", "reflex_finished", "report"})

#: 事件正文按关键词命中状态跃迁（睡觉/起床/死亡/重生/任务开始与结束常以这些词出现在
#: milestone / attention_required 条目里）；挨打、聊天、剧情叙述不含这些词，不进本块
_STATE_KEYWORDS: Tuple[str, ...] = ("睡", "醒", "起床", "倒下", "死亡", "重生", "交付", "完成", "开始做")

#: 状态行最多保留条数（防批次堆积把事实块变成转储）
_MAX_STATE_LINES = 8

#: 回执摘要里背包最多列出的物品种数
_MAX_INVENTORY_ITEMS = 8


@dataclass
class _PendingEntry:
    """待入账的一条结构化条目：时间线行（未折叠的底稿）+ 标签/正文字段 + 折叠计数。

    折叠只发生在待入账阶段：条目一经决策窗边界写进时间线就不再改写，之后到达的
    相同摘要是新的一条。``label`` / ``body`` 供事实渲染按结构化字段挑选，不走反解析。
    """

    turn: TimelineTurn
    label: str  # 行标签（``游戏·<game>·<type>`` / ``遭遇·<game>·<kind>`` / 聊天行的游戏名）
    body: str  # 行正文（标签后的话，未附折叠计数说明）
    base: str  # 未附加折叠计数说明的行原文（折叠比较键）
    arrived_ms: int
    count: int = 1
    is_chat: bool = False  # 游戏聊天行：入账后其原话随本窗说话交给表达侧


class GameLedger:
    """单个 StreamerAgent 持有的游戏事件账本（非线程安全）。

    入账方法返回脱敏后的文本，供回调处的嘴部喂料复用同一份脱敏值；委派记录只
    构造时间线行（委派行直写时间线，不进决策窗批次，也不参与事实块）。
    """

    def __init__(self) -> None:
        self._pending: List[_PendingEntry] = []

    def reset(self) -> None:
        """清空待入账队列（场次开关边界：上一场的未出账条目不带入下一场）。"""
        self._pending.clear()

    def pending_lines(self) -> List[str]:
        """待入账事件行的只读快照（已脱敏的行原文；出账经 ``drain``，本方法只看不取）。"""
        return [entry.base for entry in self._pending]

    # ==================== 标签构造（账本外零手写标签字面量） ====================

    @staticmethod
    def game_label(game: str, event_type: str) -> str:
        """游戏进展行标签：``游戏·<game>·<type>``。"""
        return f"游戏·{game}·{event_type}"

    @staticmethod
    def encounter_label(game: str, kind: str) -> str:
        """身体遭遇行标签：``遭遇·<game>·<kind>``。"""
        return f"遭遇·{game}·{kind}"

    @staticmethod
    def delegation_label(agent: str) -> str:
        """委派行标签：``我在 <agent> 里开始做``；未指名执行者时为``我在游戏里开始做``。"""
        return f"我在 {agent} 里开始做" if agent else "我在游戏里开始做"

    # ==================== 入账 ====================

    def record_event(self, game: str, event_type: str, message: str, timestamp_ms: int) -> str:
        """游戏进展事件（game.*）入账，返回脱敏后的正文供嘴部喂料复用。"""
        message = sanitize_internal_terms(message)
        label = self.game_label(game, event_type)
        self._stage(note_turn(label, message, timestamp_ms or now_ms()), label=label, body=message)
        return message

    def record_encounter(self, game: str, kind: str, summary: str, resolved: bool, timestamp_ms: int) -> str:
        """身体遭遇事件（game.body.*）入账，返回脱敏后的摘要供嘴部喂料复用。"""
        summary = sanitize_internal_terms(summary)
        body = f"{summary}（已结束）" if resolved else summary
        label = self.encounter_label(game, kind)
        self._stage(note_turn(label, body, timestamp_ms or now_ms()), label=label, body=body)
        return summary

    def record_chat(self, game: str, body: str, timestamp_ms: int) -> None:
        """游戏聊天行入账（标签就是游戏名）；原话随本窗说话交给表达侧，行文不经脱敏。"""
        self._stage(note_turn(game, body, timestamp_ms or now_ms()), label=game, body=body, is_chat=True)

    def record_delegation(self, agent: str, instruction: str) -> TimelineTurn:
        """Planner 受理委派：构造"我在哪个游戏里开始做什么"的时间线行（直写时间线用）。"""
        return note_turn(self.delegation_label(agent), sanitize_internal_terms(instruction), now_ms())

    def _stage(self, turn: TimelineTurn, *, label: str, body: str, is_chat: bool = False) -> None:
        """入账暂存：同摘要落在折叠窗内时并入最近一条只记数，按上限裁剪。"""
        now = now_ms()
        if self._pending:
            last = self._pending[-1]
            if last.base == turn.content and now - last.arrived_ms <= _GAME_EVENT_FOLD_WINDOW_MS:
                last.count += 1
                return
        self._pending.append(
            _PendingEntry(turn=turn, label=label, body=body, base=turn.content, arrived_ms=now, is_chat=is_chat)
        )
        overflow = len(self._pending) - _PENDING_GAME_EVENTS_MAX
        if overflow > 0:
            del self._pending[:overflow]

    # ==================== 出口 ====================

    def drain(self, private_chat: Sequence[str] = ()) -> GameEventBatch:
        """决策窗边界：待入账队列整批出账，产出时间线行与本批状态跃迁事实块。

        折叠过多条的行附计数说明（"另 N 次相同"）后再入时间线；时间线条目只追加
        不改写，相邻窗口的历史前缀逐字一致。事实块从结构化条目渲染，带原钟点标记。
        """
        staged = self._pending
        self._pending = []
        turns: List[TimelineTurn] = []
        events: List[str] = []
        fresh_chat: List[str] = []
        state_lines: List[str] = []
        for item in staged:
            content = item.base if item.count == 1 else f"{item.base}（另 {item.count - 1} 次相同）"
            turn = item.turn if content == item.turn.content else replace(item.turn, content=content)
            turns.append(turn)
            events.append(content)
            if item.is_chat:
                fresh_chat.append(content)
            if self._is_state_line(item.label, item.body):
                state_lines.append(content)
        if len(state_lines) > _MAX_STATE_LINES:
            state_lines = state_lines[-_MAX_STATE_LINES:]
        return GameEventBatch(
            events=tuple(events),
            fresh_chat=tuple(fresh_chat),
            private_chat=tuple(private_chat),
            facts_text="\n".join(state_lines),
            timeline_turns=tuple(turns),
        )

    @staticmethod
    def _is_state_line(label: str, body: str) -> bool:
        """一条条目是否状态跃迁类：事件种类在册，或正文命中状态关键词；聊天行不参与。"""
        parts = label.split("·")
        # 事件行标签形如 游戏·<game>·<kind> / 遭遇·<game>·<kind>；聊天行标签只有游戏名
        if len(parts) != 3 or parts[0] not in ("游戏", "遭遇"):
            return False
        return parts[-1] in _STATE_KINDS or any(keyword in body for keyword in _STATE_KEYWORDS)

    # ==================== 观察工具回执 ====================

    @staticmethod
    def render_tool_receipt(name: str, data: Any) -> str:
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

    @classmethod
    def compose_facts(cls, facts_text: str, tool_receipts: Sequence[Tuple[str, Any]]) -> str:
        """本批事实块与本轮观察工具回执合成完整事实文本（回执行接在事实行之后）。"""
        lines = [facts_text] if facts_text else []
        for name, data in tool_receipts:
            row = cls.render_tool_receipt(name, data)
            if row:
                lines.append(row)
        return "\n".join(lines)
