"""Planner ReAct 循环测试。

覆盖：工具列表构造 / ReAct 循环（reply 收尾、自然终止、超步）/ registry 路由
与观察作为观察返回 / LLM 失败降级 / 上下文组装两路径 / 记忆召回 / 可观测副产品。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner
from src.agents.streamer.room_state import RoomState
from src.modules.llm.manager import LLMResponse
from src.modules.tools.models import ToolSpec, ToolExecutionResult
from src.modules.tools.registry import ToolRegistry


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
    elapsed_live_provider: Any | None = None,
) -> tuple[Planner, MagicMock, MagicMock]:
    """构造测试 Planner：mock LLM（chat_messages）+ mock prompt_service。

    registry 缺省给一个空 registry mock；reply_provider 缺省给一个成功 mock。
    """
    llm = MagicMock()
    llm.chat_messages = AsyncMock(side_effect=list(chat_responses) if chat_responses else [])

    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM_PROMPT")

    # 缺省真注册表 + 注册 mock reply（统一调用路径：reply 经 registry.invoke）
    reg = registry if registry is not None else ToolRegistry()
    if not hasattr(reg, "list_tools"):
        reg.list_tools = MagicMock(return_value=[])

    if reply_provider is not None:
        prov = reply_provider
    else:
        # 默认成功 Provider：invoke 必须是 AsyncMock（await 语义）
        prov = MagicMock()
        prov.invoke = AsyncMock(
            return_value=ToolExecutionResult(
                tool_name="streamer_reply",
                success=True,
                structured_content={
                    "speech": "测试回复",
                    "emotion": {"name": "happy", "intensity": 0.6},
                    "actions": [],
                    "metadata": {"target": "u1"},
                },
            )
        )

    if isinstance(reg, ToolRegistry) and not reg.has("streamer_reply"):
        from src.modules.tools.provider import make_provider_from_specs
        from src.modules.tools.models import ToolSpec as _TS

        _reply_spec = _TS(name="reply", description="主播发言出口", kind="sync", provider="streamer")

        async def _reply_impl(inv):  # type: ignore[no-untyped-def]
            return await prov.invoke(inv)

        reg.register_provider(
            make_provider_from_specs("streamer", [(_reply_spec, _reply_impl)]),
            visible_to={"streamer_reply": ["streamer"]},
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
        elapsed_live_provider=elapsed_live_provider,
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
# 工具列表
# ---------------------------------------------------------------------------


def test_tool_list_is_for_agent_registry_result() -> None:
    """工具列表 = for_agent("streamer") 注册表结果（全名直出，统一来源）；rundown 例外条件追加。"""
    registry = MagicMock()
    registry.list_tools.return_value = [
        ToolSpec(name="get_work_log", description="查工作文档", parameters_schema={"type": "object"}, kind="sync", provider="minecraft"),
        ToolSpec(name="reply", description="说话出口", parameters_schema=None, kind="sync", provider="streamer"),
    ]
    planner, _llm, _prompt = _make_planner(registry=registry)

    tool_list = planner._build_tool_list()
    names = [f["name"] for f in tool_list]
    assert names == ["minecraft_get_work_log", "streamer_reply"]  # 注册表全名直出、无重复注入


def test_tool_list_registry_missing_is_empty() -> None:
    """registry 未注入：工具列表为空（reply 也来自注册表，统一来源）。"""
    planner = Planner(
        config={"planner_llm": "llm"},
        llm_service=MagicMock(),
        prompt_service=MagicMock(),
        room_state=RoomState(),
        tool_registry=None,
        reply_provider=MagicMock(),
    )
    tool_list = planner._build_tool_list()
    assert tool_list == []


# ---------------------------------------------------------------------------
# ReAct 循环
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_react_reply_terminates_loop() -> None:
    """LLM 调 reply → 经 reply_provider 执行 → 循环立即终止（说话即收尾）。"""
    llm_resp = _resp(tool_calls=[_tc("streamer_reply", {"topic_summary": "t", "target": "m1"})])
    planner, llm, _prompt = _make_planner(chat_responses=[llm_resp])

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is True
    assert outcome["speech"] == "测试回复"
    assert outcome["silent_reason"] is None
    assert outcome["steps"] == 1
    assert outcome["tool_trace"] == ["streamer_reply"]
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
    """先调 registry 工具（观察作为观察返回）→ 再调 reply 收尾。"""
    registry = MagicMock()
    registry.list_tools.return_value = [
        ToolSpec(name="minecraft_get_state", description="查", parameters_schema={"type": "object"}, kind="sync", provider="minecraft"),
    ]
    registry.invoke = AsyncMock(
        return_value=ToolExecutionResult(tool_name="minecraft_get_state", success=True, structured_content={"todo": []})
    )
    chat = [
        _resp(tool_calls=[_tc("minecraft_get_state", {}, "c1")]),
        _resp(tool_calls=[_tc("streamer_reply", {"topic_summary": "t"}, "c2")]),
    ]
    planner, llm, _prompt = _make_planner(chat_responses=chat, registry=registry)

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is True
    assert outcome["tool_trace"] == ["minecraft_get_state", "streamer_reply"]
    assert llm.chat_messages.await_count == 2
    # 观察作为观察返回：第二轮 messages 含 tool role + tool_call_id 关联
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
    """registry 工具失败 → 失败观察作为观察返回 LLM（LLM 自调整）。"""
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
            _resp(tool_calls=[_tc("streamer_reply", {"topic_summary": "t"})]),
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
    """context_enabled=False → 裸消息路径（本批为原生 user 消息，参考段仅情境标注）。"""
    planner, llm, prompt = _make_planner(chat_responses=[_resp()], context_enabled=False)

    await planner.plan([_msg("主播好")], history=[])

    messages = llm.chat_messages.await_args.kwargs["messages"]
    assert messages[1]["role"] == "user"
    assert "主播好" in messages[1]["content"]
    # 系统提示词只渲染 behavior_style（无 context_block 变量）
    prompt.render.assert_called_once()
    assert prompt.render.call_args.args[0] == "amaidesu_planner_react"


@pytest.mark.asyncio
async def test_context_forced_annotation_in_reference_tail() -> None:
    """forced 情境标注进参考段（消息序列尾的 user 消息）。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp()])

    await planner.plan([_msg()], forced=True)

    ref_msg = _reference_message(llm)
    assert ref_msg is not None
    assert "强制回应" in ref_msg["content"]


