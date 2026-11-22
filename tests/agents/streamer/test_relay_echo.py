"""主播递话不回声：没有新输入的主动窗不给游戏活补充要求，有观众原话时随补充逐字附上。

实测四十分钟递话三十次，大多是流程单推进窗把游戏侧自己汇报过的进展换个说法递回去，
其中一条过时的"拉杆没生效"让游戏侧把已经拉下的拉杆又拨了回去。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Set
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.state import MinecraftInstruction
from src.agents.streamer.delegation_workbench import DelegationWorkbench
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import GameEventBatch
from src.agents.streamer.trigger_cause import TriggerCause, TriggerKind
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.tools.registry import ToolRegistry

from tests.agents.minecraft_v1_fakes import assemble_minecraft_agent


def _planner(
    responses: List[Response],
    invoked: List[ToolInvocation],
    status_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Planner:
    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        invoked.append(invocation)
        if invocation.tool_name == "framework_delegate":
            data: Dict[str, Any] = {"accepted": True, "task_id": "deleg_1", "executor": "minecraft"}
        else:
            data = {"delivered": True, "executor": "minecraft"}
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=data)

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=_invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=responses)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    return Planner(
        config=PlannerConfig(planner_max_steps=2),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(reply_provider_factory=lambda: MagicMock(), delegation_status_provider=status_provider),
    )


def _relay(content: str) -> Response:
    return Response(
        success=True,
        tool_calls=[ToolCall(id="c1", name="framework_prompt", arguments={"agent": "minecraft", "content": content})],
    )


def _msg(text: str) -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type="danmaku", user=RoomMessageUser(id="debug_v", name="调试观众"), content=text, message_id="m1"
    )


async def test_rundown_window_without_new_input_allows_distinct_relays_and_dedups_verbatim() -> None:
    """拆闸后：无新输入的主动窗允许给游戏补充不同要求（不再硬拒诱发 replace 强杀），
    但极短时间内完全相同的同一句重复递话仍会被轻量去重拦下。"""
    invoked: List[ToolInvocation] = []
    planner = _planner(
        [
            _relay("先核对一下漏斗里还有没有元件"),
            Response(success=True, content="不说"),
            _relay("先核对一下漏斗里还有没有元件"),
            Response(success=True, content="不说"),
            _relay("如果漏斗空了再去终端确认"),
            Response(success=True, content="不说"),
        ],
        invoked,
    )
    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.RUNDOWN))
    assert [call.tool_name for call in invoked] == ["framework_prompt"]

    # 短时间内完全相同的一句被轻量去重拦下
    invoked.clear()
    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.RUNDOWN))
    assert invoked == []

    # 换了新内容的补充要求正常放行
    invoked.clear()
    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.RUNDOWN))
    assert [call.tool_name for call in invoked] == ["framework_prompt"]


async def test_game_request_and_reminder_windows_still_relay() -> None:
    """游戏侧请求定夺、运营提醒到达的主动窗仍可补充要求。"""
    invoked: List[ToolInvocation] = []
    planner = _planner([_relay("换个办法：从 AE 取铁板"), Response(success=True, content="不说")], invoked)
    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.GAME))
    assert [call.tool_name for call in invoked] == ["framework_prompt"]

    invoked.clear()
    planner = _planner([_relay("运营说先停一下"), Response(success=True, content="不说")], invoked)
    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.RUNDOWN), reminders="先停一下")
    assert [call.tool_name for call in invoked] == ["framework_prompt"]


async def test_rundown_window_relays_when_game_waits_for_my_decision() -> None:
    """她在游戏里卡住、停着等我拿主意时，补充要求即答复；答复送达后 awaits_decision 清零，且拆闸后新补充仍可放行。"""
    invoked: List[ToolInvocation] = []
    planner = _planner(
        [
            _relay("墓碑那边收手，用魂符放一只新女仆"),
            Response(success=True, content="不说"),
            _relay("再补一句：放完顺手看下背包"),
            Response(success=True, content="不说"),
        ],
        invoked,
    )
    await planner.workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "把女仆救回来"})
    planner.workbench.note_game_report("minecraft", "escalation")
    assert planner.workbench.awaits_decision("minecraft") is True
    invoked.clear()

    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.RUNDOWN))
    assert [call.tool_name for call in invoked] == ["framework_prompt"]
    assert planner.workbench.awaits_decision("minecraft") is False

    invoked.clear()
    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.RUNDOWN))
    assert [call.tool_name for call in invoked] == ["framework_prompt"]


async def test_task_ledger_decides_whether_game_waits() -> None:
    """装了任务账本时 awaits_decision 以账面状态为准：等定夺为 True，答复一次后转为 False，回到在跑亦为 False。"""
    statuses = {"deleg_1": {"status": "waiting_for_decision"}}
    invoked: List[ToolInvocation] = []
    planner = _planner(
        [
            _relay("换个办法"),
            Response(success=True, content="不说"),
        ],
        invoked,
        status_provider=statuses.get,
    )
    await planner.workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "把女仆救回来"})
    assert planner.workbench.awaits_decision("minecraft") is True
    invoked.clear()
    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.COLD))
    assert [call.tool_name for call in invoked] == ["framework_prompt"]
    assert planner.workbench.awaits_decision("minecraft") is False

    statuses["deleg_1"] = {"status": "running"}
    assert planner.workbench.awaits_decision("minecraft") is False


async def test_answer_is_remembered_after_other_relays_push_it_out() -> None:
    """ "答复过没有"按执行者单独记：给别的游戏补过几次话、把这条挤出最近几条记录，也不会忘成"还没答复"。"""

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        agent = invocation.arguments["agent"]
        if invocation.tool_name == "framework_delegate":
            data: Dict[str, Any] = {"accepted": True, "task_id": f"deleg_{agent}", "executor": agent}
        else:
            data = {"delivered": True, "executor": agent}
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=data)

    registry = MagicMock()
    registry.invoke = AsyncMock(side_effect=_invoke)
    workbench = DelegationWorkbench(registry, intents_max=4, relays_max=1)

    # 她卡住求助 -> 我答复一次 -> 又给另一个游戏补了一句（最近记录只留一条，答复被挤掉）
    await workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "把女仆救回来"})
    workbench.note_game_report("minecraft", "escalation")
    assert workbench.awaits_decision("minecraft") is True
    await workbench.handle("framework_prompt", {"agent": "minecraft", "content": "收手，用魂符放新女仆"})
    await workbench.handle("framework_prompt", {"agent": "other_game", "content": "先歇一会"})
    assert [relay.agent for relay in workbench.recent_relays] == ["other_game"]

    assert workbench.awaits_decision("minecraft") is False, "答复过的求助不能因为记录被挤掉又算成没答复"


# 账面还在做的三种状态，与等主意闸门对没有新输入的补充要求是否放行：只有等定夺且没答复过才放行
_IN_FLIGHT_GATES = [("accepted", False), ("running", False), ("waiting_for_decision", True)]


def _ledger_workbench(statuses: Dict[str, Dict[str, Any]], invoked: List[ToolInvocation]) -> DelegationWorkbench:
    """装了任务账本（``statuses`` 按任务号给账面）的工作台，记下真正递到游戏侧的调用。"""

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        invoked.append(invocation)
        agent = invocation.arguments["agent"]
        if invocation.tool_name == "framework_delegate":
            data: Dict[str, Any] = {"accepted": True, "task_id": f"deleg_{agent}", "executor": agent}
        else:
            data = {"delivered": True, "executor": agent}
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=data)

    registry = MagicMock()
    registry.invoke = AsyncMock(side_effect=_invoke)
    return DelegationWorkbench(registry, intents_max=4, relays_max=4, delegation_status_provider=statuses.get)


async def _relay_without_input(workbench: DelegationWorkbench, invoked: List[ToolInvocation]) -> List[str]:
    """没有新输入的窗里给 minecraft 补一句，返回真正递到游戏侧的调用名。"""
    invoked.clear()
    await workbench.handle(
        "framework_prompt", {"agent": "minecraft", "content": "收手，用魂符放新女仆"}, relay_without_input=True
    )
    return [call.tool_name for call in invoked]


@pytest.mark.parametrize(("status", "opens"), _IN_FLIGHT_GATES)
async def test_delegation_kept_at_live_start_keeps_its_decision_gate(status: str, opens: bool) -> None:
    """开播清场留下的委派（账面还在做），awaits_decision 状态与清场前一致，且拆闸后补充要求照常放行。"""
    statuses = {"deleg_minecraft": {"status": status, "updated_at_ms": 1}}
    invoked: List[ToolInvocation] = []
    workbench = _ledger_workbench(statuses, invoked)
    await workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "把女仆救回来"})

    workbench.reset()

    assert "minecraft" in workbench.delegations
    assert workbench.awaits_decision("minecraft") is opens
    assert await _relay_without_input(workbench, invoked) == ["framework_prompt"]


@pytest.mark.parametrize(("status", "opens"), _IN_FLIGHT_GATES)
async def test_delegation_kept_at_save_switch_keeps_its_decision_gate(status: str, opens: bool) -> None:
    """换存档时留下的委派（账面还在做），awaits_decision 状态与换存档前一致，且拆闸后补充要求照常放行。"""
    statuses = {"deleg_minecraft": {"status": status, "updated_at_ms": 1}}
    invoked: List[ToolInvocation] = []
    workbench = _ledger_workbench(statuses, invoked)
    await workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "把女仆救回来"})

    workbench.forget_game("minecraft")

    assert "minecraft" in workbench.delegations
    assert workbench.awaits_decision("minecraft") is opens
    assert await _relay_without_input(workbench, invoked) == ["framework_prompt"]


def _speaking_planner(
    responses: List[Response],
    refuse: Set[str],
    status_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> tuple[Planner, List[ToolInvocation], MagicMock]:
    """会出声的 Planner：refuse 里的工具名受理失败（集合可在测试中途改），说话工具成功出声。"""
    invoked: List[ToolInvocation] = []

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        invoked.append(invocation)
        name = invocation.tool_name
        if name in refuse:
            return ToolExecutionResult(tool_name=name, success=False, error_message=f"受理失败：{name} 送不到")
        if name == "framework_delegate":
            data: Dict[str, Any] = {"accepted": True, "task_id": "deleg_1", "executor": "minecraft"}
        elif name == "framework_prompt":
            data = {"delivered": True, "executor": "minecraft"}
        else:
            data = {"speech": "说了", "emotion": {"name": "happy"}, "metadata": {}}
        return ToolExecutionResult(tool_name=name, success=True, structured_content=data)

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=_invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=list(responses))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config=PlannerConfig(planner_max_steps=4),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        env=PlannerEnv(reply_provider_factory=lambda: MagicMock(), delegation_status_provider=status_provider),
    )
    return planner, invoked, llm


def _say(call_id: str, topic: str) -> ToolCall:
    return ToolCall(id=call_id, name="streamer_reply", arguments={"topic_summary": topic, "reply_guidance": "嘴硬"})


def _delegate(call_id: str) -> ToolCall:
    return ToolCall(id=call_id, name="framework_delegate", arguments={"agent": "minecraft", "instruction": "放新女仆"})


def _tool_reply(llm: MagicMock, call_id: str) -> Dict[str, Any]:
    """最后一次请求里某个调用的工具回执（JSON 解析后）。"""
    [message] = [m for m in llm.generate.call_args_list[-1].args[0] if m.get("tool_call_id") == call_id]
    return json.loads(message["content"])


async def test_reply_is_held_when_same_step_arrangement_is_refused() -> None:
    """同一步里给游戏的安排被拒：这句不出口，原话连同拒绝理由退回；下一步改了说法照常说出去。

    实测委派被拒（手上有一件没做完）后照样说出"那我自己招一只……来"，游戏那边什么也没收到。
    """
    planner, invoked, llm = _speaking_planner(
        [
            Response(success=True, tool_calls=[_say("r1", "那我自己招一只"), _delegate("d1")]),
            Response(success=True, tool_calls=[_say("r2", "墓碑这事先放一放")]),
        ],
        refuse={"framework_delegate"},
    )

    outcome = await planner.plan([_msg("救不了就换一只吧")])

    assert [c.arguments["topic_summary"] for c in invoked if c.tool_name == "streamer_reply"] == ["墓碑这事先放一放"]
    held = _tool_reply(llm, "r1")
    assert "这句先不说" in held["error"]
    assert held["held_reply"] == {"topic_summary": "那我自己招一只", "reply_guidance": "嘴硬"}
    assert "framework_delegate 送不到" in held["refusal"]
    assert outcome.replied is True


async def test_reply_is_held_at_most_once_per_window() -> None:
    """一窗只压一次：同一窗里安排再被拒，这句照常说出去，不让整窗哑掉。"""
    planner, invoked, _llm = _speaking_planner(
        [
            Response(success=True, tool_calls=[_say("r1", "我自己招一只"), _delegate("d1")]),
            Response(success=True, tool_calls=[_say("r2", "那换个说法"), _delegate("d2")]),
        ],
        refuse={"framework_delegate"},
    )

    outcome = await planner.plan([_msg("换一只吧")])

    assert [c.arguments["topic_summary"] for c in invoked if c.tool_name == "streamer_reply"] == ["那换个说法"]
    assert outcome.replied is True


async def test_window_that_stays_quiet_after_hold_records_reply_held() -> None:
    """压住一句后这一窗没再开口：静默原因记 reply_held，复盘看得出是被压住的。"""
    planner, invoked, _llm = _speaking_planner(
        [
            Response(success=True, tool_calls=[_say("r1", "我自己招一只"), _delegate("d1")]),
            Response(success=True, content="不说了"),
        ],
        refuse={"framework_delegate"},
    )

    outcome = await planner.plan([_msg("换一只吧")])

    assert [c.tool_name for c in invoked] == ["framework_delegate"]
    assert (outcome.replied, outcome.silent_reason) == (False, "reply_held")


async def test_stuck_game_and_refused_delegate_no_longer_deadlock() -> None:
    """复现 54 场 23:35 的死结：游戏卡住等主意 → 流程单推进窗里派新事被拒、那句话被压住 →
    补充要求不再被空窗闸门拦、递达 → 再开口说出去。修复前补充被拦、话照样说，游戏一直停着。"""
    statuses = {"deleg_1": {"status": "running"}}
    refuse: Set[str] = set()
    planner, invoked, _llm = _speaking_planner(
        [
            Response(success=True, tool_calls=[_say("r1", "救不了那只，那我自己招一只"), _delegate("d1")]),
            _relay("墓碑那边收手，用背包里的魂符放一只新女仆"),
            Response(success=True, tool_calls=[_say("r2", "魂符攥手里了，招一只新的")]),
        ],
        refuse,
        status_provider=statuses.get,
    )
    await planner.workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "把女仆救回来"})
    planner.workbench.note_game_report("minecraft", "escalation")
    statuses["deleg_1"] = {"status": "waiting_for_decision"}
    refuse.add("framework_delegate")  # 手上那件没做完：新的派不进去
    invoked.clear()

    outcome = await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.RUNDOWN))

    assert [c.tool_name for c in invoked] == ["framework_delegate", "framework_prompt", "streamer_reply"]
    assert invoked[-1].arguments["topic_summary"] == "魂符攥手里了，招一只新的"
    assert outcome.replied is True


async def test_cold_window_relays_with_or_without_fresh_game_chat_and_attaches_chat_source() -> None:
    """拆闸后冷场窗允许补充要求；批次里带来本回合新入账的玩家原话时，来源对话逐字附带进递话。"""
    invoked: List[ToolInvocation] = []
    planner = _planner([_relay("先核对下炉子状态"), Response(success=True, content="不说")], invoked)
    await planner.plan([], proactive=True, trigger_cause=TriggerCause(kind=TriggerKind.COLD))
    assert [call.tool_name for call in invoked] == ["framework_prompt"]

    invoked.clear()
    planner = _planner([_relay("回同伴一句"), Response(success=True, content="不说")], invoked)
    await planner.plan(
        [],
        proactive=True,
        trigger_cause=TriggerCause(kind=TriggerKind.COLD),
        game_batch=GameEventBatch(
            events=("[20:01 minecraft] 玩家 Steve：你在干嘛",),
            fresh_chat=("[20:01 minecraft] 玩家 Steve：你在干嘛",),
        ),
    )
    assert [call.tool_name for call in invoked] == ["framework_prompt"]
    assert "玩家 Steve：你在干嘛" in invoked[0].arguments["content"]


async def test_relay_with_viewer_words_carries_them_verbatim() -> None:
    """弹幕窗的补充要求附上观众逐字原话，游戏侧不只看到一层转述。"""
    invoked: List[ToolInvocation] = []
    planner = _planner([_relay("把铁板放到置物台上"), Response(success=True, content="不说")], invoked)
    await planner.plan([_msg("置物台上面有注液器，铁板放上去就出蜂蜜胶")])
    content = invoked[0].arguments["content"]
    assert content.startswith("[补充要求]\n把铁板放到置物台上")
    assert "置物台上面有注液器，铁板放上去就出蜂蜜胶" in content and "[来源对话" in content


def test_game_agent_labels_relay_sources() -> None:
    """运营原话与主播补充分开标注，冲突时游戏侧知道该听谁。"""
    agent = assemble_minecraft_agent(registry=ToolRegistry())
    agent.receive_prompt(content="拉下停机开关", source="operator")
    agent.receive_prompt(content="停机开关别碰", source="planner-react")
    agent.receive_prompt(content="继续", source="test")
    queued: List[Any] = list(agent._message_queue)
    assert all(isinstance(item, MinecraftInstruction) for item in queued)
    assert [item[1] for item in queued] == ["[运营原话]\n拉下停机开关", "[主播补充]\n停机开关别碰", "继续"]


def test_delegate_rules_point_with_features_not_screen_positions() -> None:
    """委派守则：她看的直播画面是几秒前的，游戏里的视角可能已经转开，指东西写特征和名字，不写画面方位。"""
    reset_prompt_manager()
    try:
        rules = get_prompt_manager().render("amaidesu_planner_delegate_rules")
    finally:
        reset_prompt_manager()
    assert "画面左边那个" in rules and "是谁的" in rules and "地点名" in rules


def test_prompts_state_relay_and_completion_rules() -> None:
    """提示词写明：补充只写新要求、完成要收进背包、原话优先于转述、一次一个身体动作。

    补充要求与完成口径属于委派型游戏守则，经 game_rules 拼进主播提示词。
    """
    reset_prompt_manager()
    try:
        rules = get_prompt_manager().render("amaidesu_planner_delegate_rules")
        planner = get_prompt_manager().render(
            "amaidesu_planner_react",
            bot_name="麦麦",
            personality="活泼",
            behavior_style="积极互动",
            boundaries_rule="",
            inside_jokes="",
            game_rules=rules,
            social_fact_step="",
        )
        game = get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()
    assert "补充只写**新的要求**" in planner and "没有新输入的流程单推进、冷场、定时窗口不补充" in planner
    assert "怎样算做完" in planner and "观众没说数量就按一个算" in planner
    assert "要的东西要收进背包才算到手" in game and "[运营原话]" in game and "[主播补充]" in game
    assert "身体同一时间只做一件事" in game
    assert "error.fields" in game
    # 卡住等我拿主意时空窗也补；游戏里回话用 minecraft_chat_send 直发、像玩家打字那样短，游戏侧照发不加工
    assert "停着等我拿主意" in planner and "minecraft_chat_send" in planner
    # 游戏侧不再代打主播的游戏公屏话：她自己直连发送，游戏 Agent 只管行动与自己想说的话
    assert "她自己直连发送" in game and "不把要问的事列成清单或编号" in game


async def test_duplicate_relay_dedup_scopes_by_kind() -> None:
    """15 秒同文去重纳入 kind 通道：同通道同文拦截，换通道（requirement vs chat）重发同文放行。"""
    invoked: List[ToolInvocation] = []
    workbench = _ledger_workbench({}, invoked)
    await workbench.handle("framework_delegate", {"agent": "minecraft", "instruction": "看看熔炉"})
    invoked.clear()

    # 第 1 次 requirement 递话：放行
    first = await workbench.handle(
        "framework_prompt",
        {"agent": "minecraft", "kind": "requirement", "content": "先停一下"},
        relay_without_input=True,
    )
    assert json.loads(first)["delivered"] is True

    # 15 秒内同通道同文重复：温和拦下
    dup = await workbench.handle(
        "framework_prompt",
        {"agent": "minecraft", "kind": "requirement", "content": "先停一下"},
        relay_without_input=True,
    )
    assert json.loads(dup)["ok"] is False

    # 换用 chat 通道发相同文案：不被误拒
    other_kind = await workbench.handle(
        "framework_prompt",
        {"agent": "minecraft", "kind": "chat", "content": "先停一下"},
        relay_without_input=True,
    )
    assert json.loads(other_kind)["delivered"] is True
    assert len(invoked) == 2
