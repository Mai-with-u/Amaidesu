"""Planner ReAct 循环测试。

覆盖：工具面构造 / ReAct 循环（reply 收尾、自然终止、超步）/ registry 路由
与观察喂回 / LLM 失败降级 / 上下文组装两路径 / 记忆召回 / 可观测副产品。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.llm.manager import LLMResponse
from src.modules.tools.models import ToolSpec, ToolExecutionResult


def _tc(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _resp(content: str = "", tool_calls: list | None = None) -> LLMResponse:
    return LLMResponse(success=True, content=content, tool_calls=tool_calls or [])


def _make_planner(
    chat_responses: list | None = None,
    registry: Any | None = None,
    reply_provider: Any | None = None,
    memory: Any | None = None,
    context_enabled: bool = True,
    max_steps: int = 8,
) -> tuple[Planner, MagicMock, MagicMock]:
    """构造测试 Planner：mock LLM（chat_messages）+ mock prompt_service。

    registry 缺省给一个空 registry mock；reply_provider 缺省给一个成功 mock。
    """
    llm = MagicMock()
    llm.chat_messages = AsyncMock(side_effect=list(chat_responses) if chat_responses else [])

    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="SYSTEM_PROMPT")

    reg = registry if registry is not None else MagicMock()
    if not hasattr(reg, "list_tools"):
        reg.list_tools = MagicMock(return_value=[])

    if reply_provider is not None:
        prov = reply_provider
    else:
        # 默认成功 Provider：invoke 必须是 AsyncMock（await 语义）
        prov = MagicMock()
        prov.invoke = AsyncMock(
            return_value=ToolExecutionResult(
                tool_name="reply",
                success=True,
                structured_content={
                    "speech": "测试回复",
                    "emotion": {"name": "happy", "intensity": 0.6},
                    "actions": [],
                    "metadata": {"target": "u1"},
                },
            )
        )

    planner = Planner(
        config={"planner_llm": "llm", "planner_max_steps": max_steps},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=reg,
        memory=memory,
        context_enabled=context_enabled,
        reply_provider=prov,
    )
    return planner, llm, prompt


def _msg(text: str = "hi", mid: str = "m1") -> Any:
    msg = MagicMock()
    msg.text = text
    msg.user_nickname = "观众"
    msg.message_id = mid
    msg.data_type = "text"
    return msg


# ---------------------------------------------------------------------------
# 工具面
# ---------------------------------------------------------------------------


def test_tool_face_reply_first_and_streamer_filtered() -> None:
    """工具面 = reply + registry 工具；provider=streamer 的内部协议被过滤。"""
    registry = MagicMock()
    registry.list_tools.return_value = [
        ToolSpec(name="minecraft_get_state", description="查状态", parameters_schema={"type": "object"}, kind="sync", provider="minecraft"),
        ToolSpec(name="reply", description="内部协议残留", parameters_schema=None, kind="sync", provider="streamer"),
        ToolSpec(name="parse_command", description="内部协议", parameters_schema=None, kind="sync", provider="streamer"),
    ]
    planner, _llm, _prompt = _make_planner(registry=registry)

    face = planner._build_tool_face()
    names = [f["name"] for f in face]
    assert names[0] == "reply"
    assert "minecraft_get_state" in names
    assert "reply" not in names[1:]
    assert "parse_command" not in names


def test_tool_face_registry_missing_still_reply() -> None:
    """registry 未注入：工具面退化为仅 reply。"""
    planner = Planner(
        config={"planner_llm": "llm"},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=None,
        reply_provider=MagicMock(),
    )
    face = planner._build_tool_face()
    assert [f["name"] for f in face] == ["reply"]


# ---------------------------------------------------------------------------
# ReAct 循环
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_react_reply_terminates_loop() -> None:
    """LLM 调 reply → 经 reply_provider 执行 → 循环立即终止（说话即收尾）。"""
    llm_resp = _resp(tool_calls=[_tc("reply", {"topic_summary": "t", "target": "m1"})])
    planner, llm, _prompt = _make_planner(chat_responses=[llm_resp])

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is True
    assert outcome["speech"] == "测试回复"
    assert outcome["silent_reason"] is None
    assert outcome["steps"] == 1
    assert outcome["tool_trace"] == ["reply"]
    # reply 之后不再有下一轮 LLM 调用
    assert llm.chat_messages.await_count == 1


@pytest.mark.asyncio
async def test_react_natural_termination_silent() -> None:
    """LLM 无 tool_calls → 自然终止（静默，不说话）。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp("这轮不说话")])

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is False
    assert outcome["silent_reason"] == "natural"
    assert llm.chat_messages.await_count == 1