@pytest.mark.asyncio
async def test_context_game_narrative_in_reference_tail() -> None:
    """游戏叙事注入参考段（消息序列尾）。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp()])

    await planner.plan([_msg()], game_narrative="刚挖到钻石")

    ref_msg = _reference_message(llm)
    assert "刚挖到钻石" in ref_msg["content"]


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
    prompt.render = MagicMock(side_effect=RuntimeError("template missing"))

    outcome = await planner.plan([_msg()])

    assert outcome["replied"] is False
    assert outcome["silent_reason"] == "prompt_render_failed"
    assert planner.last_failure is not None and "template missing" in planner.last_failure


# ---------------------------------------------------------------------------
# 消息构成：原生角色 / 顺序 / 尾部去重（canonical 映射）
# ---------------------------------------------------------------------------


def _reference_message(llm: Any) -> Any:
    """参考段 = 发给 LLM 的消息序列中最后一条 user 消息（序列尾，循环追加在其后）。"""
    messages = llm.chat_messages.await_args.kwargs["messages"]
    return next((m for m in reversed(messages) if m["role"] == "user"), None)


def _history_msg(role: str, content: str, *, message_id: str = "") -> Any:
    msg = MagicMock()
    msg.role = MagicMock(value=role)
    msg.content = content
    msg.sender_name = "观众A" if role == "user" else "主播"
    msg.message_type = "danmaku" if role == "user" else "speak"
    msg.message_id = message_id
    return msg


def test_dialogue_messages_native_roles_in_order() -> None:
    """历史（user/assistant 原生角色）在前、本批（user）在后，canonical 内容同形。"""
    planner, _llm, _prompt = _make_planner()
    history = [
        _history_msg("user", "大家好呀", message_id="m1"),
        _history_msg("assistant", "晚上好啊", message_id="m2"),
    ]

    messages = planner._build_dialogue_messages([_msg("主播好", mid="m9")], history)

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "大家好呀" in messages[0]["content"]
    assert "晚上好啊" in messages[1]["content"]
    assert "主播好" in messages[2]["content"] and "[id:m9]" in messages[2]["content"]


def test_dialogue_messages_tail_dedup_only_strips_trailing_matches() -> None:
    """历史尾部与本批同 content（canonical 全同形，含 id）的消息剔除；更早的同文保留。"""
    planner, _llm, _prompt = _make_planner()
    history = [
        _history_msg("user", "上次也说过落地水", message_id="m1"),
        _history_msg("user", "来个落地水", message_id="m9"),
    ]

    messages = planner._build_dialogue_messages([_msg("来个落地水", mid="m9")], history)

    joined = "\n".join(m["content"] for m in messages)
    assert joined.count("来个落地水") == 1  # 只剩本批一条（历史尾部同 id 消息被剔除）
    assert "上次也说过落地水" in joined


@pytest.mark.asyncio
async def test_context_dedups_batch_from_history_tail() -> None:
    """弹幕先落库再决策：消息序列内本批弹幕不与历史尾部重复。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp()])
    history = [
        _history_msg("user", "大家好呀", message_id="m1"),
        _history_msg("user", "来个落地水", message_id="m9"),
    ]

    await planner.plan([_msg("来个落地水", mid="m9")], history=history)

    msgs = llm.chat_messages.await_args.kwargs["messages"]
    dialogue = [m for m in msgs if m["role"] in ("user", "assistant")][:-1]  # 去掉参考段
    joined = "\n".join(m["content"] for m in dialogue)
    assert joined.count("来个落地水") == 1
    assert "大家好呀" in joined


