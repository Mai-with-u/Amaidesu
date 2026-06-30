"""
AmaidesuDecider 测试

覆盖：
- MessageBuffer 聚合/窗口/强制标志/渲染
- TimingGate 强制触发/采样率/退避
- AmaidesuDecider 强制发布 Intent / should_reply=false 不发布 / 采样跳过不调用 LLM /
  情绪非法降级 / silent 降级不发布
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import json

import pytest

from src.stages.decision.deciders.amaidesu.amaidesu_decider import AmaidesuDecider
from src.stages.decision.deciders.amaidesu.message_buffer import MessageBuffer
from src.stages.decision.deciders.amaidesu.timing_gate import TimingGate
from src.modules.events.names import CoreEvents
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.capabilities import (
    ParameterSpec,
    UnifiedActionEntry,
    UnifiedCapabilitiesView,
)


# ==================== 辅助 ====================


def make_message(
    text: str = "你好呀",
    data_type: str = "text",
    importance: float = 0.5,
    source: str = "bili_danmaku",
    nickname: str = "观众A",
) -> NormalizedMessage:
    return NormalizedMessage(
        text=text,
        source=source,
        data_type=data_type,
        importance=importance,
        timestamp_ms=1234567890000,
        user_nickname=nickname,
    )


def make_llm_response(*, success: bool = True, content: str = "", error: str = "") -> SimpleNamespace:
    return SimpleNamespace(success=success, content=content, error=error)


class _FakeCapabilitiesProvider:
    """测试用能力提供者，返回固定的统一能力视图。"""

    def __init__(self, view: UnifiedCapabilitiesView):
        self._view = view

    def get_all_capabilities(self) -> UnifiedCapabilitiesView:
        return self._view


def make_capabilities(*names: str) -> UnifiedCapabilitiesView:
    return UnifiedCapabilitiesView(
        actions=[
            UnifiedActionEntry(
                name=name,
                description=f"{name} 动作",
                parameters={"duration_ms": ParameterSpec(type="integer", minimum=100, maximum=10000, default=1500)},
            )
            for name in names
        ]
    )


def make_decider(
    config: dict,
    llm_response: SimpleNamespace | None = None,
    capabilities_provider=None,
) -> AmaidesuDecider:
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    llm_service = MagicMock()
    llm_service.chat = AsyncMock(return_value=llm_response or make_llm_response(success=True, content="{}"))

    prompt_service = MagicMock()
    prompt_service.render_safe = MagicMock(return_value="rendered-prompt")

    decider = AmaidesuDecider(
        config=config,
        event_bus=event_bus,
        llm_service=llm_service,
        prompt_service=prompt_service,
        config_service=None,
        context_service=None,
        capabilities_provider=capabilities_provider,
    )
    return decider


# ==================== MessageBuffer ====================


class TestMessageBuffer:
    def test_add_and_drain(self):
        buf = MessageBuffer()
        assert buf.is_empty
        buf.add(make_message("a"), arrival_ms=1000)
        buf.add(make_message("b"), arrival_ms=1100)
        assert buf.size == 2
        assert buf.first_arrival_ms == 1000

        drained = buf.drain()
        assert len(drained) == 2
        assert buf.is_empty
        assert buf.first_arrival_ms == 0
        assert buf.force is False

    def test_force_flag(self):
        buf = MessageBuffer()
        buf.add(make_message("a"), arrival_ms=1000, forced=False)
        assert buf.force is False
        buf.add(make_message("sc"), arrival_ms=1100, forced=True)
        assert buf.force is True
        buf.drain()
        assert buf.force is False

    def test_render_batch_text_prefixes(self):
        messages = [
            make_message("普通弹幕", nickname="小明"),
            make_message("感谢支持", data_type="super_chat", nickname="土豪"),
            make_message("上舰啦", data_type="guard", nickname="舰长"),
        ]
        text = MessageBuffer.render_batch_text(messages)
        assert "小明: 普通弹幕" in text
        assert "[醒目留言] 土豪: 感谢支持" in text
        assert "[上舰] 舰长: 上舰啦" in text


# ==================== TimingGate ====================


class TestTimingGate:
    def _gate(self, **overrides) -> TimingGate:
        kwargs = dict(
            participation_rate=0.5,
            force_data_types=["super_chat", "guard"],
            force_importance=0.8,
            backoff_base_ms=1000,
            backoff_cap_ms=10000,
        )
        kwargs.update(overrides)
        return TimingGate(**kwargs)

    def test_is_forced_by_data_type(self):
        gate = self._gate()
        assert gate.is_forced(make_message(data_type="super_chat")) is True
        assert gate.is_forced(make_message(data_type="guard")) is True
        assert gate.is_forced(make_message(data_type="text")) is False

    def test_is_forced_by_importance(self):
        gate = self._gate()
        assert gate.is_forced(make_message(importance=0.9)) is True
        assert gate.is_forced(make_message(importance=0.5)) is False

    def test_forced_bypasses_sampling(self):
        gate = self._gate(participation_rate=0.0)
        act, reason = gate.should_act(forced=True)
        assert act is True
        assert reason == "forced"

    def test_rate_zero_skips_when_not_forced(self):
        gate = self._gate(participation_rate=0.0)
        act, reason = gate.should_act(forced=False)
        assert act is False
        assert reason == "rate_zero"

    def test_sampling_threshold(self):
        # rate=0.5 -> 每 2 批触发一次
        gate = self._gate(participation_rate=0.5)
        act1, _ = gate.should_act(forced=False)
        act2, reason2 = gate.should_act(forced=False)
        assert act1 is False
        assert act2 is True
        assert reason2 == "sampled"

    def test_backoff_after_no_action(self):
        gate = self._gate(participation_rate=1.0, backoff_base_ms=999999)
        # 一次 no_action 进入退避
        gate.record_result(replied=False)
        act, reason = gate.should_act(forced=False)
        assert act is False
        assert reason == "backoff"
        # 强制触发仍可绕过退避
        act_forced, _ = gate.should_act(forced=True)
        assert act_forced is True

    def test_reply_resets_backoff(self):
        gate = self._gate(participation_rate=1.0, backoff_base_ms=999999)
        gate.record_result(replied=False)
        assert gate.no_action_count == 1
        gate.record_result(replied=True)
        assert gate.no_action_count == 0
        act, _ = gate.should_act(forced=False)
        assert act is True


# ==================== AmaidesuDecider ====================


class TestAmaidesuDecider:
    @pytest.mark.asyncio
    async def test_forced_message_publishes_intent(self):
        content = json.dumps({"should_reply": True, "text": "谢谢老板的SC！", "emotion": "excited", "action": "挥手"})
        decider = make_decider(
            config={"type": "amaidesu", "participation_rate": 0.0, "batch_window_ms": 0},
            llm_response=make_llm_response(success=True, content=content),
        )
        await decider.decide(make_message("感谢支持", data_type="super_chat"))
        await decider._maybe_flush()

        decider._llm_service.chat.assert_awaited_once()
        decider._event_bus.emit.assert_awaited_once()
        args, kwargs = decider._event_bus.emit.call_args
        assert args[0] == CoreEvents.DECISION_INTENT_GENERATED

    @pytest.mark.asyncio
    async def test_should_reply_false_does_not_publish(self):
        content = json.dumps({"should_reply": False, "text": "", "emotion": "neutral", "action": ""})
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_response=make_llm_response(success=True, content=content),
        )
        await decider.decide(make_message("刷屏内容"))
        await decider._maybe_flush()

        decider._llm_service.chat.assert_awaited_once()
        decider._event_bus.emit.assert_not_awaited()
        assert decider._total_no_action == 1

    @pytest.mark.asyncio
    async def test_sampling_skip_does_not_call_llm(self):
        decider = make_decider(
            config={"type": "amaidesu", "participation_rate": 0.0, "batch_window_ms": 0},
        )
        # 普通文本，非强制；rate=0 应被门控跳过
        await decider.decide(make_message("普通弹幕"))
        await decider._maybe_flush()

        decider._llm_service.chat.assert_not_awaited()
        decider._event_bus.emit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_emotion_degrades_to_neutral(self):
        content = json.dumps({"should_reply": True, "text": "嗨", "emotion": "不存在的情绪", "action": ""})
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_response=make_llm_response(success=True, content=content),
        )
        await decider.decide(make_message("你好"))
        await decider._maybe_flush()

        decider._event_bus.emit.assert_awaited_once()
        payload = decider._event_bus.emit.call_args[0][1]
        intent = payload.to_intent()
        assert intent.emotion is not None
        assert intent.emotion.name == "neutral"

    @pytest.mark.asyncio
    async def test_silent_fallback_on_llm_failure(self):
        decider = make_decider(
            config={
                "type": "amaidesu",
                "force_data_types": ["text"],
                "batch_window_ms": 0,
                "fallback_mode": "silent",
            },
            llm_response=make_llm_response(success=False, error="boom"),
        )
        await decider.decide(make_message("你好"))
        await decider._maybe_flush()

        decider._event_bus.emit.assert_not_awaited()
        assert decider._failed_requests == 1

    @pytest.mark.asyncio
    async def test_action_selection_valid_action(self):
        content = json.dumps(
            {
                "should_reply": True,
                "text": "好嘞，挥个手~",
                "emotion": "happy",
                "action": "warudo.wave",
                "action_parameters": {"duration_ms": 2000},
            }
        )
        provider = _FakeCapabilitiesProvider(make_capabilities("warudo.wave", "warudo.nod"))
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_response=make_llm_response(success=True, content=content),
            capabilities_provider=provider,
        )
        await decider.decide(make_message("挥个手"))
        await decider._maybe_flush()

        decider._event_bus.emit.assert_awaited_once()
        intent = decider._event_bus.emit.call_args[0][1].to_intent()
        assert intent.action is not None
        assert intent.action.name == "warudo.wave"
        assert intent.action.parameters == {"duration_ms": 2000}

        # action_list 被渲染进 prompt
        render_kwargs = decider._prompt_service.render_safe.call_args.kwargs
        assert "warudo.wave" in render_kwargs["action_list"]

    @pytest.mark.asyncio
    async def test_action_selection_invalid_action_dropped(self):
        content = json.dumps(
            {
                "should_reply": True,
                "text": "在的在的",
                "emotion": "neutral",
                "action": "warudo.unknown",
                "action_parameters": {},
            }
        )
        provider = _FakeCapabilitiesProvider(make_capabilities("warudo.wave"))
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_response=make_llm_response(success=True, content=content),
            capabilities_provider=provider,
        )
        await decider.decide(make_message("你好"))
        await decider._maybe_flush()

        decider._event_bus.emit.assert_awaited_once()
        intent = decider._event_bus.emit.call_args[0][1].to_intent()
        # 非法动作被丢弃，但发言仍发布
        assert intent.action is None
        assert intent.speech == "在的在的"

    @pytest.mark.asyncio
    async def test_window_not_due_keeps_buffer(self):
        decider = make_decider(
            config={"type": "amaidesu", "batch_window_ms": 999999, "batch_max_size": 99},
        )
        await decider.decide(make_message("普通弹幕"))
        await decider._maybe_flush()

        # 窗口未到且未达条数上限，缓冲应保留，不调用 LLM
        decider._llm_service.chat.assert_not_awaited()
        assert decider._buffer.size == 1
