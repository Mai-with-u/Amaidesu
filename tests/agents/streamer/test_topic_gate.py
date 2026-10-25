"""发言闸：清洗纯函数、台词级重合度口径、reply 工具的打回路径与每窗重试上限"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import ReplyRoundAttachment
from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.agents.streamer.topic_gate import (
    SPEECH_DUPLICATE_RATIO,
    SPEECH_GATE_WINDOW,
    speech_overlap_ratio,
    strip_citations,
)
from src.modules.tools.models import ToolInvocation, ToolSpec


# 清洗与口径（纯函数）


def test_strip_citations_removes_paren_notes_and_ascii_tokens() -> None:
    """中英文括号内的出处引注段与残余 ASCII 字母词元都剥掉，正文中文原样保留
    （数字不剥，留给数值归一压占位符）。"""
    assert (
        strip_citations("刚做出3个云杉木门（见 reflex_finished）；当前任务：装上门（游戏记录）")
        == "刚做出3个云杉木门 ；当前任务：装上门 "
    )
    assert strip_citations("经 craft 拿到羊肉（glance 确认）") == "经   拿到羊肉 "
    assert strip_citations("提交对方块（-86,66,50）的交互") == "提交对方块 的交互"
    assert strip_citations("无引注的普通话题") == "无引注的普通话题"


def test_speech_overlap_ratio_measures_containment() -> None:
    """包含率分母是新台词自身：换皮复述接近 1，全新内容接近 0。"""
    repeat = speech_overlap_ratio("我在游戏里挖到了石料呀", ["我在游戏里挖到了石料"])
    fresh = speech_overlap_ratio("水车修好开始转动了", ["我在游戏里挖到了石料"])
    assert repeat > SPEECH_DUPLICATE_RATIO
    assert fresh < 0.2


def test_speech_overlap_ratio_normalizes_numbers() -> None:
    """数值变化不构成新内容：换数字写法仍是高重合。"""
    ratio = speech_overlap_ratio("血量就剩2.8点了", ["血量就剩五点了"])
    assert ratio >= SPEECH_DUPLICATE_RATIO


def test_speech_overlap_ratio_empty_sides_pass() -> None:
    """任一侧无可比对成分（过短/空白/无历史）返回 0——无从比对即放行。"""
    assert speech_overlap_ratio("好", ["挖到石料了"]) == 0.0
    assert speech_overlap_ratio("随便说点什么", []) == 0.0
    assert speech_overlap_ratio("   ", ["挖到石料了"]) == 0.0


def test_speech_gate_window_default() -> None:
    """比对窗口默认最近 10 条（场次 31 回放定标：正常发言与复读在此窗口分层最清晰）。"""
    assert SPEECH_GATE_WINDOW == 10


# reply 工具接线：is_novel 自检打回 → 台词重合度打回 → 每窗重试上限


def _invocation(arguments: dict[str, Any], round_id: str = "", attachment: Any = None) -> ToolInvocation:
    return ToolInvocation(
        tool_name="streamer_reply",
        arguments=arguments,
        source="planner-react",
        round_id=round_id,
        attachment=attachment,
    )


def _history(*speeches: str) -> List[Any]:
    """对话历史桩：观众/主播行交错，主播行携带台词原文。"""
    turns: List[Any] = []
    for i, speech in enumerate(speeches):
        turns.append(
            SimpleNamespace(role="user", content=f"观众{i}: 随便聊聊", sender_name="", message_type="", message_id="")
        )
        turns.append(SimpleNamespace(role="assistant", content=speech, sender_name="", message_type="", message_id=""))
    return turns


def _provider(history: Optional[List[Any]] = None) -> ReplyToolProvider:
    class _Replyer:
        async def generate(self, **kwargs: Any) -> dict[str, Any]:
            return {"speech": "台词", "llm_request_id": None}

    return ReplyToolProvider(replyer=_Replyer(), history_provider=(lambda: history) if history else None)


async def test_is_novel_false_is_rejected_immediately() -> None:
    """自评重复（is_novel=false）立即打回，不触达表达侧。"""
    provider = _provider(_history("我在游戏里挖到了石料"))
    result = await provider.invoke(
        _invocation({"topic_summary": "石料", "is_novel": False, "emotion": "neutral", "intensity": 0.5})
    )
    assert result.success is False
    assert (
        result.error_message
        == "你自评这是重复——要么换个新角度拿出真内容重新调用（新进展，或她对这件事的新态度、新联想），要么本轮别发言"
    )


async def test_is_novel_missing_is_rejected() -> None:
    """未申报（非布尔）同样打回并提示申报义务，防静默绕过。"""
    provider = _provider()
    result = await provider.invoke(_invocation({"topic_summary": "话题", "emotion": "neutral", "intensity": 0.5}))
    assert result.success is False
    assert "is_novel" in (result.error_message or "")


async def test_duplicate_speech_bounces_with_overlap_summary() -> None:
    """生成后台词与最近发言大面积重合：打回并附重合度概况。"""
    provider = _provider(_history("我在游戏里挖到了石料"))
    replyer = provider._replyer

    async def _repeat(**kwargs: Any) -> dict[str, Any]:
        return {"speech": "我在游戏里挖到了石料呀", "llm_request_id": None}

    replyer.generate = _repeat  # type: ignore[method-assign]
    result = await provider.invoke(
        _invocation({"topic_summary": "石料", "is_novel": True, "emotion": "neutral", "intensity": 0.5})
    )
    assert result.success is False
    assert result.error_message is not None and "重合度" in result.error_message
    assert "真内容" in result.error_message
    assert result.structured_content == {"speech_gate_bounces": 1}


async def test_speech_gate_retry_limit_one_per_window() -> None:
    """每窗重试上限 1 次：第二次重合打回改引导沉默；换窗清零后恢复首次话术。"""
    provider = _provider(_history("我在游戏里挖到了石料"))
    replyer = provider._replyer

    async def _repeat(**kwargs: Any) -> dict[str, Any]:
        return {"speech": "我在游戏里挖到了石料呀", "llm_request_id": None}

    replyer.generate = _repeat  # type: ignore[method-assign]
    arguments = {"topic_summary": "石料", "is_novel": True, "emotion": "neutral", "intensity": 0.5}

    first = await provider.invoke(_invocation(dict(arguments), round_id="window-1"))
    assert first.success is False and "真内容" in (first.error_message or "")

    second = await provider.invoke(_invocation(dict(arguments), round_id="window-1"))
    assert second.success is False
    assert second.error_message is not None and "保持沉默" in second.error_message
    assert "不再调用任何工具" in second.error_message

    # 新决策窗：round_id 变化即清零，重新拿到完整的重试机会
    third = await provider.invoke(_invocation(dict(arguments), round_id="window-2"))
    assert third.success is False and "真内容" in (third.error_message or "")


async def test_fresh_speech_passes_and_logs_telemetry() -> None:
    """新内容台词放行；遥测三字段（申报值/结果/重合度）进既有工具日志。"""
    provider = _provider(_history("我在游戏里挖到了石料"))
    with pytest.MonkeyPatch.context() as mp:
        records: List[str] = []
        mp.setattr(provider._logger, "info", lambda message, **kw: records.append(str(message)))
        result = await provider.invoke(
            _invocation({"topic_summary": "水车", "is_novel": True, "emotion": "neutral", "intensity": 0.5})
        )
    assert result.success is True
    assert any("is_novel=true" in r and "结果=pass" in r and "重合度=" in r for r in records)


async def test_new_round_id_resets_bounce_counter() -> None:
    """换决策窗（round_id 变化）即清零打回计数：新窗首次重合仍按首次话术打回，
    计数从 1 重新起算；同窗调用不重置。"""
    provider = _provider(_history("我在游戏里挖到了石料"))
    replyer = provider._replyer

    async def _repeat(**kwargs: Any) -> dict[str, Any]:
        return {"speech": "我在游戏里挖到了石料呀", "llm_request_id": None}

    replyer.generate = _repeat  # type: ignore[method-assign]
    arguments = {"topic_summary": "石料", "is_novel": True, "emotion": "neutral", "intensity": 0.5}

    first = await provider.invoke(_invocation(dict(arguments), round_id="window-1"))
    assert first.structured_content == {"speech_gate_bounces": 1}

    # 同窗第二次重合：计数累加到 2（进重试耗尽分支）
    second = await provider.invoke(_invocation(dict(arguments), round_id="window-1"))
    assert second.structured_content == {"speech_gate_bounces": 2}

    # 换窗：计数清零，重合台词重新按首次话术打回
    third = await provider.invoke(_invocation(dict(arguments), round_id="window-2"))
    assert third.structured_content == {"speech_gate_bounces": 1}
    assert "真内容" in (third.error_message or "")


async def test_wrong_attachment_type_fails_invoke() -> None:
    """attachment 不是 ReplyRoundAttachment：按编程错误以失败结果暴露，不静默。"""
    provider = _provider()
    result = await provider.invoke(
        _invocation(
            {"topic_summary": "话题", "is_novel": True, "emotion": "neutral", "intensity": 0.5},
            attachment="not-an-attachment",
        )
    )
    assert result.success is False
    assert result.error_message is not None and "attachment 类型不符" in result.error_message


async def test_gate_exempt_window_skips_overlap_check() -> None:
    """道别窗豁免：字面重合的台词照常放行（闸会系统性误杀道别）。"""
    provider = _provider(_history("谢谢大家今天的陪伴，我们下次再见"))
    replyer = provider._replyer

    async def _farewell(**kwargs: Any) -> dict[str, Any]:
        return {"speech": "谢谢大家今天的陪伴，我们下次再见呀", "llm_request_id": None}

    replyer.generate = _farewell  # type: ignore[method-assign]
    result = await provider.invoke(
        _invocation(
            {"topic_summary": "道别", "is_novel": True, "emotion": "neutral", "intensity": 0.5},
            attachment=ReplyRoundAttachment(gate_exempt=True),
        )
    )
    assert result.success is True


async def test_gate_window_config_zero_disables_check() -> None:
    """窗口配置为 0 即关闭台词重合度检查（自检 false 仍打回）。"""
    provider = ReplyToolProvider(
        replyer=_RepeatReplyer(), history_provider=lambda: _history("我在游戏里挖到了石料"), gate_window=0
    )
    result = await provider.invoke(
        _invocation({"topic_summary": "石料", "is_novel": True, "emotion": "neutral", "intensity": 0.5})
    )
    assert result.success is True


class _RepeatReplyer:
    """固定复读同一句台词的表达层桩（窗口关闭时应照常放行）。"""

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        return {"speech": "我在游戏里挖到了石料呀", "llm_request_id": None}


# Planner 接线：每窗入口清零 Provider 打回计数；打回经工具回执喂回，outcome 带打回次数


def _planner_with_real_provider(provider: ReplyToolProvider, responses: List[Any]) -> tuple[Any, AsyncMock]:
    from src.agents.streamer.planner import Planner
    from src.agents.streamer.room_state import RoomState
    from src.modules.tools.provider import make_provider_from_specs
    from src.modules.tools.registry import ToolRegistry

    registry = ToolRegistry()
    spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
    registry.register_provider(
        make_provider_from_specs("streamer", [(spec, provider.invoke)]), visible_to={"streamer_reply": ["streamer"]}
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
        reply_provider=provider,
    )
    return planner, llm


def _reply_call(topic: str) -> Any:
    from src.modules.llm.payload import Response, ToolCall

    return Response(
        success=True,
        content="",
        tool_calls=[
            ToolCall(
                id="c1",
                name="streamer_reply",
                arguments={"topic_summary": topic, "is_novel": True, "emotion": "neutral", "intensity": 0.5},
            )
        ],
    )


def _silent_call() -> Any:
    from src.modules.llm.payload import Response

    return Response(success=True, content="（沉默）", tool_calls=[])


def _provider_with_fixed_speech(history: Optional[List[Any]] = None) -> ReplyToolProvider:
    """表达层桩：固定复读同一句（与历史语料字面重合）的 ReplyToolProvider。"""
    return ReplyToolProvider(replyer=_RepeatReplyer(), history_provider=(lambda: history) if history else None)


async def test_planner_passes_duplicate_speech_to_next_window_clean() -> None:
    """台词重合打回经工具回执喂回：本窗 reply_failures 计数、打回话术进观察，
    outcome.speech_gate_bounces 读自失败结果的 structured_content；换窗
    （round_id 变化）后计数从零开始。"""
    provider = _provider_with_fixed_speech(_history("我在游戏里挖到了石料"))
    planner, llm = _planner_with_real_provider(provider, [_reply_call("石料"), _silent_call()])

    outcome = await planner.plan([], proactive=True, round_id="window-1")

    assert (outcome["replied"], outcome["silent_reason"], outcome["speech"]) == (False, "natural", None)
    assert outcome["reply_failures"] == 1
    assert outcome["speech_gate_bounces"] == 1
    last_messages = llm.generate.call_args_list[-1].args[0]
    tool_texts = [m["content"] for m in last_messages if m.get("role") == "tool"]
    assert len(tool_texts) == 1 and "真内容" in tool_texts[0]

    # 下一窗：round_id 变化使 Provider 清零，重合台词再次拿到完整的首次打回话术
    llm.generate.side_effect = [_reply_call("石料"), _silent_call()]
    outcome = await planner.plan([], proactive=True, round_id="window-2")
    assert outcome["speech_gate_bounces"] == 1
    last_messages = llm.generate.call_args_list[-1].args[0]
    tool_texts = [m["content"] for m in last_messages if m.get("role") == "tool"]
    assert "真内容" in tool_texts[0]


async def test_planner_gate_bounces_accumulate_within_window() -> None:
    """同窗内第二次重合：Provider 改引导沉默，outcome 计到 2。"""
    provider = _provider_with_fixed_speech(_history("我在游戏里挖到了石料"))
    planner, llm = _planner_with_real_provider(provider, [_reply_call("石料"), _reply_call("石料"), _silent_call()])

    outcome = await planner.plan([], proactive=True, round_id="window-1")

    assert outcome["silent_reason"] == "natural"
    assert outcome["speech_gate_bounces"] == 2
    last_messages = llm.generate.call_args_list[-1].args[0]
    tool_texts = [m["content"] for m in last_messages if m.get("role") == "tool"]
    assert len(tool_texts) == 2 and "保持沉默" in tool_texts[1]
