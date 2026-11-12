"""D1 主播侧同源看板渲染、第 63/65 场时间线回归，以及 D3 选题与 Minecraft 玩法下沉测试。"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

from src.agents.minecraft.tools import STREAMER_GUIDE
from src.agents.streamer.game_now import AgentNow, GameNow, actual_doing_map
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.planner_render import render_delegation_backlog, render_delegations
from src.agents.streamer.room_state import RoomState
from src.modules.agents.base import BaseAgent
from src.modules.agents.control import DelegationCore, build_agent_control_provider
from src.modules.agents.delegation_board import read_boards
from src.modules.agents.manager import AgentManager
from src.modules.events.event_bus import EventBus
from src.modules.llm.payload import Response
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger


class _ReleasableGameAgent(BaseAgent):
    """支持收手协议的游戏 Agent 替身，模拟 Minecraft 执行者。"""

    name = "minecraft"
    description = "Minecraft 游戏 Agent"

    def __init__(self) -> None:
        super().__init__()
        self.received: List[tuple[str, str]] = []
        self.progress_note: str = ""

    def list_tools(self):
        return []

    def receive_delegation(self, *, instruction: str, task_id: str):
        self.received.append((task_id, instruction))
        return None

    def receive_prompt(self, *, content: str, source: str = "") -> bool:
        del content, source
        return True

    async def release_delegation(self, task_id: str, *, reason: str = "") -> str | None:
        del task_id, reason
        return self.progress_note


def _build_streamer_delegation_rig(game_now: GameNow | None = None):
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    ledger = TaskLedger(event_bus=bus)
    manager = AgentManager()
    game = _ReleasableGameAgent()
    manager.register(game)
    core = DelegationCore(manager, ledger, event_bus=bus)
    registry.register_provider(build_agent_control_provider(manager, ledger, event_bus=bus, delegation_core=core))

    recorded_actions: List[tuple[str, str]] = []
    references: List[str] = []

    async def _generate(messages: List[Dict[str, Any]], **_: Any) -> Response:
        references.append(messages[-1]["content"])
        return Response(success=True, content="不说")

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=_generate)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")

    def _status(task_id: str) -> Dict[str, Any] | None:
        rec = ledger.get(task_id)
        if rec is None:
            return None
        return {"status": rec.status, "updated_at_ms": rec.updated_at_ms, "snapshot": dict(rec.snapshot)}

    planner = Planner(
        config=PlannerConfig(planner_max_steps=2),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(
            reply_provider_factory=lambda: MagicMock(),
            action_recorder=lambda a, text: recorded_actions.append((a, text)),
            delegation_status_provider=_status,
            delegation_boards_provider=lambda: read_boards(ledger),
            game_now_provider=(lambda _agents: game_now) if game_now is not None else None,
        ),
    )
    return planner, ledger, bus, game, references, recorded_actions


async def test_session65_2158_close_planting_promotes_iron_farm_and_keeps_plot_unstarted() -> None:
    """第 65 场 21:58 时间线核验：
    栽树苗在做，刷铁机在待办1，开菜地在待办2 -> 决策定下'那一件就算收尾，专心做刷铁机'
    调 framework_delegate(item='待办1', when_busy='replace', current='done') ->
    栽树苗记 closed_by_initiator 结单，刷铁机成为唯一【在做】，嘴同步看到刷铁机，
    排在后面的开菜地渲染为'还没开工'，绝不会误当'种子都下地了'。"""
    planner, ledger, bus, _game, references, recorded = _build_streamer_delegation_rig()
    wb = planner.workbench

    await wb.handle("framework_delegate", {"agent": "minecraft", "instruction": "把树场四格栽满树苗"})
    await wb.handle("framework_delegate", {"agent": "minecraft", "instruction": "给刷铁机加压锭工序"})
    await wb.handle("framework_delegate", {"agent": "minecraft", "instruction": "在营地旁开一块菜地种小麦"})

    # 换活前核对：在做是栽树苗，待办1/待办2 都是还没开工
    await planner.plan([], proactive=True)
    before_ref = references[-1]
    assert "[在做]" in before_ref and "把树场四格栽满树苗" in before_ref
    assert "[待办1]" in before_ref and "给刷铁机加压锭工序" in before_ref and "还没开工" in before_ref
    assert "[待办2]" in before_ref and "在营地旁开一块菜地种小麦" in before_ref

    # 21:58 把待办1 刷铁机提上来做，原栽树苗按做完了结单
    obs = await wb.handle(
        "framework_delegate",
        {"agent": "minecraft", "item": "待办1", "when_busy": "replace", "current": "done"},
    )
    assert '"accepted": true' in obs
    await bus.wait_until_drained()

    await planner.plan([], proactive=True)
    after_ref = references[-1]
    work_block = after_ref.split("【我手头在游戏里做的事】\n", 1)[1].split("\n\n", 1)[0]
    backlog_block = after_ref.split("【待办】\n", 1)[1].split("\n\n", 1)[0]

    assert "给刷铁机加压锭工序" in work_block and "把树场四格栽满树苗" not in work_block
    assert "[待办2]" in backlog_block and "在营地旁开一块菜地种小麦" in backlog_block and "还没开工" in backlog_block
    assert "给刷铁机加压锭工序" not in backlog_block
    assert "deleg_" not in after_ref
    # 嘴的现场卡与决策环节读同一份：手上的目标是刷铁机，不是旧栽树苗，也不含待办菜地
    goals = wb.current_goals()
    assert [(g.instruction, g.status) for g in goals] == [("给刷铁机加压锭工序", "进行中")]
    assert ("minecraft", "给刷铁机加压锭工序") in recorded
    assert len(ledger) == 2


async def test_session63_2328_drop_dandelion_delivers_next_and_clears_mouth_stale_goal() -> None:
    """第 63 场 23:28 & 23:53 时间线核验：
    蒲公英单在做，补种树苗排在待办1 -> 按短编号'在做'撤掉作废的蒲公英单（outcome='drop'）->
    不需要内部任务号即可撤掉，补种树苗立即自动送达成为【在做】，嘴的现场卡再也不会出现蒲公英。"""
    planner, ledger, bus, game, references, _recorded = _build_streamer_delegation_rig()
    wb = planner.workbench

    await wb.handle("framework_delegate", {"agent": "minecraft", "instruction": "去采一朵蒲公英"})
    await wb.handle("framework_delegate", {"agent": "minecraft", "instruction": "回树场补种橡树苗"})

    obs = await wb.handle("framework_task_settle", {"item": "在做", "outcome": "drop"})
    assert '"settled": true' in obs
    await bus.wait_until_drained()

    assert [instr for _tid, instr in game.received] == ["去采一朵蒲公英", "回树场补种橡树苗"]
    await planner.plan([], proactive=True)
    ref = references[-1]
    assert "回树场补种橡树苗" in ref and "去采一朵蒲公英" not in ref
    assert "【待办】" not in ref
    assert [(g.instruction, g.status) for g in wb.current_goals()] == [("回树场补种橡树苗", "进行中")]
    assert len(ledger) == 1


async def test_in_hand_renders_own_words_duration_and_actual_game_doing_side_by_side() -> None:
    """D1 第 4 条机械兜底：【我手头在游戏里做的事】同时列出短编号、委派原话、已做时长和游戏那边此刻实际在做什么。"""
    now_ms_val = 100 * 60_000
    game_now = GameNow(agents={"minecraft": AgentNow(step="在刷铁机旁放置动力辊压机", step_at_ms=now_ms_val - 60_000)})
    planner, ledger, _bus, _game, _refs, _rec = _build_streamer_delegation_rig(game_now=game_now)
    await planner.workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "把树场四格栽满树苗"})
    # 把开始时刻拨到 54 分钟前模拟长拖单
    planner.workbench.delegations["minecraft"].at_ms = now_ms_val - 54 * 60_000

    doing = actual_doing_map(planner.workbench.delegations, game_now, now_ms_val)
    rendered = render_delegations(
        planner.workbench.delegations,
        lambda tid: {"status": "running", "updated_at_ms": now_ms_val - 30_000, "snapshot": {}}
        if ledger.get(tid)
        else None,
        now_ms_val,
        actual_doing=doing,
    )
    assert "[在做]" in rendered
    assert "把树场四格栽满树苗" in rendered
    assert "已做 54 分钟" in rendered
    assert "游戏那边此刻实际在做：在刷铁机旁放置动力辊压机" in rendered


async def test_backlog_rendering_distinguishes_unstarted_and_shelved_with_progress() -> None:
    """D1 第 7 条渲染口径：没送达过的写'还没开工'，搁下的写'搁下了，做到：……'，均不露内部任务号。"""
    planner, ledger, _bus, game, _refs, _rec = _build_streamer_delegation_rig()
    wb = planner.workbench
    game.progress_note = "地基铺好一半"

    await wb.handle("framework_delegate", {"agent": "minecraft", "instruction": "盖一座木屋"})
    await wb.handle("framework_delegate", {"agent": "minecraft", "instruction": "开一块菜地"})
    await wb.handle(
        "framework_delegate",
        {"agent": "minecraft", "instruction": "跟玩家甲去看新家", "when_busy": "replace", "current": "shelve"},
    )

    text = render_delegation_backlog(read_boards(ledger), 10 * 60_000)
    lines = text.splitlines()
    assert len(lines) == 2
    assert "[待办2]" in lines[0] and "盖一座木屋" in lines[0] and "搁下了，做到：地基铺好一半" in lines[0]
    assert "[待办1]" in lines[1] and "开一块菜地" in lines[1] and "还没开工" in lines[1]
    assert "deleg_" not in text
    assert render_delegation_backlog({}, 10 * 60_000) == ""


def test_delegate_rules_state_one_in_hand_settle_on_switch_and_topic_selection() -> None:
    """D1 + D3 提示词守则：一件在做其余待办、换活结单、挑下一件做什么三层先后、冷场旧事垫底、MC 玩法下沉。"""
    reset_prompt_manager()
    try:
        pm = get_prompt_manager()
        rules = pm.render("amaidesu_planner_delegate_rules")
        bare_planner = pm.render(
            "amaidesu_planner_react",
            bot_name="麦麦",
            personality="活泼",
            behavior_style="积极互动",
            boundaries_rule="",
            inside_jokes="",
            game_rules="",
            social_fact_step="",
        )
    finally:
        reset_prompt_manager()

    # D1：一件在做、其余待办、换活结单、短编号、补充不换活
    assert "一件在做，其余待办；换活必须结单" in rules
    assert "`framework_task_settle`" in rules and "`when_busy`" in rules and "`current`" in rules
    assert "要做的事变了（怎样算做完变了），就是新的一件" in rules
    assert "游戏里的战斗、受伤、逃生**不归这套**" in rules
    assert "一单一件事" in rules and '一单只能有一个"怎样算做完"' in rules
    assert "不构成把它们打包成一单的理由" in rules and "记忆本子里并列的几件旧账同样拆开派" in rules
    assert "按作废处理" not in rules

    # D3 第 1、2 条：挑下一件做什么 + 速览值得去看是现成节目 + 冷场顺序把上一场没说完的事挪到最后
    assert "挑下一件做什么" in rules
    assert "先看当面的人" in rules and "再看眼前新出现的" in rules and "最后才是惦记的旧事" in rules
    assert "【现场速览】里标了值得去看的东西是现成的节目" in rules
    assert "维护活不连着做" in rules and "约半小时还没成" in rules
    assert "【现场速览】里标了值得去看的东西是现成的节目" in bare_planner
    cold_line = [line for line in bare_planner.splitlines() if "可聊的素材不只来自游戏，只在冷场时用" in line][0]
    assert cold_line.index("她的喜好和执念") < cold_line.index("最后才是【她记得的事】里上一场没说完的事")

    # D3 第 3 条：Minecraft 专属玩法规矩下沉在 Minecraft 层 STREAMER_GUIDE，不污染通用提示词
    assert "死亡不掉落" in STREAMER_GUIDE and "死一次回重生点" in STREAMER_GUIDE
    assert "随身带一条船" in STREAMER_GUIDE
    assert "死亡不掉落" not in bare_planner and "随身带一条船" not in bare_planner
