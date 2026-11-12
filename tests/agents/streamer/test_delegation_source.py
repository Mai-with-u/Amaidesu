"""游戏委派保留具体目标原句（含观众弹幕与游戏公屏原话），不把来源引用误当成空指令的行动授权。"""

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import GameEventBatch, PlayerNote
from src.modules.llm.payload import Response, ToolCall
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.utils.time_utils import now_ms


async def test_delegation_keeps_source_without_rewriting_model_history() -> None:
    """即使规划摘要泛化了目标，接收方仍看到原词和禁用范围；原工具请求对象不被原地修改。

    做什么以主播的决定为准：原话只补物品名写法与禁用条件，做法建议须经目标采纳，
    避免"别死磕这台机器"和"以原话为准的放块铁板试试"同时摆在游戏侧面前。
    """
    registry = MagicMock()
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="framework_delegate", success=True, structured_content={"accepted": True}
        )
    )
    planner = Planner(PlannerConfig(), MagicMock(), MagicMock(), RoomState(), tool_registry=registry)
    original = "在平台上搭建精密构建生产线，不许用Mek"
    source = [{"role": "user", "content": original}]
    arguments = {"agent": "minecraft", "instruction": "搭一条工业风产线"}
    result = await planner.workbench.handle("framework_delegate", arguments, source_dialogue=source)
    delivered = registry.invoke.call_args.args[0].arguments["instruction"]
    assert "[本次目标]" in delivered  # 目标抬头标记是双方契约，措辞可调、存在性不可失
    assert delivered.index("搭一条工业风产线") > delivered.index("[本次目标]")  # 目标原文在抬头之后逐字呈现
    assert original in delivered and "不构成额外任务" in delivered  # 来源原话逐字附带且不构成行动授权
    assert "以对应来源原话为准" not in delivered  # 旧措辞"以原话为准"退役后不得回流
    assert arguments["instruction"] == "搭一条工业风产线"
    assert json.loads(result)["accepted"] is True


async def test_source_does_not_create_an_instruction_or_change_other_tools() -> None:
    """没有目标时仍交给原有校验拒绝；普通查询不附带观众对话。"""
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=ToolExecutionResult(tool_name="test", success=True, structured_content={}))
    planner = Planner(PlannerConfig(), MagicMock(), MagicMock(), RoomState(), tool_registry=registry)
    for tool, arguments in [("framework_delegate", {"instruction": ""}), ("clock_time", {})]:
        await planner._invoke_registry_tool(
            tool, arguments, source_dialogue=[{"role": "user", "content": "另一个请求"}]
        )
        assert registry.invoke.call_args.args[0].arguments == arguments


async def test_delegation_attaches_recent_and_current_game_public_chat_waypoints_verbatim() -> None:
    """D1 第 8 条（第 61 场 19:17 回归）：玩家在游戏公屏发了路点/坐标（无论上一窗留下的还是本窗新到的），
    即使本窗没有直播间弹幕，Planner 派出的委派与补充要求也自动把那几句游戏公屏原文附在[来源对话]里送给游戏侧；
    公屏回话走直发工具 minecraft_chat_send，原样发送不被来源对话污染。"""
    invoked: List[tuple[str, Dict[str, Any]]] = []

    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        invoked.append((invocation.tool_name, dict(invocation.arguments)))
        if invocation.tool_name == "framework_delegate":
            return ToolExecutionResult(
                tool_name="framework_delegate",
                success=True,
                structured_content={"accepted": True, "task_id": "deleg_1", "executor": "minecraft"},
            )
        if invocation.tool_name == "minecraft_chat_send":
            return ToolExecutionResult(
                tool_name="minecraft_chat_send",
                success=True,
                structured_content={"success": True, "message": "已发送"},
            )
        return ToolExecutionResult(
            tool_name="framework_prompt",
            success=True,
            structured_content={"delivered": True, "executor": "minecraft"},
        )

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=_invoke)

    llm = MagicMock()
    llm.generate = AsyncMock(
        side_effect=[
            # 第一窗：玩家甲在公屏发了路点，这窗先没派活
            Response(success=True, content="先听他说完"),
            # 第二窗：玩家甲催促，决策调 framework_delegate 过去看，并用 minecraft_chat_send 在公屏直回一句
            Response(
                success=True,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="framework_delegate",
                        arguments={"agent": "minecraft", "instruction": "按玩家甲发的路点去海边新家看看"},
                    ),
                    ToolCall(
                        id="c2",
                        name="minecraft_chat_send",
                        arguments={"text": "看到坐标啦，马上游过去"},
                    ),
                ],
            ),
            Response(success=True, content="收尾"),
            # 第三窗：玩家甲补充靠码头停船，决策调 framework_prompt 补要求
            Response(
                success=True,
                tool_calls=[
                    ToolCall(
                        id="c3",
                        name="framework_prompt",
                        arguments={
                            "agent": "minecraft",
                            "kind": "requirement",
                            "content": "到了海边新家靠北侧木码头下船",
                        },
                    ),
                ],
            ),
            Response(success=True, content="收尾"),
        ]
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        PlannerConfig(planner_max_steps=3),
        llm,
        prompt,
        RoomState(),
        tool_registry=registry,
        env=PlannerEnv(reply_provider_factory=lambda: MagicMock()),
    )

    waypoint_line = (
        "[19:17 minecraft 公屏] 玩家 玩家甲："
        "[xaero-waypoint:海边新家:新:248:64:-512:3:false:0:Internal-overworld-waypoints]"
    )
    ts = now_ms()
    await planner.plan(
        [],
        proactive=True,
        game_batch=GameEventBatch(
            fresh_chat=(waypoint_line,),
            player_notes=(PlayerNote(who="玩家甲", said=True, line=waypoint_line, timestamp_ms=ts),),
        ),
    )
    follow_line = "[19:18 minecraft 公屏] 玩家 玩家甲：按刚才那个路点过来就行"
    await planner.plan(
        [],
        proactive=True,
        game_batch=GameEventBatch(
            fresh_chat=(follow_line,),
            player_notes=(PlayerNote(who="玩家甲", said=True, line=follow_line, timestamp_ms=ts + 5_000),),
        ),
    )
    dock_line = "[19:19 minecraft 公屏] 玩家 玩家甲：北侧有个木码头"
    await planner.plan(
        [],
        proactive=True,
        game_batch=GameEventBatch(
            fresh_chat=(dock_line,),
            player_notes=(PlayerNote(who="玩家甲", said=True, line=dock_line, timestamp_ms=ts + 10_000),),
        ),
    )

    assert [name for name, _ in invoked] == ["framework_delegate", "minecraft_chat_send", "framework_prompt"]
    delegated_text = invoked[0][1]["instruction"]
    assert "xaero-waypoint:海边新家:新:248:64:-512" in delegated_text
    assert "按刚才那个路点过来就行" in delegated_text
    assert "路点、坐标" in delegated_text

    assert invoked[1][1] == {"text": "看到坐标啦，马上游过去"}

    prompt_text = invoked[2][1]["content"]
    assert prompt_text.startswith("[补充要求]\n")
    assert "xaero-waypoint:海边新家:新:248:64:-512" in prompt_text
    assert "北侧有个木码头" in prompt_text
