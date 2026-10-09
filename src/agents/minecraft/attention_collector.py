"""
MaicraftAttentionCollector —— AI 玩家身体事件采集器

上游是 MaiCraft v1 的事件流（``events`` 默认的 ``self``：角色身上发生的事）：生存需求插进来的临时任务（自卫、夜里封顶自保、
退离边沿）开始与结束、角色自己处理不了的需求、角色死亡，都写在这条流上。本采集器**常驻**带着游标长轮询，
把其中的身体遭遇转成 ``game.body.*`` 事件；目标运行的处境变化归游戏 Agent 的任务通道，不转。
连接、长轮询与游标都在 :class:`MaicraftEventsCollector`，这里只管分类与转发。

为什么是独立的流而不是让游戏 Agent 兼职转发：
- 它是**持续流**（无目标、无起止、生命周期挂装配期）——按三问判据归采集器；
- 游戏 Agent 只在**干活时**需要身体事实（用来调整任务），主播侧**一直**需要
  叙事素材；把后者绑在前者的忙碌状态与重建上，等于让主播的叙事随时断档。

连接与游标自带一份（不共用游戏 Agent 的 MCP 连接）：事件流按读者各自的游标读，
两边互不影响；Mod 侧支持多会话并行。
"""

from __future__ import annotations

from typing import Any, Dict

from src.agents.minecraft.attention_matrix import KIND_TO_EVENT, RESOLVED_KINDS, classify, summarize
from src.agents.minecraft.events_collector import MaicraftEventsCollector
from src.modules.events.payloads.body import BodyEventPayload


class MaicraftAttentionCollector(MaicraftEventsCollector):
    """AI 玩家身体事件采集器（常驻长轮询 MaiCraft 的任务事件流）。"""

    name = "maicraft_attention"
    description = "常驻读 MaiCraft 的任务事件流，把身体先处理的急事、处理不了的需求与角色死亡转成 game.body.* 事件"
    stream_label = "任务事件流"

    async def _forward(self, event: Dict[str, Any]) -> bool:
        """一条上游事件 → 一条叙事化 ``game.body.*`` 事件；目标运行的处境变化返回 False。"""
        source_type = str(event.get("kind") or "")
        kind = classify(source_type)
        if kind is None:
            return False
        payload = BodyEventPayload(
            game="minecraft",
            kind=kind,
            summary=summarize(kind, source_type, str(event.get("message") or "")),
            source_event_type=source_type,
            resolved=kind in RESOLVED_KINDS,
        )
        await self.emit_event(KIND_TO_EVENT[kind], payload, source=self.name)
        # 与日志同处落一条：事件面给程序看，日志给人复盘看。
        self.logger.info(f"[身体事件] {payload.summary}（{payload.source_event_type}）")
        return True


__all__ = ["MaicraftAttentionCollector"]
