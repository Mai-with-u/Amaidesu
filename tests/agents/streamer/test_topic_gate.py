"""话题重复闸——纯函数检测口径 + planner reply 打回路径 + 闸后沉默兜底。"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner, _Intent
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.topic_gate import check_topic, rejection_reason
from src.modules.llm.payload import Response, ToolCall
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# 纯函数口径
# ---------------------------------------------------------------------------


def test_paraphrased_repeat_is_rejected() -> None:
    """换皮复述（同义改写、加语气词）bigram 大面积重合，判重拒绝。"""
    verdict = check_topic(
        "我在游戏里挖到了石料",
        recent_topics=["石料进展"],
        recent_speeches=["我在游戏里挖到了石料"],
    )

    assert not verdict.allowed
    assert verdict.duplicate
    assert verdict.overlap_ratio >= 0.5


def test_fresh_angle_passes() -> None:
    """同话题但换了实质角度（超过一半成分是新的），放行。"""
    verdict = check_topic(
        "石料要拿去修水车了",
        recent_topics=["挖石料"],
        recent_speeches=["我在游戏里挖到了石料"],
    )

    assert verdict.allowed
    assert not verdict.duplicate


def test_new_entity_exempt_even_when_literal_overlap() -> None:
    """新实体豁免：字面大面积重合，但话题带进了本窗新语料里才出现的新词。"""
    verdict = check_topic(
        "我在游戏里挖到了附魔书",
        recent_topics=["石料进展"],
        recent_speeches=["我在游戏里挖到了石料"],
        fresh_texts=["观众：附魔书是什么"],
    )

    assert verdict.allowed
    assert verdict.duplicate  # 字面确实重合，放行凭的是新词


def test_numeric_rewrite_is_rejected() -> None:
    """数值变化不构成新角度：同一句话换数字写法（五点 → 2.8）仍判重复。"""
    verdict = check_topic(
        "背包里就剩2.8滴血了，僵尸还在贴脸",
        recent_topics=["血量见底"],
        recent_speeches=["背包里就剩五滴血了，僵尸还在贴脸"],
    )

    assert not verdict.allowed
    assert verdict.duplicate


def test_fresh_numbers_do_not_arm_exemption() -> None:
    """本窗新语料里的数字（三十五只、十六只）不充当新实体豁免的证据——
    游戏叙事的数值刷新不是新角度。"""
    verdict = check_topic(
        "那群苦力怕在远处排队，血就剩五点了",
        recent_topics=["怪群围观"],
        recent_speeches=["那群苦力怕在远处排队"],
        fresh_texts=["远处那三十五只苦力怕、十六只骷髅都没散"],
    )

    assert not verdict.allowed
    assert verdict.duplicate


def test_short_or_blank_topic_passes() -> None:
    """空白或单字话题没有可比对成分，交由提示词纪律而非框架拦下。"""
    for topic in ("", "   ", "好"):
        verdict = check_topic(topic, recent_topics=["石料"], recent_speeches=["挖到石料了"])
        assert verdict.allowed


def test_no_recent_history_passes() -> None:
    """无最近发言语料时无从判重，直接放行。"""
    verdict = check_topic("随便什么话题", recent_topics=[], recent_speeches=[])

    assert verdict.allowed


def test_retry_rejection_guides_silence() -> None:
    """打回话术：首次给改题机会，再次拒绝显式引导本轮沉默。"""
    assert "换新角度" in rejection_reason(False)
    assert "保持沉默" in rejection_reason(True)
    assert "不再调用任何工具" in rejection_reason(True)


# ---------------------------------------------------------------------------
# planner 接线：打回 → 再次拒绝 → 沉默兜底；新话题放行
# ---------------------------------------------------------------------------


def _reply_response(topic: str) -> Response:
    return Response(
        success=True,
        content="",
        tool_calls=[ToolCall(id="c1", name="streamer_reply", arguments={"topic_summary": topic, "reply_guidance": ""})],
    )


def _silent_response() -> Response:
    return Response(success=True, content="（沉默）", tool_calls=[])


class _ReplyStub:
    """可切换成败的 reply Provider 桩（记录收到的 topic）。"""

    def __init__(self) -> None:
        self.topics: List[str] = []

    async def __call__(self, invocation: Any) -> ToolExecutionResult:
        self.topics.append(str(invocation.arguments.get("topic_summary") or ""))
        return ToolExecutionResult(
            tool_name="streamer_reply",
            success=True,
            structured_content={"speech": f"说了：{self.topics[-1]}", "metadata": {}},
        )


def _planner(responses: List[Response]) -> tuple[Planner, MagicMock, _ReplyStub]:
    registry = ToolRegistry()
    reply = _ReplyStub()

    spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
    registry.register_provider(
        make_provider_from_specs("streamer", [(spec, reply.__call__)]), visible_to={"streamer_reply": ["streamer"]}
    )
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=list(responses))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    planner = Planner(
        config={},
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=registry,
        context_enabled=False,
    )
    return planner, llm, reply


_REPEAT_TOPIC = "我在游戏里挖到了石料"


def _seed_spoken(planner: Planner) -> None:
    """预置一轮已说出口的话题+台词：闸要有"最近发言"才判得了重。"""
    planner._recent_intents.append(
        _Intent(at_ms=now_ms(), topic_summary="石料进展", reply_guidance="", speech=_REPEAT_TOPIC)
    )


@pytest.mark.asyncio
async def test_gate_rejects_repeat_then_silence_fallback() -> None:
    """重复话题两次被打回后模型选择沉默：自然终止（无产出结束）是合法终态。

    打回走工具回执（reply_failures 不增——不是 registry 执行失败），
    二次拒绝话术显式引导本轮沉默。
    """
    planner, llm, reply = _planner(
        [_reply_response(_REPEAT_TOPIC), _reply_response(_REPEAT_TOPIC + "呀"), _silent_response()]
    )
    _seed_spoken(planner)

    outcome = await planner.plan([], proactive=True, game_narrative="挖到了石料")

    assert outcome["replied"] is False
    assert outcome["silent_reason"] == "natural"
    assert outcome["topic_gate_retries"] == 2
    assert outcome["reply_failures"] == 0
    assert outcome["speech"] is None
    # reply 从未真正执行到表达侧
    assert reply.topics == []
    # 打回经工具回执喂回：最后一次请求里两条 tool 回执，先改题机会、后沉默引导
    last_messages = llm.generate.call_args_list[-1].args[0]
    tool_texts = [m["content"] for m in last_messages if m.get("role") == "tool"]
    assert len(tool_texts) == 2
    assert "换新角度" in tool_texts[0]
    assert "保持沉默" in tool_texts[1]


@pytest.mark.asyncio
async def test_gate_passes_fresh_topic_and_records_intent() -> None:
    """新话题正常放行：reply 成功收尾，台词随意图留档供后续窗口比对。"""
    planner, _llm, reply = _planner([_reply_response("新话题：水车修好了")])
    _seed_spoken(planner)

    outcome = await planner.plan([], proactive=True, game_narrative="水车转起来了")

    assert outcome["replied"] is True
    assert outcome["topic_gate_retries"] == 0
    assert reply.topics == ["新话题：水车修好了"]
    assert planner._recent_intents[-1].speech == "说了：新话题：水车修好了"


@pytest.mark.asyncio
async def test_gate_retries_reset_per_plan() -> None:
    """打回计数是本轮口径：上一窗打回过，下一窗从零计。"""
    planner, llm, _reply = _planner([_reply_response(_REPEAT_TOPIC), _silent_response()])
    _seed_spoken(planner)

    # 上一窗：打回一次后收场
    await planner.plan([], proactive=True, game_narrative="挖到了石料")

    # 下一窗：同样的话题仍能拿到完整的两次打回机会
    llm.generate.side_effect = [_reply_response(_REPEAT_TOPIC), _silent_response()]
    outcome = await planner.plan([], proactive=True, game_narrative="挖到了石料")

    assert outcome["topic_gate_retries"] == 1
    assert outcome["silent_reason"] == "natural"
