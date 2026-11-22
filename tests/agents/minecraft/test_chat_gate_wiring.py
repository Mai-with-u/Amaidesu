"""MinecraftAgent 游戏公屏门控、合批定时器与并发状态接线测试（假时钟、零真实 sleep）。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from src.modules.agents import AgentManager, LeadCoordinator, LeadHandoverContext
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatMention, GameChatPayload
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.llm.payload import Response
from src.modules.tools.registry import ToolRegistry
from tests.agents.minecraft._wiring_helpers import StreamerPlaceholder, no_real_sleep, settle
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)
from tests.helpers import FakeClock


async def test_chat_gate_batching_distance_eavesdrop_partner_window_and_session_clear() -> None:
    """主控态游戏公屏全链路：2 秒合批、刷屏折叠、24 格远处旁听不唤醒/下步附尾、chat 与互动激活 2 分钟同伴窗、下播清空。"""
    clock = FakeClock()
    server = FakeMaicraft()
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    reply_partner = [False]

    def script(messages: List[Dict[str, Any]]) -> Response:
        joined = _texts(messages)
        if "帮我修下熔炉" in joined and not reply_partner[0]:
            reply_partner[0] = True
            return _resp(
                "游戏公屏回复远方同伴丙",
                calls=[
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "chat",
                                "target": {"kind": "player", "name": "远方同伴丙"},
                                "parameters": {"message": "马上过来看看！"},
                            }
                        },
                        "c_reply_partner",
                    )
                ],
            )
        return _resp("收到，继续观察")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=2_000,
        chat_flush_wait=no_real_sleep,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="联机合批测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)

        # 1) 远处非点名游戏公屏（60 格）：进入旁听滑窗，不主动唤醒 LLM
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="路人远甲",
                sender_distance=60.0,
                content="主城今天卖南瓜吗",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await settle(bus)
        clock.tick(3_000)
        assert await agent.flush_pending_chat() is False
        assert len(llm.seen) == 1

        # 2) 2 秒合批窗内来三条消息（身边同伴 + 同人刷屏 3 次 + 点名你）：不到 2 秒不唤醒，满 2 秒合为一条 [游戏公屏合批]
        # 且之前攒的远处 [游戏公屏旁听] 在这次正常推理步顺带附到尾部
        for _ in range(3):
            await bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(
                    game="minecraft",
                    kind="player",
                    sender="同伴近甲",
                    sender_distance=10.0,
                    content="快来看箱子",
                    timestamp_ms=clock.now_ms(),
                ),
                source="test",
            )
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="远方同伴丙",
                sender_distance=50.0,
                content="麦麦帮我修下熔炉",
                mentions=[GameChatMention(name="麦麦", type="self", id="")],
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await settle(bus)
        # 推进 1000ms 未到 2 秒窗：不刷出
        clock.tick(1_000)
        assert await agent.flush_pending_chat() is False
        assert len(llm.seen) == 1

        # 满 2000ms 到窗刷出 -> 唤醒推理步 2
        clock.tick(1_000)
        assert await agent.flush_pending_chat() is True
        await _wait_until(lambda: reply_partner[0] and not agent.batch_active)

        step2_text = _texts(llm.seen[1])
        assert "[游戏公屏合批]" in step2_text
        assert "[游戏公屏·身边10格] 同伴近甲：快来看箱子（连发 ×3）" in step2_text
        assert "[游戏公屏·点名你] 远方同伴丙：麦麦帮我修下熔炉" in step2_text
        assert "[游戏公屏旁听]" in step2_text
        assert "[游戏公屏·远处] 路人远甲：主城今天卖南瓜吗" in step2_text

        # 3) 角色刚刚对“远方同伴丙”发了 chat -> 激活 2 分钟互动同伴窗；
        # 此时他在 50 格外发非点名游戏公屏仍能进入唤醒窗（因触发了前面刷屏折叠冷却，需推进冷却 6s 后刷出）
        turns_before_partner = len(llm.seen)
        clock.tick(10_000)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="远方同伴丙",
                sender_distance=50.0,
                content="漏斗口朝东边哦",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await settle(bus)
        clock.tick(6_000)
        assert await agent.flush_pending_chat() is True
        await _wait_until(lambda: len(llm.seen) > turns_before_partner and not agent.batch_active)
        assert "远方同伴丙：漏斗口朝东边哦" in _texts(llm.seen[-1])

        # 4) 超过 2 分钟后不再是互动同伴 -> 50 格外非点名游戏公屏重新回退旁听不唤醒
        turns_before_expired = len(llm.seen)
        clock.tick(130_000)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="远方同伴丙",
                sender_distance=50.0,
                content="过了两分钟随便聊聊",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await settle(bus)
        clock.tick(3_000)
        assert await agent.flush_pending_chat() is False
        assert len(llm.seen) == turns_before_expired

        # 5) 验证本场短期记忆可查到刚才游戏公屏，下播 live.ended 后清空本场短期记忆与门控
        hits_before_end = await agent.query_short_term_memory("漏斗口朝东边")
        assert any("漏斗口朝东边哦" in item["text"] for item in hits_before_end)

        await bus.emit(
            CoreEvents.LIVE_ENDED,
            LiveEndedPayload(live_session_id=501, ended_at_ms=clock.now_ms()),
            source="test",
        )
        await settle(bus)
        assert list(await agent.query_short_term_memory("漏斗口朝东边")) == []

        # 新开播 live.started 也保持干净起跑
        await bus.emit(
            CoreEvents.LIVE_STARTED,
            LiveStartedPayload(live_session_id=502, kind="live", started_at_ms=clock.now_ms()),
            source="test",
        )
        await settle(bus)
        assert list(await agent.query_short_term_memory("快来看箱子")) == []
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_chat_flush_timer_concurrent_reschedule_yield_cleanup_and_mid_await_guard() -> None:
    """验证合批定时器跨 await 新消息续调度、yield_lead 清门控脏窗口、以及跨 await 退主控拒脏投递。"""
    clock = FakeClock()
    server = FakeMaicraft()
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    # 场景 A：第 1 批合批在拉取被动线索期间让出事件循环，期间到达第 2 批游戏公屏 -> 定时器 loop 续调度不漏批
    enter_first_cue = asyncio.Event()
    release_first_cue = asyncio.Event()
    cue_round = {"n": 0}

    async def _gated_cues(
        *,
        game: str,
        texts: Any = (),
        players: Any = (),
        situation: Any = None,
        arrived_places: Any = (),
        limit: int = 2,
    ) -> List[str]:
        del game, texts, players, situation, arrived_places, limit
        cue_round["n"] += 1
        if cue_round["n"] == 1:
            enter_first_cue.set()
            await release_first_cue.wait()
        return []

    coord.set_passive_cue_provider(_gated_cues)

    # 门控等待器：仅在测试显式放行时推进假时钟并返回，避免任何真实 sleep
    wait_gate = asyncio.Event()

    async def _gated_wait(delay_ms: int) -> None:
        await wait_gate.wait()
        wait_gate.clear()
        clock.tick(delay_ms)

    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _msgs: _resp("收到游戏公屏消息"),
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=2_000,
        chat_flush_wait=_gated_wait,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="定时器并发测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)

        # 发第 1 条身边游戏公屏并放行定时器到点 -> 卡在 _gated_cues 第 1 次 await
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴甲",
                sender_distance=6.0,
                content="第一批并发游戏公屏",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        wait_gate.set()
        await asyncio.wait_for(enter_first_cue.wait(), timeout=2.0)

        # 第 1 批尚在 await 被动线索期间，第 2 条游戏公屏到达开启新窗口
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴乙",
                sender_distance=6.0,
                content="第二批并发续调度游戏公屏",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()

        # 放行第 1 批线索 + 放行第 2 批等待门 -> 旧定时器任务通过 continue 自动刷出第 2 批，不会滞留丢唤醒
        release_first_cue.set()
        await _wait_until(lambda: any("第一批并发游戏公屏" in _texts(s) for s in llm.seen))
        wait_gate.set()
        await _wait_until(
            lambda: any("第二批并发续调度游戏公屏" in _texts(s) for s in llm.seen) and not agent.batch_active
        )

        # 场景 B：yield_lead 清空挂起的待发窗口，重进主控后远处非点名游戏公屏不误入旧窗口也不吐陈旧消息
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴甲",
                sender_distance=6.0,
                content="退出主控前的滞留草稿消息",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        # 在 2 秒窗未到期时切回主播主控（yield_lead 取消定时器并清空待发窗口）
        await coord.reclaim_streamer_lead(reason="rundown_changed")
        clock.tick(10_000)

        # 重新进入游戏主控
        turns_before_reenter = len(llm.seen)
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="重进主控段"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) > turns_before_reenter and not agent.batch_active)
        turns_after_reenter = len(llm.seen)

        # 发送一条 60 格外远处非点名游戏公屏：旧窗口已清，不应被误路由唤醒，也不会吐出“滞留草稿消息”
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="远处路人",
                sender_distance=60.0,
                content="远处闲聊不应误唤醒",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await settle(bus)
        clock.tick(5_000)
        assert await agent.flush_pending_chat() is False
        assert len(llm.seen) == turns_after_reenter
        assert all("退出主控前的滞留草稿消息" not in _texts(s) for s in llm.seen[turns_before_reenter:])

        # 场景 C：在 _deliver_due_chat_batch 跨 await 被动线索期间发生 yield_lead -> 恢复后不向队列脏投递
        enter_c = asyncio.Event()
        release_c = asyncio.Event()

        async def _c_cues(**_kwargs: Any) -> List[str]:
            enter_c.set()
            await release_c.wait()
            return ["晚到的线索"]

        coord.set_passive_cue_provider(_c_cues)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴甲",
                sender_distance=5.0,
                content="跨await退主控测试消息",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        clock.tick(2_000)
        flush_task = asyncio.create_task(agent.flush_pending_chat())
        await asyncio.wait_for(enter_c.wait(), timeout=2.0)

        # 在 flush_pending_chat 仍挂在 await _c_cues 上时立即收权退出主控
        await coord.reclaim_streamer_lead(reason="mid_await_reclaim")
        release_c.set()
        assert await flush_task is False
        assert len(llm.seen) == turns_after_reenter
    finally:
        await agent.stop()
        await bus.cleanup()
