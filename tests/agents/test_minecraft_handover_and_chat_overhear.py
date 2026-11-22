"""MinecraftAgent 双脑协同测试：when_busy="replace" 保留 [交接摘要]、普通结单干净开局、任务中旁听游戏公屏与短窗去重。"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import MagicMock

from src.agents.minecraft.maicraft import FINISHED, GoalRun
from src.agents.minecraft.state import TodoItem
from src.modules.agents.control import DelegationCore
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatMention, GameChatPayload, GameSelfChattedPayload
from src.modules.tools.tasks import TaskLedger
from tests.agents.minecraft_v1_fakes import FakeMaicraft, _wait_until, make_minecraft_agent


def _seed_in_flight_work(agent: Any, task_id: str) -> None:
    """在 Agent 中写入一条进行中的委派进度与操作记忆，供检验收手后的交接行为。"""
    agent._delegations.absorb(task_id)
    agent._task_finished = False
    agent._task_instructions.append("把 4k 存储元件装进 ME 驱动器")
    agent._mc_state.todos = [TodoItem(content="放漏斗指向驱动器", status="done")]
    agent._goal_ledger.remember_finished(
        GoalRun(
            goal_id=4050,
            ability="drop",
            purpose="把 4k 元件扔进漏斗",
            state=FINISHED,
            question=None,
            result={"status": "cancelled", "summary": "已扔出 1 件 ae2:item_storage_cell_4k"},
            doing="",
            data={},
        )
    )
    agent._messages.append({"role": "assistant", "content": "刚放好指向驱动器的漏斗并把 4K 元件扔进去了"})


async def test_replace_release_preserves_handover_summary_while_settle_starts_clean() -> None:
    """when_busy="replace" 换活时新任务开头带入紧凑 [交接摘要]；普通结单/撤单后开新任务保持干净开局。"""
    bus = EventBus()
    agent, _llm, _bus = await make_minecraft_agent(FakeMaicraft(), bus=bus, live=True)
    try:
        await agent.pause()
        ledger = TaskLedger()
        manager = MagicMock()
        manager.get_agent_by_name = MagicMock(return_value=agent)
        core = DelegationCore(manager, ledger, event_bus=bus)

        # 1. 经框架 DelegationCore 走 when_busy="replace" 换活（handover=True）→ 保留紧凑 [交接摘要]
        first = await core.accept(agent, "把 4k 存储元件装进 ME 驱动器", "planner-react")
        assert first.ok is True and first.task_id
        # 清掉入队未消费项并模拟已吸收进入执行
        agent._wake_queue.drop_task(first.task_id)
        _seed_in_flight_work(agent, first.task_id)

        replaced = await core.accept(
            agent,
            "顺手核对 ME 驱动器槽位",
            "planner-react",
            when_busy="replace",
            current="drop",
        )
        assert replaced.ok is True
        assert agent._task_finished is True

        new_messages: List[dict[str, Any]] = []
        agent._start_new_task(new_messages, {"role": "system", "content": "SYS"})
        handover_msgs = [m for m in new_messages if "[交接摘要]" in str(m.get("content", ""))]
        assert len(handover_msgs) == 1
        handover_text = handover_msgs[0]["content"]
        assert "把 4k 存储元件装进 ME 驱动器" in handover_text
        assert "放漏斗指向驱动器" in handover_text
        assert "已扔出 1 件 ae2:item_storage_cell_4k" in handover_text
        assert "刚放好指向驱动器的漏斗" in handover_text

        # 2. 经框架 DelegationCore 走 framework_task_settle(drop) 结单（handover=False）→ 清空交接摘要，下次全新任务干净
        agent._wake_queue.drop_task(replaced.task_id)
        _seed_in_flight_work(agent, replaced.task_id)
        outcome = await core.settle(
            target_name="minecraft",
            item_ref="在做",
            outcome="drop",
            caller="planner-react",
            progress="不做了",
        )
        assert outcome.ok is True

        clean_messages: List[dict[str, Any]] = []
        agent._start_new_task(clean_messages, {"role": "system", "content": "SYS"})
        assert all("[交接摘要]" not in str(m.get("content", "")) for m in clean_messages)
    finally:
        await agent.stop()


async def test_game_chat_and_streamer_self_chat_overheard_only_during_active_task() -> None:
    """空闲时收到玩家游戏公屏或主播己方游戏公屏零消耗忽略；执行活跃任务时注入 [游戏公屏] 并按需唤醒，排除己方 maicraft:chat 重复。"""
    bus = EventBus()
    agent, llm, _bus = await make_minecraft_agent(bus=bus, live=True)
    try:
        # 1. 空闲态（_task_finished=True）：玩家游戏公屏与主播替发游戏公屏都不入队、不唤醒、不调模型
        agent._wake_event.clear()
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(game="minecraft", kind="player", sender="同伴甲", content="过来"),
            source="test",
        )
        await bus.emit(
            CoreEvents.GAME_SELF_CHATTED,
            GameSelfChattedPayload(game="minecraft", message="炉子先不管了"),
            source="test",
        )
        await bus.wait_until_drained()
        assert len(agent._message_queue) == 0
        assert not agent._wake_event.is_set()
        assert len(llm.seen) == 0

        # 2. 活跃任务态：检验精确入队与去重，再恢复验证模型上下文
        agent._task_finished = False
        agent._task_suspended = False
        await agent.pause()
        agent._task_suspended = True
        agent._wake_event.clear()
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴甲",
                content="麦麦过来，别管炉子了",
                mentions=[GameChatMention(name="麦麦", type="self", id="")],
            ),
            source="test",
        )
        await bus.emit(
            CoreEvents.GAME_SELF_CHATTED,
            GameSelfChattedPayload(game="minecraft", message="好啦炉子先不管了"),
            source="test",
        )
        # 游戏 Agent 自己通过 maicraft:chat 发出的游戏公屏被去重排除，不重复注入上下文
        await agent._send_self_chat("我看到牌子了", from_streamer=False)
        await bus.wait_until_drained()

        queued_texts = [item[1] for item in agent._message_queue]
        assert queued_texts == [
            "[游戏公屏] 玩家 同伴甲：麦麦过来，别管炉子了",
            "[游戏公屏·主播替你说] 好啦炉子先不管了",
        ]

        # 3. 活跃等待中收到喊停/点名游戏公屏与主播替发游戏公屏会立即唤醒 worker 并将 [游戏公屏] 送进 LLM
        agent._task_suspended = False
        await agent.resume()
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴乙",
                content="先停一下，过来看看",
            ),
            source="test",
        )
        await bus.wait_until_drained()

        await _wait_until(lambda: len(llm.seen) >= 1)
        joined = "\n".join(str(m.get("content") or "") for m in llm.seen[0])
        assert "[游戏公屏] 玩家 同伴甲：麦麦过来，别管炉子了" in joined
        assert "[游戏公屏·主播替你说] 好啦炉子先不管了" in joined
        assert "[游戏公屏] 玩家 同伴乙：先停一下，过来看看" in joined
        assert "我看到牌子了" not in joined
    finally:
        await agent.stop()


async def test_overhear_chat_dedup_expires_after_window_and_resets_on_new_task() -> None:
    """游戏公屏去重仅在 5 秒短窗内生效且换新任务即清空，同伴稍后或下一任务再喊“过来”可正常入队并唤醒循环。"""
    now_s = [100.0]
    bus = EventBus()
    agent, llm, _bus = await make_minecraft_agent(FakeMaicraft(), bus=bus, live=True, clock=lambda: now_s[0])
    try:
        agent._task_finished = False
        agent._task_suspended = False

        payload = GameChatPayload(game="minecraft", kind="player", sender="同伴甲", content="过来")
        await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, payload, source="test")
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) == 1)
        assert any("[游戏公屏] 玩家 同伴甲：过来" in str(m.get("content") or "") for m in llm.seen[0])

        # 1 秒后（5 秒短窗内）重复同文：去重跳过，不入队也不再次唤醒
        now_s[0] += 1.0
        agent._task_finished = False
        await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, payload, source="test")
        await bus.wait_until_drained()
        assert len(agent._message_queue) == 0
        assert len(llm.seen) == 1

        # 超过 5 秒短窗后再次喊“过来”：正常入队并再次唤醒任务循环
        now_s[0] += 5.0
        await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, payload, source="test")
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) == 2)

        # 切到新任务后即使不足 5 秒，去重表已清空，再喊“过来”立即入队并唤醒
        agent._start_new_task([], {"role": "system", "content": "SYS"})
        agent._task_finished = False
        now_s[0] += 0.5
        await bus.emit(CoreEvents.GAME_CHAT_RECEIVED, payload, source="test")
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) == 3)
    finally:
        await agent.stop()
