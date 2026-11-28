"""MinecraftAgent 系统聊天转发节流、流程单切段刷新、记忆动态可见、抗折叠钉住规矩与行为提示词回归测试。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterable, List, Tuple
from unittest.mock import AsyncMock

from src.agents.minecraft.config import MinecraftConfig, MinecraftContextConfig
from src.agents.minecraft.context import FACTS_KEY, INSTRUCTION_KEY
from src.agents.minecraft.lead_mode import LEAD_MODE_PROMPT_SECTION
from src.agents.minecraft.state import MAX_PINNED_RULES, MAX_RULE_CHARS
from src.agents.minecraft.tools import build_glance_spec
from src.agents.streamer.memory.recall import (
    MEMORY_RECALL_SPEC,
    MEMORY_RECALL_TOOL_NAME,
    MemoryRecallService,
    build_memory_recall_provider,
)
from src.modules.agents.base import BaseAgent
from src.modules.agents.lead import LeadCoordinator, LeadHandoverContext
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameChatMention, GameChatPayload
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.registry import ToolRegistry
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
    offline_config,
)


def _fold_config(**context: Any) -> MinecraftConfig:
    """离线配置 + 小预算：快速触发后台折叠以验证钉住规矩固定层不被折叠稀释。"""
    settings: Dict[str, Any] = {"max_tokens": 40_000, "fold_keep_recent": 1, **context}
    return offline_config(events_wait_ms=1000).model_copy(update={"context": MinecraftContextConfig(**settings)})


def _is_fold_request(messages: List[Dict[str, Any]]) -> bool:
    content = messages[-1].get("content")
    return isinstance(content, str) and content.startswith("[宿主整理请求]")


def _listed_ranges(messages: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    return re.findall(r"^- (\S+)–(\S+)：", str(messages[-1]["content"]), re.M)


# ======================================================================
# 非死亡系统消息放行、进退服噪声过滤与 15 秒节流合并
# ======================================================================


async def test_system_chat_ignored_when_idle_and_forwarded_in_lead_or_active_task() -> None:
    """空闲态严格零消耗忽略非死亡系统提示；游戏主控态与活跃委派态放行权限/插件报错并滤掉进退服噪声。"""
    now_s = [100.0]
    bus = EventBus(enable_stats=False)
    server = FakeMaicraft()
    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _m: _resp("收到系统提示"),
        bus=bus,
        live=True,
        clock=lambda: now_s[0],
    )
    try:
        # 空闲态：非死亡系统反馈不入队、不唤醒、不调模型
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(game="minecraft", kind="system", content="你没有权限在此区域破坏方块"),
            source="test",
        )
        await bus.wait_until_drained()
        assert agent.queued_messages == []
        assert len(llm.seen) == 0

        # 进入游戏主控态：开局先消费初始交接简报
        entered = await agent.enter_lead(
            LeadHandoverContext(segment_id="seg-1", segment_title="矿洞探索", segment_goal="采铁")
        )
        assert entered is True
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)
        calls_before = len(llm.seen)

        # 纯进退服噪声与空白系统消息均被过滤，不唤醒模型
        for noise_payload in (
            GameChatPayload(game="minecraft", kind="system", content="   "),
            GameChatPayload(
                game="minecraft",
                kind="system",
                content="Alex 加入了游戏",
                message_key="multiplayer.player.joined",
            ),
            GameChatPayload(
                game="minecraft",
                kind="system",
                content="Alex left the game",
                message_key="multiplayer.player.left",
            ),
            GameChatPayload(game="minecraft", kind="system", content="Steve 退出了游戏"),
            GameChatPayload(game="minecraft", kind="system", content="[+] PartnerA"),
        ):
            await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, noise_payload, source="test")
        await bus.wait_until_drained()
        assert len(llm.seen) == calls_before

        # 真实服务器权限拦截提示立即以 [系统提示] 入队并唤醒游戏侧推理
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="system",
                content="你没有权限在当前领地内交互容器",
                message_key="server.claim.denied",
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) > calls_before and not agent.batch_active)
        assert "[系统提示] 你没有权限在当前领地内交互容器" in _texts(llm.seen[-1])
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_system_chat_throttles_burst_within_15s_and_merges_count_while_keeping_death_detection() -> None:
    """15 秒内同前缀服务器提示合并计数不刷爆上下文，满窗后汇总汇报；生物死讯识别逻辑完整保留。"""
    now_s = [200.0]
    bus = EventBus(enable_stats=False)
    milestones: List[GamePayload] = []

    async def _on_milestone(_name: str, payload: GamePayload, _src: str) -> None:
        milestones.append(payload)

    bus.on(CoreEvents.GAME_MILESTONE, _on_milestone, model_class=GamePayload)
    server = FakeMaicraft()
    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _m: _resp("知道了"),
        bus=bus,
        live=True,
        clock=lambda: now_s[0],
    )
    try:
        # 进入游戏主控态并消费完开局简报后暂停 worker，以便精确核查 15 秒窗口内的入队与合并结果
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-1", segment_title="领地整理", segment_goal="核对边界")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)
        calls_before = len(llm.seen)
        await agent.pause()

        # 同前缀坐标变化的 4 条选区提示在 15 秒内到达：仅首条立即入队，后续 3 条合并计数不刷屏
        for step_idx in range(4):
            now_s[0] = 200.0 + step_idx * 2.0
            await bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(
                    game="minecraft",
                    kind="system",
                    content=f"First position set to ({10 + step_idx}, 64, 20)",
                ),
                source="test",
            )
            await bus.wait_until_drained()
        assert len(agent.queued_messages) == 1
        assert agent.queued_messages[0][1] == "[系统提示] First position set to (10, 64, 20)"

        # 不同内容的权限提示不受上面归并键影响，立即独立入队
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(game="minecraft", kind="system", content="该区域受领地保护，无法放置方块"),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(agent.queued_messages) == 2
        assert agent.queued_messages[1][1] == "[系统提示] 该区域受领地保护，无法放置方块"

        # 推进假时钟超过 15 秒后再来同类提示：连同前 15 秒合并计数一并入队，不丢频次事实
        now_s[0] = 216.0
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(game="minecraft", kind="system", content="First position set to (99, 64, 20)"),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(agent.queued_messages) == 3
        assert "First position set to (99, 64, 20)" in agent.queued_messages[2][1]
        assert "合并 3 次" in agent.queued_messages[2][1]

        # 未命中生物名册的死亡翻译键（如在线玩家或未登记实体死讯）在主控态走 [系统提示] 入队，不发名册里程碑
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="system",
                content="某个陌生村民死了",
                message_key="death.attack.mob",
                mentions=[GameChatMention(name="村民", type="minecraft:villager", id="u-unknown")],
            ),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(agent.queued_messages) == 4
        assert agent.queued_messages[3][1] == "[系统提示] 某个陌生村民死了"
        assert milestones == []
        assert len(llm.seen) == calls_before
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_player_death_chat_routes_by_control_state_and_preserves_creature_milestone() -> None:
    """玩家 death.* 在主控态与活跃委派态走 [系统提示]，空闲态零消耗忽略，已登记宠物仍优先走名册里程碑。"""
    now_s = [250.0]
    bus = EventBus(enable_stats=False)
    milestones: List[GamePayload] = []

    async def _on_milestone(_name: str, payload: GamePayload, _src: str) -> None:
        milestones.append(payload)

    bus.on(CoreEvents.GAME_MILESTONE, _on_milestone, model_class=GamePayload)
    server = FakeMaicraft()
    # 场景里放一只属于角色自己的命名驯服狼，经公开 minecraft_glance 自动登记进生物名册
    server.scene_view["entities"] = [
        {
            "id": "e10",
            "type": "minecraft:wolf",
            "name": "小灰",
            "uuid": "wolf-uuid-1",
            "traits": {"驯服": "你的"},
            "direction": "前方",
            "distance": 4,
            "hostile": False,
        }
    ]
    registry = ToolRegistry()
    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _m: _resp("收到死讯"),
        bus=bus,
        registry=registry,
        live=True,
        clock=lambda: now_s[0],
    )
    try:
        # 通过公开工具 minecraft_glance 读一眼场景，使生物名册自动建档“小灰”
        glance_result = await registry.invoke(ToolInvocation(tool_name="minecraft_glance", arguments={"what": "scene"}))
        assert glance_result.success is True

        # 空闲态：玩家 death.* 严格零消耗忽略，不入队也不唤醒模型
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="system",
                content="PartnerA 试图在岩浆里游泳",
                message_key="death.attack.lava",
            ),
            source="test",
        )
        await bus.wait_until_drained()
        assert agent.queued_messages == []
        assert len(llm.seen) == 0

        # 进入游戏主控态并消费完开局简报
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-death", segment_title="联机探险", segment_goal="探索下界")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)
        calls_before = len(llm.seen)

        # 主控态下在线玩家 death.* 经 _enqueue_system_chat_notice 注入 [系统提示] 并唤醒游戏侧推理
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="system",
                content="PartnerA 试图在岩浆里游泳",
                message_key="death.attack.lava",
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) > calls_before and not agent.batch_active)
        assert "[系统提示] PartnerA 试图在岩浆里游泳" in _texts(llm.seen[-1])
        assert milestones == []

        # 已登记宠物死讯仍优先命中生物名册并上报里程碑，不再重复当普通 [系统提示] 唤醒游戏侧
        calls_after_player_death = len(llm.seen)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="system",
                content="小灰 被僵尸杀死了",
                message_key="death.attack.mob",
                mentions=[GameChatMention(name="小灰", type="minecraft:wolf", id="wolf-uuid-1")],
            ),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(milestones) == 1
        assert "小灰" in milestones[0].message
        assert len(llm.seen) == calls_after_player_death
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_expired_throttled_system_chat_flushes_into_current_reasoning_step() -> None:
    """满 15 秒节流窗的汇总系统提示在队列消费前排空，确保紧接着的那一步推理即可直接读到。"""
    now_s = [300.0]
    bus = EventBus(enable_stats=False)
    server = FakeMaicraft()
    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _m: _resp("收到汇总"),
        bus=bus,
        live=True,
        clock=lambda: now_s[0],
    )
    try:
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-1", segment_title="领地整理", segment_goal="核对边界")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)

        # 首条系统提示立刻触发一步推理；窗内后续 2 条重复提示先累计计数，不刷屏
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(game="minecraft", kind="system", content="你没有权限破坏此方块"),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)

        for offset in (2.0, 4.0):
            now_s[0] = 300.0 + offset
            await bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(game="minecraft", kind="system", content="你没有权限破坏此方块"),
                source="test",
            )
            await bus.wait_until_drained()

        # 假时钟跨过 15 秒窗后，下一条普通事件唤醒推理时应在本步直接读到重复 2 次的汇总提示
        calls_before_wake = len(llm.seen)
        now_s[0] = 320.0
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(game="minecraft", kind="player", sender="同伴甲", content="这边换个位置吧"),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) > calls_before_wake and not agent.batch_active)
        assert "15 秒内重复 2 次" in _texts(llm.seen[calls_before_wake])
    finally:
        await agent.stop()
        await bus.cleanup()


# ======================================================================
# 流程单切段更新刷新固定层交接简报，消除整场冻结
# ======================================================================


async def test_receive_rundown_update_refreshes_pinned_handover_brief_and_notifies_model() -> None:
    """主控态收到流程单切段更新时：原地刷新固定层交接简报 + 追加 [流程单切换] 通知，旧环节名不再残留。"""
    server = FakeMaicraft()
    agent, llm, _bus = await make_minecraft_agent(
        server,
        lambda _m: _resp("按当前环节继续"),
        live=True,
    )
    try:
        # 非主控态调用为安全空操作
        agent.receive_rundown_update(segment_id="seg-ignored", segment_title="不应生效", segment_goal="无")
        assert agent.queued_messages == []

        # 进入游戏主控态：开场环节为“开场交代”
        assert (
            await agent.enter_lead(
                LeadHandoverContext(
                    segment_id="seg-open",
                    segment_title="开场交代",
                    segment_goal="先和观众打个招呼并检查营地物资",
                    expected_ms=300_000,
                )
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)
        assert "开场交代" in _texts(llm.seen[0])

        # 100 分钟后流程单切段到“机械动力工厂扩建”
        calls_before = len(llm.seen)
        agent.receive_rundown_update(
            segment_id="seg-factory",
            segment_title="机械动力工厂扩建",
            segment_goal="搭好动力磨粉机与储物传送带",
            expected_ms=2_400_000,
        )
        await _wait_until(lambda: len(llm.seen) > calls_before and not agent.batch_active)

        latest = llm.seen[-1]
        # 固定层交接简报已原地刷新，完全不含旧环节“开场交代”
        pinned_instructions = [m for m in latest if m.get(INSTRUCTION_KEY)]
        assert len(pinned_instructions) == 1
        assert "机械动力工厂扩建" in str(pinned_instructions[0]["content"])
        assert "搭好动力磨粉机与储物传送带" in str(pinned_instructions[0]["content"])
        assert "开场交代" not in str(pinned_instructions[0]["content"])
        # 同时附带紧凑 [流程单切换] 通知
        assert "[流程单切换]" in _texts(latest)
        assert "开场交代" not in _texts(latest)

        # 相同环节重复通知幂等，不再唤醒也不重复入队
        calls_after_switch = len(llm.seen)
        agent.receive_rundown_update(
            segment_id="seg-factory",
            segment_title="机械动力工厂扩建",
            segment_goal="搭好动力磨粉机与储物传送带",
            expected_ms=2_400_000,
        )
        for _ in range(4):
            await asyncio.sleep(0)
        assert len(llm.seen) == calls_after_switch
    finally:
        await agent.stop()


async def test_receive_rundown_update_finish_clears_pinned_brief_and_passes_segment_notes() -> None:
    """普通切段透传有效备注并滤掉占位模板；流程单 finish 绝不早退，清空固定层旧环节目标并切换为自由游玩。"""
    server = FakeMaicraft()
    agent, llm, _bus = await make_minecraft_agent(
        server,
        lambda _m: _resp("明白，继续游玩"),
        live=True,
    )
    try:
        assert (
            await agent.enter_lead(
                LeadHandoverContext(
                    segment_id="seg-build",
                    segment_title="营地扩建",
                    segment_goal="搭好自动化仓库",
                    expected_ms=1_800_000,
                )
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)

        # 切段附带真实备注：固定层与切换通知均包含本场备注/大方向
        calls_before_notes = len(llm.seen)
        agent.receive_rundown_update(
            segment_id="seg-explore",
            segment_title="下界探索",
            segment_goal="找下界堡垒并带回烈焰棒",
            segment_notes="本场大方向：推进主线进度并顺手搜集工业材料\n未填栏：______",
            expected_ms=1_500_000,
        )
        await _wait_until(lambda: len(llm.seen) > calls_before_notes and not agent.batch_active)
        after_notes = llm.seen[-1]
        pinned_after_notes = [m for m in after_notes if m.get(INSTRUCTION_KEY)]
        assert len(pinned_after_notes) == 1
        assert "本场备注/大方向：本场大方向：推进主线进度并顺手搜集工业材料" in str(pinned_after_notes[0]["content"])
        assert "______" not in str(pinned_after_notes[0]["content"])
        assert "本场备注/大方向：" in _texts(after_notes)

        # 切段仅带纯下划线占位备注：不把占位模板塞进固定层简报
        calls_before_blank = len(llm.seen)
        agent.receive_rundown_update(
            segment_id="seg-closing",
            segment_title="末段收尾",
            segment_goal="整理成果准备下播",
            segment_notes="______",
            expected_ms=600_000,
        )
        await _wait_until(lambda: len(llm.seen) > calls_before_blank and not agent.batch_active)
        pinned_closing = [m for m in llm.seen[-1] if m.get(INSTRUCTION_KEY)][0]
        assert "末段收尾" in str(pinned_closing["content"])
        assert "______" not in str(pinned_closing["content"])
        assert "本场备注/大方向：" not in str(pinned_closing["content"])

        # 流程单 finish（即使环节字段全空）：绝不早退，清空末段环节与目标并切为自由游玩简报
        calls_before_finish = len(llm.seen)
        agent.receive_rundown_update(rundown_finished=True)
        await _wait_until(lambda: len(llm.seen) > calls_before_finish and not agent.batch_active)
        latest_after_finish = llm.seen[-1]
        pinned_after_finish = [m for m in latest_after_finish if m.get(INSTRUCTION_KEY)]
        assert len(pinned_after_finish) == 1
        pinned_text = str(pinned_after_finish[0]["content"])
        assert "本场流程单已结束" in pinned_text
        assert "自由游玩阶段" in pinned_text
        assert "末段收尾" not in pinned_text
        assert "整理成果准备下播" not in pinned_text
        assert "当前环节：" not in pinned_text
        assert "环节目标：" not in pinned_text
        assert "[流程单切换] 本场流程单已结束" in _texts(latest_after_finish)

        # 重复推送 finish 幂等，不再次唤醒
        calls_after_finish = len(llm.seen)
        agent.receive_rundown_update(rundown_finished=True)
        for _ in range(4):
            await asyncio.sleep(0)
        assert len(llm.seen) == calls_after_finish
    finally:
        await agent.stop()


async def test_system_chat_renders_advancement_detail_when_present() -> None:
    """成就/进度等系统消息携带 detail 字段时，游戏侧拼成自然说明便于主控直接理解含义。"""
    bus = EventBus(enable_stats=False)
    server = FakeMaicraft()
    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _m: _resp("看到成就了"),
        bus=bus,
        live=True,
    )
    try:
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-1", segment_title="自由探索", segment_goal="探索周边")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)
        calls_before = len(llm.seen)

        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="system",
                content="Alex 达成了进度 [不是今天，谢谢]",
                detail="使用盾牌格挡一次弹射物攻击",
                message_key="chat.type.advancement.task",
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) > calls_before and not agent.batch_active)
        assert "[系统提示] Alex 达成了进度 [不是今天，谢谢]（描述：使用盾牌格挡一次弹射物攻击）" in _texts(llm.seen[-1])
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_receive_silence_nudge_dedupes_unread_and_wakes_background_goal_without_cancelling() -> None:
    """主控态收到直播间静默提醒时不堆积、不打断在跑后台目标，并可唤醒等待中的游戏侧顺手汇报进度。"""
    server = FakeMaicraft()
    step = [0]

    def script(messages: List[Dict[str, Any]]) -> Response:
        step[0] += 1
        if step[0] == 1:
            return _resp(
                "先下达采矿目标",
                calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "采铁矿"}}, "g-1")],
            )
        return _resp("继续等待后台采矿完成")

    agent, llm, _bus = await make_minecraft_agent(server, script, live=True)
    try:
        # 非主控态调用直接忽略
        agent.receive_silence_nudge(silent_ms=80_000)
        assert agent.queued_messages == []

        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-mine", segment_title="矿洞探索", segment_goal="采铁")
            )
            is True
        )
        await _wait_until(lambda: len(agent.tracked_goal_ids) == 1 and not agent.batch_active)
        goal_id = agent.tracked_goal_ids[0]
        assert server.goals[goal_id]["state"] == "running"

        # 暂停 worker 连续投递两次静默提醒：未读时原地替换不堆积，队列仅保留最新时长的一条
        await agent.pause()
        agent.receive_silence_nudge(silent_ms=75_000)
        agent.receive_silence_nudge(silent_ms=95_000)
        assert len(agent.queued_messages) == 1
        assert "[直播间静默提醒]" in agent.queued_messages[0][1]
        assert "95 秒" in agent.queued_messages[0][1]

        # 恢复运行后应立即唤醒模型读到提醒，同时后台在跑目标原样保持 running 不被打断
        calls_before = len(llm.seen)
        await agent.resume()
        await _wait_until(lambda: len(llm.seen) > calls_before and not agent.batch_active)
        assert "[直播间静默提醒]" in _texts(llm.seen[-1])
        assert agent.tracked_goal_ids == [goal_id]
        assert server.goals[goal_id]["state"] == "running"
    finally:
        await agent.stop()


# ======================================================================
# 回忆工具动态可见名单与 Agent 名册热启停联动
# ======================================================================


class _AutonomousGameStub(BaseAgent):
    """最小自主游戏 Agent 桩，用于验证名册注册/注销触发工具可见名单自动刷新。"""

    name = "demo_game"
    description = "测试用自主游戏 Agent"
    supports_autonomous_lead: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__(heartbeat_interval_ms=0)

    def list_tools(self) -> Iterable[ToolSpec]:
        return []


async def test_memory_recall_provider_dynamic_extra_visible_agents() -> None:
    """build_memory_recall_provider 默认仅主播可见；注入动态回调后按需向自主游戏 Agent 暴露且零硬编码游戏名。"""
    service = MemoryRecallService(
        chat_repo=AsyncMock(),
        commitments_repo=AsyncMock(),
        archive_repo=AsyncMock(),
    )
    service.recall = AsyncMock(return_value="【回忆】命中 1 条记录")  # type: ignore[method-assign]

    # 默认行为向后兼容：仅对 streamer 可见，且不污染全局 MEMORY_RECALL_SPEC
    default_provider = build_memory_recall_provider(service)
    reg_default = ToolRegistry()
    reg_default.register_provider(default_provider)
    assert [s.full_name for s in reg_default.list_tools(for_agent="streamer")] == [MEMORY_RECALL_TOOL_NAME]
    assert reg_default.list_tools(for_agent="minecraft") == []
    assert MEMORY_RECALL_SPEC.visible_to is None

    # 动态额外名单：支持随运行态变化（零硬编码具体游戏名）
    extra_agents: List[str] = ["minecraft"]
    dynamic_provider = build_memory_recall_provider(service, extra_visible_agents=lambda: list(extra_agents))
    reg_dyn = ToolRegistry()
    reg_dyn.register_provider(dynamic_provider)
    assert MEMORY_RECALL_TOOL_NAME in [s.full_name for s in reg_dyn.list_tools(for_agent="streamer")]
    assert MEMORY_RECALL_TOOL_NAME in [s.full_name for s in reg_dyn.list_tools(for_agent="minecraft")]
    assert reg_dyn.list_tools(for_agent="other_game") == []

    # 刷新工具清单后新加入的自主 Agent 也能看到 memory_recall
    extra_agents.append("other_game")
    reg_dyn.refresh_provider_tools(dynamic_provider)
    assert MEMORY_RECALL_TOOL_NAME in [s.full_name for s in reg_dyn.list_tools(for_agent="other_game")]

    # 工具调用走同一 MemoryRecallService
    res = await reg_dyn.invoke(
        ToolInvocation(tool_name=MEMORY_RECALL_TOOL_NAME, arguments={"query": "刷铁机"}, invoking_agent="minecraft")
    )
    assert res.success is True
    assert "命中 1 条记录" in res.content

    # 动态回调抛异常时优雅降级回仅主播可见
    def _broken() -> List[str]:
        raise RuntimeError("boom")

    degraded = build_memory_recall_provider(service, extra_visible_agents=_broken)
    specs = list(degraded.list_tools())
    assert len(specs) == 1 and specs[0].visible_to == ["streamer"]


async def test_memory_recall_provider_auto_syncs_visibility_on_agent_manager_register_and_unregister() -> None:
    """先注册回忆工具、后热启用或停用自主游戏 Agent 时，AgentManager 自动刷新 ToolRegistry 可见名单。"""
    service = MemoryRecallService(
        chat_repo=AsyncMock(),
        commitments_repo=AsyncMock(),
        archive_repo=AsyncMock(),
    )
    registry = ToolRegistry()
    manager = AgentManager(tool_registry=registry)
    coordinator = LeadCoordinator(manager)
    provider = build_memory_recall_provider(service, extra_visible_agents=coordinator.lead_capable_agents)
    assert getattr(provider, "syncs_with_agent_roster", False) is True
    registry.register_provider(provider)

    # 尚未启用任何自主游戏 Agent 时，仅 streamer 可见
    assert [s.full_name for s in registry.list_tools(for_agent="streamer")] == [MEMORY_RECALL_TOOL_NAME]
    assert registry.list_tools(for_agent="demo_game") == []

    # 运行期热启用自主游戏 Agent：AgentManager.register 自动刷新 provider 工具可见名单
    game_agent = _AutonomousGameStub()
    assert manager.register(game_agent) is True
    assert MEMORY_RECALL_TOOL_NAME in [s.full_name for s in registry.list_tools(for_agent="demo_game")]
    assert MEMORY_RECALL_TOOL_NAME in [s.full_name for s in registry.list_tools(for_agent="streamer")]

    # 停用并注销自主游戏 Agent：AgentManager.unregister 自动摘除该 Agent 的可见权
    assert manager.unregister("demo_game") is True
    assert registry.list_tools(for_agent="demo_game") == []
    assert MEMORY_RECALL_TOOL_NAME in [s.full_name for s in registry.list_tools(for_agent="streamer")]


# ======================================================================
# 独立钉住规矩槽：增删约束、抗折叠固定层、工作文档导出与新场次清空
# ======================================================================


async def test_minecraft_rules_tool_limits_dedupe_remove_and_work_log_export() -> None:
    """minecraft_rules 支持增量 list/add/remove、去重、单条字数上限、总条数上限并导出到 minecraft_get_work_log。"""
    registry = ToolRegistry()
    server = FakeMaicraft()
    agent, _llm, _bus = await make_minecraft_agent(server, registry=registry)
    try:
        # 增量添加长期与本场临时规矩，并验证空白规整去重
        r1 = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_rules",
                arguments={"action": "add", "rule": "东侧公用动力车间箱子只看不拆", "durable": True},
            )
        )
        assert r1.structured_content["success"] is True and r1.structured_content["id"] == "P1"

        r1_dup = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_rules",
                arguments={"action": "add", "rule": "  东侧公用动力车间箱子只看不拆 ", "durable": True},
            )
        )
        assert r1_dup.structured_content["success"] is True and r1_dup.structured_content["count"] == 1

        r2 = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_rules",
                arguments={"action": "add", "rule": "同伴刚刚明确拒绝今晚去下界堡垒", "durable": False},
            )
        )
        assert r2.structured_content["success"] is True and r2.structured_content["id"] == "S1"

        # 单条超过 MAX_RULE_CHARS 拒绝写入
        too_long = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_rules",
                arguments={"action": "add", "rule": "规" * (MAX_RULE_CHARS + 1)},
            )
        )
        assert too_long.structured_content["success"] is False

        # minecraft_get_work_log 一并导出 pinned_rules 与 session_rules 供主播查阅
        work_log = await registry.invoke(ToolInvocation(tool_name="minecraft_get_work_log", arguments={}))
        assert work_log.structured_content["pinned_rules"] == ["东侧公用动力车间箱子只看不拆"]
        assert work_log.structured_content["session_rules"] == ["同伴刚刚明确拒绝今晚去下界堡垒"]

        # 总数上限 MAX_PINNED_RULES 守卫
        for idx in range(MAX_PINNED_RULES - 2):
            res = await registry.invoke(
                ToolInvocation(tool_name="minecraft_rules", arguments={"action": "add", "rule": f"规矩条目 {idx}"})
            )
            assert res.structured_content["success"] is True
        overflow = await registry.invoke(
            ToolInvocation(tool_name="minecraft_rules", arguments={"action": "add", "rule": "超出上限的一条"})
        )
        assert overflow.structured_content["success"] is False
        assert str(MAX_PINNED_RULES) in overflow.structured_content["error"]

        # 按短编号删除 S1 与按正文删除 P1
        rem_s = await registry.invoke(
            ToolInvocation(tool_name="minecraft_rules", arguments={"action": "remove", "id": "S1"})
        )
        assert rem_s.structured_content["success"] is True
        assert rem_s.structured_content["session_rules"] == []

        rem_p = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_rules",
                arguments={"action": "remove", "rule": "东侧公用动力车间箱子只看不拆"},
            )
        )
        assert rem_p.structured_content["success"] is True
        assert "东侧公用动力车间箱子只看不拆" not in rem_p.structured_content["pinned_rules"]
    finally:
        await agent.stop()


async def test_pinned_rules_survive_notebook_overwrite_and_background_folding_and_session_rules_clear_on_new_live(
    chars_as_tokens: None,
    db_path: Path,
) -> None:
    """钉住规矩固定层在多次后台折叠与整篇覆写工作笔记后原样不丢；新场次开播自动清空本场临时拒绝。"""
    del chars_as_tokens
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        server = FakeMaicraft()
        server.enter_world("save-fold", "联机折叠服")
        server.scene_view["summary"] = "前方：e1（minecraft:cow，6格）" + "草" * 2000

        state = {"steps": 0, "folds": 0}

        def script(messages: List[Dict[str, Any]]) -> Response:
            if _is_fold_request(messages):
                state["folds"] += 1
                start, end = _listed_ranges(messages)[0]
                return _resp(
                    calls=[
                        _call(
                            "minecraft_context_fold",
                            {"folds": [{"start": start, "end": end, "summary": "已巡查周围地形与牛群"}]},
                            f"f{state['folds']}",
                        )
                    ]
                )
            state["steps"] += 1
            if state["steps"] == 1:
                return _resp(
                    calls=[
                        _call(
                            "minecraft_rules",
                            {"action": "add", "rule": "同伴严禁动用她的附魔台和钻石箱", "durable": True},
                            "r-durable",
                        ),
                        _call(
                            "minecraft_rules",
                            {"action": "add", "rule": "同伴本场明确拒绝共享烈焰棒", "durable": False},
                            "r-temp",
                        ),
                    ]
                )
            if state["steps"] == 2:
                # 整篇覆写 2000 字工作笔记（完全不提上面的两条规矩），验证规矩槽与笔记彻底独立
                return _resp(
                    calls=[
                        _call(
                            "minecraft_notebook",
                            {"action": "write", "content": "全新覆盖后的工作笔记：只记录采矿路线"},
                            "nb-overwrite",
                        )
                    ]
                )
            if state["folds"] >= 1 and state["steps"] >= 10:
                return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "巡查完成"}, "d1")])
            if state["steps"] > 35:
                return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "兜底结束"}, "d2")])
            return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, f"obs-{state['steps']}")])

        bus = EventBus(enable_stats=False)
        agent, llm, _ = await make_minecraft_agent(
            server,
            script,
            bus=bus,
            config=_fold_config(),
            work_docs_repo=db.game_work_docs,
        )
        try:
            agent.receive_delegation(instruction="巡查营地并整理笔记", task_id="d-fold-rules")
            await _wait_until(lambda: not agent.batch_active and state["steps"] >= 10)
            assert state["folds"] >= 1

            # 折叠发生后的最后一步推理消息里，固定层仍原样携带长期与本场临时两条钉住规矩且未被编号
            action_seens = [seen for seen in llm.seen if not _is_fold_request(seen)]
            final_seen = action_seens[-1]
            pinned_rule_msgs = [
                m for m in final_seen if m.get(FACTS_KEY) and "[钉住规矩与硬拒绝]" in str(m.get("content") or "")
            ]
            assert len(pinned_rule_msgs) == 1
            content = str(pinned_rule_msgs[0]["content"])
            assert not content.startswith("[m")
            assert "同伴严禁动用她的附魔台和钻石箱" in content
            assert "同伴本场明确拒绝共享烈焰棒" in content
            assert agent.get_state_snapshot()["notebook"] == "全新覆盖后的工作笔记：只记录采矿路线"

            # 下播与新场次开播后：本场临时拒绝自动清空，长期持久规矩继续保留
            await bus.emit(CoreEvents.LIVE_ENDED, LiveEndedPayload(live_session_id=1), source="test")
            await bus.wait_until_drained()
            assert agent.get_state_snapshot()["session_rules"] == []
            assert agent.get_state_snapshot()["pinned_rules"] == ["同伴严禁动用她的附魔台和钻石箱"]

            await bus.emit(CoreEvents.LIVE_STARTED, LiveStartedPayload(live_session_id=2), source="test")
            await bus.wait_until_drained()
            assert agent.get_state_snapshot()["session_rules"] == []
            assert agent.get_state_snapshot()["pinned_rules"] == ["同伴严禁动用她的附魔台和钻石箱"]
        finally:
            await agent.stop()
            await bus.cleanup()
    finally:
        await db.close()


# ======================================================================
# 行为提示词与折叠保真规则检查
# ======================================================================


def test_prompts_and_lead_mode_cover_inventory_check_correction_social_stop_and_fold_guard() -> None:
    """提示词覆盖查包后 obtain、纠偏高优穿透、同伴社交停手、公屏简洁与放权推进、记忆回忆、钉住规矩与折叠保真。"""
    reset_prompt_manager()
    try:
        pm = get_prompt_manager()
        agent_prompt = pm.render("amaidesu_minecraft_agent")
        fold_prompt = pm.render("amaidesu_minecraft_context_fold")
    finally:
        reset_prompt_manager()

    # 工具与参数标识：模板只教注册表里存在的工具，字段名照签名
    assert "minecraft_rules" in agent_prompt
    assert "observe(what=self)" in agent_prompt and "obtain" in agent_prompt
    assert "machine_inspect" in agent_prompt
    assert (
        "仅列原版常见设施" in build_glance_spec().description and "不含联动模组机器" in build_glance_spec().description
    )
    # 已删除诱发长文堆砌的冲突条款，不再回潮
    assert "minecraft_rules" in fold_prompt

    # 工具与参数标识、退役条款不回潮由 tests/agents/test_prompt_rules.py 的清单守护
    assert "memory_recall" in LEAD_MODE_PROMPT_SECTION
    assert "minecraft_rules" in LEAD_MODE_PROMPT_SECTION
    assert "[直播间静默提醒]" in LEAD_MODE_PROMPT_SECTION
    assert "correction=True" in LEAD_MODE_PROMPT_SECTION
    assert "observe(what=self)" in LEAD_MODE_PROMPT_SECTION
    assert "rundown_control" in LEAD_MODE_PROMPT_SECTION
