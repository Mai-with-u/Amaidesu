"""
MaicraftAttentionCollector —— AI 玩家身体事件采集器

上游是 MaiCraft v1 的事件流（``events`` 默认的 ``self``：角色身上发生的事）：挨了一阵打、换了维度、生存需求插进来的
临时任务（自卫、夜里封顶自保、退离边沿）开始与结束、角色自己处理不了的需求、角色死亡，都写在这条流上。本采集器**常驻**带着游标长轮询，
把其中的身体遭遇转成 ``game.body.*`` 事件（别的玩家冲角色做的肢体动作单独成一种，不当挨打讲）；
步进完成（主播可讲的进展）转成 ``game.milestone``；
目标运行的处境变化归游戏 Agent 的任务通道，不转。
连接、长轮询与游标都在 :class:`MaicraftEventsCollector`，这里只管分类与转发。

为什么是独立的流而不是让游戏 Agent 兼职转发：
- 它是**持续流**（无目标、无起止、生命周期挂装配期）——按三问判据归采集器；
- 游戏 Agent 只在**干活时**需要身体事实（用来调整任务），主播侧**一直**需要
  叙事素材；把后者绑在前者的忙碌状态与重建上，等于让主播的叙事随时断档。

连接与游标自带一份（不共用游戏 Agent 的 MCP 连接）：事件流按读者各自的游标读，
两边互不影响；Mod 侧支持多会话并行。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.agents.minecraft.attention_matrix import (
    KIND_TO_EVENT,
    RESOLVED_KINDS,
    classify,
    summarize,
    without_coordinates,
)
from src.agents.minecraft.chosen_death import CHOSEN_DEATH_LINE, SuicideWatch
from src.agents.minecraft.events_collector import MaicraftEventsCollector
from src.agents.minecraft.others_food import OTHERS_FOOD_TAKEN, food_taken_of, spoken_line
from src.agents.minecraft.player_body_language import PLAYER_BODY_LANGUAGE
from src.agents.minecraft.player_body_language import spoken_line as body_language_line
from src.agents.minecraft.player_interactions import body_language_interactions
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.events.payloads.game import GamePayload
from src.modules.utils.time_utils import now_ms


class MaicraftAttentionCollector(MaicraftEventsCollector):
    """AI 玩家身体事件采集器（常驻长轮询 MaiCraft 的任务事件流）。"""

    name = "maicraft_attention"
    description = (
        "常驻读 MaiCraft 的任务事件流，把挨打、换维度、身体先处理的急事、处理不了的需求与角色死亡"
        "转成 game.body.* 事件；步进完成的进展转成 game.milestone"
    )
    stream_label = "任务事件流"

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
        client_factory: Optional[Callable[..., Any]] = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        super().__init__(config=config, event_bus=event_bus, client_factory=client_factory)
        self._clock = clock
        # 正在跑的寻死目标：死讯落在它跑的时候，就是自己选的死（回家），不当出事讲
        self._suicides = SuicideWatch(clock)

    async def _teardown(self) -> None:
        await super()._teardown()
        self._suicides.clear()

    async def _forward(self, event: Dict[str, Any]) -> bool:
        """一条上游事件 → 一条叙事化 ``game.body.*`` 事件（步进完成转 ``game.milestone``）；其余处境变化返回 False。"""
        source_type = str(event.get("kind") or "")
        # 目标的开始与结束不进叙事，但要先看一眼：寻死开始了就记下，死讯来时才认得出是不是自己要死的
        self._suicides.observe(event)
        if source_type == OTHERS_FOOD_TAKEN:
            return await self._forward_food_taken(event)
        if source_type == PLAYER_BODY_LANGUAGE:
            return await self._forward_body_language(event)
        kind = classify(source_type)
        if kind is None:
            return False
        if kind == "milestone":
            # 步进完成是主播可讲的进展：Mod 自带结论句，去坐标/观察编号后直接讲；
            # 不走 summarize()（那是为身体事件配的开头句式）。
            message = str(event.get("message") or "")
            world = self._page_world
            payload = GamePayload(
                game="minecraft",
                event_type="milestone",
                message=without_coordinates(message),
                save_id=world.id if world is not None else "",
                save_name=world.name if world is not None else "",
            )
            await self.emit_event(KIND_TO_EVENT[kind], payload, source=self.name)
            self.logger.info(f"[进展事件] {payload.message}（{source_type}）")
            return True
        summary = summarize(kind, source_type, str(event.get("message") or ""))
        if kind == "died" and self._suicides.chosen():
            # 寻死跑着的时候死的：开着死亡不掉落回重生点，是回家的办法，主播轻松带过，不当事故讲、不当伤心事记
            summary = CHOSEN_DEATH_LINE
        payload = BodyEventPayload(
            game="minecraft",
            kind=kind,
            summary=summary,
            source_event_type=source_type,
            resolved=kind in RESOLVED_KINDS,
        )
        await self.emit_event(KIND_TO_EVENT[kind], payload, source=self.name)
        # 与日志同处落一条：事件面给程序看，日志给人复盘看。
        self.logger.info(f"[身体事件] {payload.summary}（{payload.source_event_type}）")
        return True

    async def _forward_food_taken(self, event: Dict[str, Any]) -> bool:
        """拿了别人家的吃的：连同回应口径转给主播（主人来问时由主播在游戏聊天里认下）。

        事件面还没有这一种的专名，走 ``unknown`` 并留上游种类；一句话由归属与 Mod 原话拼出，
        保留主人名和区域名（对号要用），去掉坐标。
        """
        payload = BodyEventPayload(
            game="minecraft",
            kind="unknown",
            summary=spoken_line(food_taken_of(event, self._clock())),
            source_event_type=OTHERS_FOOD_TAKEN,
            resolved=True,
        )
        await self.emit_event(KIND_TO_EVENT["unknown"], payload, source=self.name)
        self.logger.info(f"[身体事件] {payload.summary}（{OTHERS_FOOD_TAKEN}）")
        return True

    async def _forward_body_language(self, event: Dict[str, Any]) -> bool:
        """别的玩家冲角色做了肢体动作：转给主播一句能顺口接的话，连同"这是在跟我玩、俏皮接、不提血量"的口径。

        不走 :func:`summarize`：那套白话化会把带数字的分句整句去掉，"朝你扔了 3 个面包"会整句没了；
        这里只去观察编号和方位距离，动作和送的东西照说。被拍一下也在这里，不当挨打讲。
        """
        # 叙事种类和 Mod 的事件种类同名
        payload = BodyEventPayload(
            game="minecraft",
            kind=PLAYER_BODY_LANGUAGE,
            summary=body_language_line(str(event.get("message") or "")),
            source_event_type=PLAYER_BODY_LANGUAGE,
            resolved=PLAYER_BODY_LANGUAGE in RESOLVED_KINDS,
        )
        await self.emit_event(KIND_TO_EVENT[PLAYER_BODY_LANGUAGE], payload, source=self.name)
        self.logger.info(f"[身体事件] {payload.summary}（{PLAYER_BODY_LANGUAGE}）")
        # 叙事发完再按事件上的玩家 UUID 记一笔互动（打招呼，扔给她东西的另记送东西）：只给存储记账，
        # 主播不订阅，她当场说什么不变；没带 UUID 的认不出是谁，不记
        world = self._page_world
        for interaction in body_language_interactions(
            event, save_id=world.id if world is not None else "", save_name=world.name if world is not None else ""
        ):
            await self.emit_event(CoreEvents.GAME_PLAYER_INTERACTED, interaction, source=self.name)
        return True


__all__ = ["MaicraftAttentionCollector"]