@pytest.mark.asyncio
async def test_react_registry_tool_then_reply() -> None:
    """先调 registry 工具（观察喂回）→ 再调 reply 收尾。"""
    registry = MagicMock()
    registry.list_tools.return_value = [
        ToolSpec(name="minecraft_get_state", description="查", parameters_schema={"type": "object"}, kind="sync", provider="minecraft"),
    ]
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(tool_name="minecraft_get_state", success=True, structured_content={"todo": []})
    )
    chat = [
        _resp(tool_calls=[_tc("minecraft_get_state", {}, "c1")]),
        _resp(tool_calls=[_tc("reply", {"topic_summary": "t"}, "c2")]),
    ]
    planner, llm, _prompt = _make_planner(chat_responses=chat, registry=registry)

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is True
    assert outcome["tool_trace"] == ["minecraft_get_state", "reply"]
    assert llm.chat_messages.await_count == 2
    # 观察喂回：第二轮 messages 含 tool role + tool_call_id 关联
    second = llm.chat_messages.await_args_list[1].kwargs["messages"]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"
    assert '"todo"' in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_react_max_steps_silent() -> None:
    """LLM 恒调非 reply 工具 → 超步静默，恰好 max_steps 轮。"""
    registry = MagicMock()
    registry.list_tools.return_value = [
        ToolSpec(name="tool_x", description="x", parameters_schema={"type": "object"}, kind="sync", provider="x"),
    ]
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(tool_name="tool_x", success=True, structured_content={"ok": True})
    )
    planner, llm, _prompt = _make_planner(
        chat_responses=[_resp(tool_calls=[_tc("tool_x", {}, f"c{i}")]) for i in range(20)],
        registry=registry,
        max_steps=3,
    )

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is False
    assert outcome["silent_reason"] == "max_steps"
    assert outcome["steps"] == 3
    assert llm.chat_messages.await_count == 3


@pytest.mark.asyncio
async def test_react_tool_failure_fed_back_to_llm() -> None:
    """registry 工具失败 → 失败观察喂回 LLM（LLM 自调整）。"""
    registry = MagicMock()
    registry.list_tools.return_value = [
        ToolSpec(name="tool_x", description="x", parameters_schema={"type": "object"}, kind="sync", provider="x"),
    ]
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(tool_name="tool_x", success=False, error_message="world not loaded")
    )
    chat = [
        _resp(tool_calls=[_tc("tool_x", {}, "c1")]),
        _resp(),
    ]
    planner, llm, _prompt = _make_planner(chat_responses=chat, registry=registry)

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is False
    second = llm.chat_messages.await_args_list[1].kwargs["messages"]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert tool_msgs and "world not loaded" in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_react_reply_unavailable_fed_back() -> None:
    """reply Provider 未绑定 → 观察报错（不崩溃，LLM 可调整或自然终止）。"""
    planner = Planner(
        config={"planner_llm": "llm", "planner_max_steps": 3},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=MagicMock(),
        reply_provider=None,
    )
    planner._llm_service.chat_messages = AsyncMock(
        side_effect=[
            _resp(tool_calls=[_tc("reply", {"topic_summary": "t"})]),
            _resp(),
        ]
    )

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is False
    assert outcome["silent_reason"] == "natural"


