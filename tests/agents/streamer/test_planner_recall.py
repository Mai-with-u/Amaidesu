"""Planner 回看自己——最近几轮说出口的打算与补充过的要求进参考段，防止连着几轮讲同一件事。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.planner import Planner, _Intent
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.llm.payload import Response, ToolCall
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry


def _reply_call(topic: str, guidance: str) -> Response:
    return Response(
        success=True,
        content="",
        tool_calls=[
            ToolCall(id="c1", name="streamer_reply", arguments={"topic_summary": topic, "reply_guidance": guidance})
        ],
    )


def _planner(
    responses: List[Response],
    *,
    reply_ok: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> Planner:
    """真注册表 + 可切换成败的 reply；上下文组装器关闭，只看情境段。"""
    registry = ToolRegistry()
    reply_result = ToolExecutionResult(
        tool_name="streamer_reply",
        success=reply_ok,
        structured_content={"speech": "说了", "emotion": {"name": "happy"}, "metadata": {}} if reply_ok else None,
        error_message=None if reply_ok else "Replyer 返回 None",
    )

    async def _reply(_invocation: Any) -> ToolExecutionResult:
        return reply_result

    spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
    registry.register_provider(
        make_provider_from_specs("streamer", [(spec, _reply)]), visible_to={"streamer_reply": ["streamer"]}
    )
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=list(responses))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    return Planner(
        config=config or {},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        context_enabled=False,
        reply_provider=MagicMock(),
    )


async def _reference(planner: Planner) -> str:
    text = await planner._assemble_reference([], None, None, False, False, "")
    assert text is not None
    return text


@pytest.mark.asyncio
async def test_spoken_intents_listed_oldest_first() -> None:
    """说出口的每轮话题一句话按先后列进【我最近几轮想说的】，下一窗就看得到自己连着讲了什么。

    指引全文不随行注入：那轮的原话已在对话历史 assistant 消息里，同一信息双份
    只会让参考段每轮平白多出几百字。
    """
    planner = _planner([_reply_call("报平安：满血了", "轻松"), _reply_call("还是满血", "得意")])

    await planner.plan([], proactive=True)
    await planner.plan([], proactive=True)
    text = await _reference(planner)

    assert "【我最近几轮想说的】" in text
    section = text.split("【我最近几轮想说的】", 1)[1].splitlines()[1:]
    assert section == ["- 刚刚 话题：报平安：满血了", "- 刚刚 话题：还是满血"]


@pytest.mark.asyncio
async def test_failed_reply_is_not_recorded() -> None:
    """表达失败（观众没听到）的打算不算说过。"""
    planner = _planner([_reply_call("报平安", "轻松")], reply_ok=False, config={"planner_max_steps": 1})

    await planner.plan([], proactive=True)

    assert "【我最近几轮想说的】" not in await _reference(planner)


@pytest.mark.asyncio
async def test_intents_keep_only_configured_count() -> None:
    """只回看配置的轮数，最早的先出列。"""
    planner = _planner(
        [_reply_call(f"话题{i}", "") for i in range(3)],
        config={"planner_recent_intents_max": 2},
    )

    for _ in range(3):
        await planner.plan([], proactive=True)
    text = await _reference(planner)

    assert "话题0" not in text and "话题1" in text and "话题2" in text


@pytest.mark.asyncio
async def test_zero_intents_disables_section() -> None:
    """配置为 0 时不列这一段。"""
    planner = _planner([_reply_call("报平安", "")], config={"planner_recent_intents_max": 0})

    await planner.plan([], proactive=True)

    assert "【我最近几轮想说的】" not in await _reference(planner)


@pytest.mark.asyncio
async def test_unread_summary_consumed_once() -> None:
    """未读摘要被一个决策窗取走后不再重复注入，直到后台写入新摘要。

    实测"观众向主播问好并表达惊喜"在多轮请求里反复出现，诱导主播重复播报同一件事。
    """
    room_state = RoomState()
    room_state.set_topic_summary("观众向主播问好并表达惊喜")
    planner = Planner({}, MagicMock(), MagicMock(), room_state, context_enabled=True)

    first = await planner._assemble_reference([], None, None, False, False, "")
    second = await planner._assemble_reference([], None, None, False, False, "")

    assert "未读摘要: 观众向主播问好并表达惊喜" in first
    assert "未读摘要" not in second


def _relay_planner(result: ToolExecutionResult) -> tuple[Planner, MagicMock]:
    registry = MagicMock()
    registry.invoke = AsyncMock(return_value=result)
    return Planner({}, MagicMock(), MagicMock(), RoomState(), tool_registry=registry, context_enabled=False), registry


@pytest.mark.asyncio
async def test_game_report_supersedes_inflight_intents() -> None:
    """游戏回执到达后，回执前说出口的在途条目标注已被覆盖，不再带指引全文。

    实测同一轮注入里两条都标"刚刚"且互相矛盾（"石料还没挖进包" vs "攒了 19 块圆石"），
    模型任选一条播报就会前后打脸——回执才是权威事实。
    """
    planner, _registry = _relay_planner(
        ToolExecutionResult(
            tool_name="framework_delegate",
            success=True,
            structured_content={"accepted": True, "executor": "minecraft", "task_id": "deleg_1"},
        )
    )

    await planner._invoke_registry_tool("framework_delegate", {"agent": "minecraft", "instruction": "挖石料"})
    # 委派在途期间说出口的对进展的猜测（此刻与回执说法矛盾）
    planner._recent_intents.append(
        _Intent(at_ms=now_ms(), topic_summary="石料到现在都还没挖进包", reply_guidance="懊恼地汇报白挥了一镐")
    )
    planner.note_game_report("minecraft", "delivery")
    text = await _reference(planner)

    assert "已被游戏回执覆盖" in text
    assert "还没挖进包" in text  # 话题仍保留，反重复记忆不丢
    assert "懊恼" not in text  # 指引全文不随行注入（原话已在对话历史）


@pytest.mark.asyncio
async def test_intent_outside_delegation_window_is_not_superseded() -> None:
    """委派窗口之外的条目不受回执影响：委派开始前的旧话、上报后说的新话都完整保留。"""
    planner, _registry = _relay_planner(
        ToolExecutionResult(
            tool_name="framework_delegate",
            success=True,
            structured_content={"accepted": True, "executor": "minecraft", "task_id": "deleg_1"},
        )
    )

    base = now_ms()
    # 委派开始前说的一轮
    planner._recent_intents.append(
        _Intent(at_ms=base - 60_000, topic_summary="和观众打了个招呼", reply_guidance="热情")
    )
    await planner._invoke_registry_tool("framework_delegate", {"agent": "minecraft", "instruction": "挖石料"})
    delegation = planner._delegations["minecraft"]
    delegation.at_ms = base
    delegation.reported_ms = base + 30_000
    # 回执之后说的一轮
    planner._recent_intents.append(
        _Intent(at_ms=base + 60_000, topic_summary="包里攒了 19 块圆石", reply_guidance="报喜")
    )
    text = await _reference(planner)

    section = text.split("【我最近几轮想说的】", 1)[1].splitlines()[1:]
    assert len(section) == 2
    assert all("已被游戏回执覆盖" not in line for line in section)
    assert "话题：和观众打了个招呼" in section[0]
    assert "话题：包里攒了 19 块圆石" in section[1]



@pytest.mark.asyncio
async def test_delivered_relay_records_own_words_without_source() -> None:
    """送达的补充要求记主播自己的原话，不带附加的来源对话；游戏侧仍收到带来源的版本。"""
    planner, registry = _relay_planner(
        ToolExecutionResult(
            tool_name="framework_prompt",
            success=True,
            structured_content={"delivered": True, "executor": "minecraft"},
        )
    )

    observation = await planner._invoke_registry_tool(
        "framework_prompt",
        {"agent": "minecraft", "content": "机械锯那边别再管了"},
        source_dialogue=[{"role": "user", "content": "观众：锯子修好了吗"}],
    )
    text = await _reference(planner)

    assert json.loads(observation)["delivered"] is True
    assert "[来源对话" in registry.invoke.call_args.args[0].arguments["content"]
    assert "【我最近给手上的事补充过的要求】\n- 刚刚在 minecraft 里补充：机械锯那边别再管了" in text
    assert "来源对话" not in text


@pytest.mark.asyncio
async def test_rejected_or_blocked_relay_is_not_recorded() -> None:
    """拒收的、没有新输入被拦下的补充都没交代到，不能当成说过。"""
    planner, _registry = _relay_planner(
        ToolExecutionResult(tool_name="framework_prompt", success=False, error_message="留言已满")
    )

    await planner._invoke_registry_tool("framework_prompt", {"agent": "minecraft", "content": "先别拆"})
    await planner._invoke_registry_tool(
        "framework_prompt", {"agent": "minecraft", "content": "再说一遍"}, relay_without_input=True
    )

    assert "补充过的要求" not in await _reference(planner)


def test_agent_passes_recall_limits_to_planner() -> None:
    """[agents.streamer] 的回看条数一路传到 Planner。"""
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    agent = StreamerAgent(
        config=StreamerConfig.from_dict(
            {"proactive": {"enabled": False}, "planner_recent_intents_max": 3, "planner_recent_relays_max": 1}
        ),
        llm_manager=MagicMock(),
        prompt_manager=prompt,
    )

    assert agent._planner._recent_intents.maxlen == 3
    assert agent._planner._recent_relays.maxlen == 1
