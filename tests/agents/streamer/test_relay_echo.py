"""主播递话不回声：没有新输入的主动窗不给游戏活补充要求，有观众原话时随补充逐字附上。

实测四十分钟递话三十次，大多是流程单推进窗把游戏侧自己汇报过的进展换个说法递回去，
其中一条过时的"拉杆没生效"让游戏侧把已经拉下的拉杆又拨了回去。
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.agents.minecraft.state import MinecraftInstruction
from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.tools.registry import ToolRegistry


def _planner(responses: List[Response], invoked: List[ToolInvocation]) -> Planner:
    async def _invoke(invocation: ToolInvocation) -> ToolExecutionResult:
        invoked.append(invocation)
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            structured_content={"delivered": True, "executor": "minecraft"},
        )

    registry = MagicMock()
    registry.list_tools = MagicMock(return_value=[])
    registry.invoke = AsyncMock(side_effect=_invoke)
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=responses)
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    return Planner(
        config={"planner_max_steps": 2},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        reply_provider=MagicMock(),
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


@pytest.mark.asyncio
async def test_rundown_window_without_new_input_does_not_relay() -> None:
    """流程单推进窗没有新输入：递话被拦下，模型收到"直接说话"的观察，游戏侧什么也收不到。"""
    invoked: List[ToolInvocation] = []
    planner = _planner([_relay("拉杆那下被顶掉了，重发一次"), Response(success=True, content="不说")], invoked)
    await planner.plan([], proactive=True, trigger_reason="proactive:rundown")
    assert invoked == []


@pytest.mark.asyncio
async def test_game_request_and_reminder_windows_still_relay() -> None:
    """游戏侧请求定夺、运营提醒到达的主动窗仍可补充要求。"""
    invoked: List[ToolInvocation] = []
    planner = _planner([_relay("换个办法：从 AE 取铁板"), Response(success=True, content="不说")], invoked)
    await planner.plan([], proactive=True, trigger_reason="proactive:game")
    assert [call.tool_name for call in invoked] == ["framework_prompt"]

    invoked.clear()
    planner = _planner([_relay("运营说先停一下"), Response(success=True, content="不说")], invoked)
    await planner.plan([], proactive=True, trigger_reason="proactive:rundown", reminders="先停一下")
    assert [call.tool_name for call in invoked] == ["framework_prompt"]


@pytest.mark.asyncio
async def test_only_fresh_game_chat_counts_as_new_input() -> None:
    """冷场窗里只有看过的旧聊天不算新输入，递话被拦；本窗新到玩家原话才放行。"""
    old_chat = "[3 分钟前] [minecraft] 玩家 Steve：在吗"
    invoked: List[ToolInvocation] = []
    planner = _planner([_relay("回 Steve 一句"), Response(success=True, content="不说")], invoked)
    await planner.plan([], proactive=True, trigger_reason="proactive:cold", game_chat=old_chat)
    assert invoked == []

    planner = _planner([_relay("回 Steve 一句"), Response(success=True, content="不说")], invoked)
    await planner.plan(
        [],
        proactive=True,
        trigger_reason="proactive:cold",
        game_chat=f"{old_chat}\n[新·刚刚] [minecraft] 玩家 Steve：你在干嘛",
        fresh_game_chat=["[minecraft] 玩家 Steve：你在干嘛"],
    )
    assert [call.tool_name for call in invoked] == ["framework_prompt"]


@pytest.mark.asyncio
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
    agent = MinecraftAgent(
        MinecraftConfig(), llm_manager=MagicMock(), event_bus=MagicMock(), tool_registry=ToolRegistry()
    )
    agent.receive_prompt(content="拉下停机开关", source="operator")
    agent.receive_prompt(content="停机开关别碰", source="planner-react")
    agent.receive_prompt(content="继续", source="test")
    queued: List[Any] = list(agent._message_queue)
    assert all(isinstance(item, MinecraftInstruction) for item in queued)
    assert [item[1] for item in queued] == ["[运营原话]\n拉下停机开关", "[主播补充]\n停机开关别碰", "继续"]


def test_prompts_state_relay_and_completion_rules() -> None:
    """提示词写明：补充只写新要求、完成要收进背包、原话优先于转述、一次一个身体动作。"""
    reset_prompt_manager()
    try:
        planner = get_prompt_manager().render("amaidesu_planner_react", behavior_style="积极互动")
        game = get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()
    assert "补充只写**新的要求**" in planner and "没有新输入的流程单推进、冷场、定时窗口不补充" in planner
    assert "怎样算做完" in planner and "观众没说数量就按一个算" in planner
    assert "要的东西要收进背包才算到手" in game and "[运营原话]" in game and "[主播补充]" in game
    assert "一次只提交一个身体动作的 `maicraft_execute`" in game
    assert "ability_signature" in game