@pytest.mark.asyncio
async def test_react_llm_error_outcome() -> None:
    """chat_messages 抛异常 → llm_error outcome（不抛出）。"""
    llm = MagicMock()
    llm.chat_messages = AsyncMock(side_effect=RuntimeError("boom"))
    planner, _llm, _prompt = _make_planner()
    planner._llm_service = llm

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is False
    assert outcome["silent_reason"] == "llm_error"
    assert "boom" in outcome["error"]
    assert planner.last_failure is not None


# ---------------------------------------------------------------------------
# 上下文组装
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_bare_path_when_disabled() -> None:
    """context_enabled=False → 裸消息路径（直播流窗口文本作 user 消息）。"""
    planner, llm, prompt = _make_planner(chat_responses=[_resp()], context_enabled=False)

    await planner.plan([_msg("主播好")], history=[])

    user_msg = llm.chat_messages.await_args.kwargs["messages"][1]
    assert user_msg["role"] == "user"
    assert "主播好" in user_msg["content"]
    # 系统提示词只渲染 behavior_style（无 context_block 变量）
    prompt.render_safe.assert_called_once()
    assert prompt.render_safe.call_args.args[0] == "amaidesu_planner_react"


@pytest.mark.asyncio
async def test_context_forced_annotation_in_user_message() -> None:
    """forced 情境标注进首轮 user 消息。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp()])

    await planner.plan([_msg()], forced=True)

    user_msg = llm.chat_messages.await_args.kwargs["messages"][1]
    assert "强制回应" in user_msg["content"]


@pytest.mark.asyncio
async def test_context_game_narrative_in_user_message() -> None:
    """游戏叙事注入首轮 user 消息。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp()])

    await planner.plan([_msg()], game_narrative="刚挖到钻石")

    user_msg = llm.chat_messages.await_args.kwargs["messages"][1]
    assert "刚挖到钻石" in user_msg["content"]


# ---------------------------------------------------------------------------
# 记忆召回
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_recall_hits_in_context() -> None:
    """记忆命中 → 注入组装器（context_enabled 路径经 AssemblerInputs）。"""
    memory = MagicMock()
    hit = MagicMock()
    hit.text = "上周聊过工作台"
    hit.score = 0.8
    hit.metadata = {"source": "test"}
    memory.recall = AsyncMock(return_value=[hit])
    planner, llm, _prompt = _make_planner(chat_responses=[_resp()], memory=memory)

    await planner.plan([_msg("工作台")])

    memory.recall.assert_awaited_once()


@pytest.mark.asyncio
async def test_memory_recall_failure_not_blocking() -> None:
    """记忆召回异常 → 不阻断决策（静默降级）。"""
    memory = MagicMock()
    memory.recall = AsyncMock(side_effect=RuntimeError("db down"))
    planner, _llm, _prompt = _make_planner(chat_responses=[_resp()], memory=memory)

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is False
    assert outcome["silent_reason"] == "natural"


# ---------------------------------------------------------------------------
# 可观测副产品
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observability_fields_reset_and_populated() -> None:
    """last_* 副产品每轮重置并在成功路径填充。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp(content="思考中")])

    await planner.plan([_msg()])
    assert planner.last_raw_content == "思考中"
    # 二轮失败路径：last_failure 写入
    planner._llm_service.chat_messages = AsyncMock(side_effect=RuntimeError("x"))
    await planner.plan([_msg()])
    assert planner.last_failure is not None and "x" in planner.last_failure


@pytest.mark.asyncio
async def test_prompt_render_failure_degrades() -> None:
    """提示词渲染失败 → prompt_render_failed outcome（不抛异常）。"""
    planner, _llm, prompt = _make_planner(chat_responses=[_resp()])
    prompt.render_safe = MagicMock(side_effect=RuntimeError("template missing"))

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is False
    assert outcome["silent_reason"] == "prompt_render_failed"
    assert planner.last_failure is not None and "template missing" in planner.last_failure
