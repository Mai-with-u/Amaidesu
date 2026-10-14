"""话题重复闸：纯函数判重口径、Planner 的 reply 打回路径与闸后沉默兜底"""

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


@pytest.mark.parametrize(
    ("topic", "recent_topics", "recent_speeches", "fresh_texts", "allowed", "duplicate"),
    [
        # 换皮复述（同义改写、加语气词）bigram 大面积重合
        ("我在游戏里挖到了石料", ["石料进展"], ["我在游戏里挖到了石料"], None, False, True),
        # 同话题但超过一半成分是新的
        ("石料要拿去修水车了", ["挖石料"], ["我在游戏里挖到了石料"], None, True, False),
        # 新实体豁免：字面重合，但带进了本窗新语料里才出现的新词
        ("我在游戏里挖到了附魔书", ["石料进展"], ["我在游戏里挖到了石料"], ["观众：附魔书是什么"], True, True),
        # 换数字写法不构成新角度
        ("背包里就剩2.8滴血了，僵尸还在贴脸", ["血量见底"], ["背包里就剩五滴血了，僵尸还在贴脸"], None, False, True),
        # 新语料里的数字不充当新实体证据：叙事数值刷新不是新角度
        (
            "那群苦力怕在远处排队，血就剩五点了",
            ["怪群围观"],
            ["那群苦力怕在远处排队"],
            ["远处那三十五只苦力怕、十六只骷髅都没散"],
            False,
            True,
        ),
        # 空白/单字话题与无历史语料：无从比对，交由提示词纪律
        ("   ", ["石料"], ["挖到石料了"], None, True, False),
        ("好", ["石料"], ["挖到石料了"], None, True, False),
        ("随便什么话题", [], [], None, True, False),
    ],
)
def test_check_topic(topic, recent_topics, recent_speeches, fresh_texts, allowed: bool, duplicate: bool) -> None:
    kwargs = {"fresh_texts": fresh_texts} if fresh_texts else {}
    verdict = check_topic(topic, recent_topics=recent_topics, recent_speeches=recent_speeches, **kwargs)
    assert (verdict.allowed, verdict.duplicate) == (allowed, duplicate)
    if duplicate and not allowed:
        assert verdict.overlap_ratio >= 0.5


def test_rejection_reason_offers_new_angle_then_silence() -> None:
    assert "换新角度" in rejection_reason(False)
    assert "保持沉默" in rejection_reason(True) and "不再调用任何工具" in rejection_reason(True)


# planner 接线：打回 → 再次拒绝 → 沉默兜底；新话题放行


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

    assert (outcome["replied"], outcome["silent_reason"], outcome["speech"]) == (False, "natural", None)
    assert (outcome["topic_gate_retries"], outcome["reply_failures"]) == (2, 0)
    # reply 从未真正执行到表达侧
    assert reply.topics == []
    # 打回经工具回执喂回：最后一次请求里两条 tool 回执，先改题机会、后沉默引导
    last_messages = llm.generate.call_args_list[-1].args[0]
    tool_texts = [m["content"] for m in last_messages if m.get("role") == "tool"]
    assert len(tool_texts) == 2
    assert "换新角度" in tool_texts[0]
    assert "保持沉默" in tool_texts[1]


async def test_gate_passes_fresh_topic_and_records_intent() -> None:
    """新话题正常放行：reply 成功收尾，台词随意图留档供后续窗口比对。"""
    planner, _llm, reply = _planner([_reply_response("新话题：水车修好了")])
    _seed_spoken(planner)

    outcome = await planner.plan([], proactive=True, game_narrative="水车转起来了")

    assert outcome["replied"] is True
    assert outcome["topic_gate_retries"] == 0
    assert reply.topics == ["新话题：水车修好了"]
    assert planner._recent_intents[-1].speech == "说了：新话题：水车修好了"


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