@pytest.mark.asyncio
async def test_context_omits_char_level_topics() -> None:
    """字符级 topics 不进 prompt——不出现"关键变化"段（单字噪声）。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp()])
    planner._room_state.update(_msg("来个落地水"))
    planner._room_state.update(_msg("来个落地水"))

    await planner.plan([_msg("来个落地水")])

    ref_msg = llm.chat_messages.await_args.kwargs["messages"][-1]
    assert "关键变化" not in ref_msg["content"]


# ---------------------------------------------------------------------------
# 开播时长：provider 注入 / 未注入与异常降级
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_includes_elapsed_live_duration() -> None:
    """开播时长 provider 提供数据 → 快照渲染分钟级时长。"""
    planner, llm, _prompt = _make_planner(
        chat_responses=[_resp()],
        elapsed_live_provider=lambda: 75 * 60_000,
    )

    await planner.plan([_msg()])

    ref_msg = _reference_message(llm)
    assert "已开播时长: 1 小时 15 分钟" in ref_msg["content"]


@pytest.mark.asyncio
async def test_context_elapsed_provider_missing_omits_line() -> None:
    """provider 未注入（未开播语义）→ 快照不含开播时长行。"""
    planner, llm, _prompt = _make_planner(chat_responses=[_resp()])

    await planner.plan([_msg()])

    ref_msg = _reference_message(llm)
    assert "已开播时长" not in ref_msg["content"]


@pytest.mark.asyncio
async def test_context_elapsed_provider_failure_degrades() -> None:
    """provider 抛异常 → 降级为 0（省略该行），不阻断决策。"""
    def _boom() -> int:
        raise RuntimeError("agenda down")

    planner, llm, _prompt = _make_planner(
        chat_responses=[_resp()],
        elapsed_live_provider=_boom,
    )

    outcome = await planner.plan([_msg()])

    ref_msg = _reference_message(llm)
    assert "已开播时长" not in ref_msg["content"]
    assert outcome["silent_reason"] == "natural"
