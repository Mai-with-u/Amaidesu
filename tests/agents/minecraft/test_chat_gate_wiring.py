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
from src.modules.storage.models.game_players import GamePlayer
from src.modules.tools.models import ToolInvocation
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


async def test_online_players_not_nearby_never_triggers_nearby_fallback_wake() -> None:
    """视野外在线玩家名单绝不混入 chat_gate 近身名单，同时脱敏情境仍保留全服在线名单。"""
    clock = FakeClock()
    server = FakeMaicraft()
    server.self_view["position"] = {"x": 0.0, "y": 64.0, "z": 0.0, "dimension": "minecraft:overworld"}
    server.scene_view["entities"] = [
        {"id": "e1", "type": "minecraft:player", "name": "PlayerNear", "direction": "前方", "distance": 6},
        {"id": "e2", "type": "minecraft:player", "name": "PlayerFar", "direction": "后方", "distance": 45},
    ]
    server.scene_view["online_players_not_nearby"] = ["PlayerRemote"]

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _m: _resp("收到，继续推进"),
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
            await coord.request_game_lead(LeadHandoverContext(segment_title="视野外玩家隔离测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)

        # 触发一次速览刷新（经公共工具调用同步近身实体名单与全服脱敏名单）
        glance_result = await registry.invoke(
            ToolInvocation(tool_name="minecraft_glance", arguments={}, source="test", invoking_agent="streamer")
        )
        assert glance_result.success is True
        situation = registry.current_situations(for_agent="streamer")["minecraft"]
        assert situation.players == ("PlayerNear", "PlayerFar", "PlayerRemote")

        # 视野外全服在线玩家与超距实体玩家发 sender_distance=None 的普通公屏消息：
        # 绝不降级假标为 [游戏公屏·身边]，也不触发唤醒
        for sender, msg in (("PlayerRemote", "我在主城修仓库"), ("PlayerFar", "我也在四十五格外")):
            await bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(
                    game="minecraft",
                    kind="player",
                    sender=sender,
                    sender_distance=None,
                    content=msg,
                    timestamp_ms=clock.now_ms(),
                ),
                source="test",
            )
        await settle(bus)
        clock.tick(3_000)
        assert await agent.flush_pending_chat() is False
        assert len(llm.seen) == 1

        # 真实身边玩家（≤24格）发 sender_distance=None 的普通公屏消息：
        # 正确降级标为 [游戏公屏·身边] 并按窗唤醒，且先前两名远处玩家的发言仅作为 [游戏公屏旁听] 的 [游戏公屏·远处] 附入
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerNear",
                sender_distance=None,
                content="你看看这个箱子",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await settle(bus)
        clock.tick(2_000)
        assert await agent.flush_pending_chat() is True
        await _wait_until(lambda: len(llm.seen) == 2 and not agent.batch_active)

        step2_text = _texts(llm.seen[1])
        assert "[游戏公屏·身边] PlayerNear：你看看这个箱子" in step2_text
        assert "[游戏公屏·远处] PlayerRemote：我在主城修仓库" in step2_text
        assert "[游戏公屏·远处] PlayerFar：我也在四十五格外" in step2_text
        assert "[游戏公屏·身边] PlayerRemote" not in step2_text
        assert "[游戏公屏·身边] PlayerFar" not in step2_text
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_on_game_chat_renders_registered_player_aliases_and_degrades_on_repo_error() -> None:
    """实时公屏接线：game_players 有常用称呼时发言人渲染为「Name（常叫：Alias）」，异常或无别名时平滑退回原名。"""
    clock = FakeClock(500_000)
    server = FakeMaicraft()
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    class _FakeGamePlayerRepo:
        def __init__(self) -> None:
            self.should_fail = False
            self.calls = 0

        async def list_players(self, *, game: str | None = None, limit: int = 200) -> List[GamePlayer]:
            del game, limit
            self.calls += 1
            if self.should_fail:
                raise RuntimeError("模拟仓储瞬时故障")
            return [
                GamePlayer(
                    id=1,
                    game="minecraft",
                    uuid="uuid-alpha",
                    name="PlayerAlpha",
                    names=["PlayerAlpha"],
                    aliases=["阿甲"],
                    first_seen_ms=1_000,
                    last_seen_ms=2_000,
                ),
                GamePlayer(
                    id=2,
                    game="minecraft",
                    uuid="uuid-beta",
                    name="PlayerBeta",
                    names=["PlayerBeta"],
                    aliases=["阿乙"],
                    first_seen_ms=1_000,
                    last_seen_ms=2_000,
                ),
                GamePlayer(
                    id=3,
                    game="minecraft",
                    uuid="uuid-forgotten",
                    name="PlayerGhost",
                    names=["PlayerGhost"],
                    aliases=["不应出现"],
                    first_seen_ms=1_000,
                    last_seen_ms=2_000,
                    forget_requested_at_ms=5_000,
                ),
            ]

    repo = _FakeGamePlayerRepo()
    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _msgs: _resp("收到公屏消息"),
        registry=registry,
        bus=bus,
        live=True,
        game_player_repo=repo,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=2_000,
        chat_flush_wait=no_real_sleep,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="实时别名测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)

        # 远处玩家（带别名）进旁听滑窗；身边玩家（带别名）、已遗忘玩家与无别名玩家进唤醒合并窗
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerBeta",
                sender_id="uuid-beta",
                sender_online=True,
                sender_distance=70.0,
                content="我在河对岸种小麦",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerAlpha",
                sender_id="uuid-alpha",
                sender_online=True,
                sender_distance=8.0,
                content="铁锭放主箱里了",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerGhost",
                sender_id="uuid-forgotten",
                sender_online=True,
                sender_distance=9.0,
                content="路过打个招呼",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerPlain",
                sender_id="uuid-plain",
                sender_online=True,
                sender_distance=10.0,
                content="工具台修好了",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await settle(bus)
        # 四条公屏共享 TTL 缓存，只查一次库
        assert repo.calls == 1

        clock.tick(2_000)
        assert await agent.flush_pending_chat() is True
        await _wait_until(lambda: len(llm.seen) == 2 and not agent.batch_active)

        step2_text = _texts(llm.seen[1])
        assert "[游戏公屏·身边8格] PlayerAlpha（常叫：阿甲）：铁锭放主箱里了" in step2_text
        assert "[游戏公屏·远处] PlayerBeta（常叫：阿乙）：我在河对岸种小麦" in step2_text
        assert "[游戏公屏·身边9格] PlayerGhost：路过打个招呼" in step2_text
        assert "不应出现" not in step2_text
        assert "[游戏公屏·身边10格] PlayerPlain：工具台修好了" in step2_text

        # TTL 过期后仓储抛异常：绝不阻塞或崩溃，公屏照常投递
        repo.should_fail = True
        clock.tick(70_000)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerPlain",
                sender_id="uuid-plain",
                sender_online=True,
                sender_distance=6.0,
                content="仓储异常时也能收到这句话",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await settle(bus)
        assert repo.calls == 2
        clock.tick(2_000)
        assert await agent.flush_pending_chat() is True
        await _wait_until(lambda: len(llm.seen) == 3 and not agent.batch_active)
        assert "[游戏公屏·身边6格] PlayerPlain：仓储异常时也能收到这句话" in _texts(llm.seen[2])
    finally:
        await agent.stop()
        await bus.cleanup()
