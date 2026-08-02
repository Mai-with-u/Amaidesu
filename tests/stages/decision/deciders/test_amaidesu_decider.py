"""
AmaidesuDecider 测试（双阶段：Planner + Replyer）

覆盖：
- MessageBuffer 聚合/窗口/强制标志/渲染
- MessageBuffer.should_flush() 空窗补偿判定
- TimingGate 强制触发判定（Task 11 已移除采样/退避，仅保留 is_forced）
- AmaidesuDecider 两阶段编排：
  - test_two_stage_flow_forced：强制 → Planner 1 次 + Replyer 1 次 → Intent 发布（共 2 次 LLM）
  - test_two_stage_flow_no_action：Planner should_reply=False → Replyer 不调用 → 不发布
  - test_planner_failure_silent：Planner 异常 → 不发布，planner_failures+1
  - test_replyer_failure_silent：Replyer 异常 → 不发布，replyer_failures+1
  - test_event_bus_no_new_events：运行期间仅 emit decision.intent.generated
- 强制/情绪降级/动作选择等既有路径回归
"""

# 先导入 config.schemas 种子，规避 deciders/__init__ 的预存在循环导入：
#   deciders/__init__ → llm → llm_decider → schemas → decision_schemas → llm_decider(未完成)
# 先让 schemas 入口完成 llm_decider 的完整初始化，再进入 deciders 包即不再死锁。
import src.modules.config.schemas  # noqa: F401  # isort:skip

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
    """构造模拟的 LLMResponse（鸭子类型：含 .success / .content / .error）。"""
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
    llm_responses: list | None = None,
    capabilities_provider=None,
) -> AmaidesuDecider:
    """构造测试用 AmaidesuDecider。

    Args:
        config: 配置字典。
        llm_response: 单次 LLM 返回（用于 chat.return_value）。
        llm_responses: 多次 LLM 返回（用于 chat.side_effect，按调用顺序消费）。
            与 llm_response 互斥；同时给出时 llm_responses 优先。
        capabilities_provider: 可选能力提供者。
    """
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    llm_service = MagicMock()
    if llm_responses is not None:
        llm_service.chat = AsyncMock(side_effect=llm_responses)
    else:
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


def _planner_json(*, should_reply: bool = True, target: str = "用户", confidence: float = 0.8) -> str:
    """构造 Planner 风格的 JSON 响应（DecisionPlan 5 字段）。"""
    return json.dumps(
        {
            "should_reply": should_reply,
            "target": target,
            "topic_summary": "",
            "reply_guidance": "",
            "confidence": confidence,
        }
    )


def _replyer_json(*, text: str = "好的呀", emotion: str = "happy", action: str = "") -> str:
    """构造 Replyer 风格的 JSON 响应（Intent 4 字段）。"""
    return json.dumps({"text": text, "emotion": emotion, "action": action, "action_parameters": {}})


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

    def test_last_arrival_tracking(self):
        buf = MessageBuffer()
        buf.add(make_message("a"), arrival_ms=1000)
        buf.add(make_message("b"), arrival_ms=1500)
        buf.add(make_message("c"), arrival_ms=3000)
        assert buf.first_arrival_ms == 1000
        assert buf.last_arrival_ms == 3000
        buf.drain()
        assert buf.last_arrival_ms == 0

    def test_should_flush_forced(self):
        buf = MessageBuffer(batch_window_ms=999999)
        buf.add(make_message("sc", data_type="super_chat"), arrival_ms=1000, forced=True)
        flush, reason = buf.should_flush(now_ms=1100)
        assert flush is True
        assert reason == "forced"

    def test_should_flush_batch_full(self):
        buf = MessageBuffer(batch_window_ms=999999, batch_max_size=3)
        buf.add(make_message("a"), arrival_ms=1000)
        buf.add(make_message("b"), arrival_ms=1100)
        buf.add(make_message("c"), arrival_ms=1200)
        flush, reason = buf.should_flush(now_ms=1300)
        assert flush is True
        assert reason == "batch_full"

    def test_should_flush_window_not_expired(self):
        buf = MessageBuffer(batch_window_ms=5000, batch_max_size=99)
        buf.add(make_message("a"), arrival_ms=1000)
        flush, reason = buf.should_flush(now_ms=2000)
        assert flush is False
        assert reason == "window_not_expired"

    def test_should_flush_window_expired_no_idle_compensation(self):
        buf = MessageBuffer(batch_window_ms=1000, batch_max_size=99, enable_idle_compensation=False)
        buf.add(make_message("a"), arrival_ms=1000)
        flush, reason = buf.should_flush(now_ms=5000)
        assert flush is True
        assert reason == "window_expired"

    def test_should_flush_window_expired_no_avg_interval(self):
        buf = MessageBuffer(batch_window_ms=1000, batch_max_size=99, enable_idle_compensation=True)
        buf.add(make_message("a"), arrival_ms=1000)
        flush, reason = buf.should_flush(now_ms=5000, avg_interval_ms=None)
        assert flush is True
        assert reason == "window_expired"

    def test_should_flush_idle_compensation_triggers(self):
        buf = MessageBuffer(batch_window_ms=1000, batch_max_size=20, enable_idle_compensation=True)
        buf.add(make_message("a"), arrival_ms=1000)
        buf.add(make_message("b"), arrival_ms=2000)
        avg_interval_ms = 1000.0
        now = 1000 + 1000 + int(18.5 * avg_interval_ms)
        flush, reason = buf.should_flush(now, avg_interval_ms=avg_interval_ms)
        assert flush is True
        assert reason == "idle_compensation"

    def test_should_flush_idle_compensation_capped_at_threshold_minus_1(self):
        buf = MessageBuffer(batch_window_ms=100, batch_max_size=5, enable_idle_compensation=True)
        buf.add(make_message("a"), arrival_ms=1000)
        avg_interval_ms = 100.0
        now = 1000 + 100 + 99999999
        flush, reason = buf.should_flush(now, avg_interval_ms=avg_interval_ms)
        assert flush is True
        assert reason == "idle_compensation"

    def test_should_flush_idle_compensation_waits_when_equivalent_below_threshold(self):
        buf = MessageBuffer(batch_window_ms=1000, batch_max_size=20, enable_idle_compensation=True)
        buf.add(make_message("a"), arrival_ms=1000)
        buf.add(make_message("b"), arrival_ms=2000)
        avg_interval_ms = 2000.0
        now = 2000 + 5000
        flush, reason = buf.should_flush(now, avg_interval_ms=avg_interval_ms)
        assert flush is False
        assert "waiting_idle" in reason

    def test_should_flush_idle_compensation_never_triggers_on_empty_buffer(self):
        buf = MessageBuffer(batch_window_ms=1000, batch_max_size=5, enable_idle_compensation=True)
        flush, reason = buf.should_flush(now_ms=999999, avg_interval_ms=100.0)
        assert flush is False
        assert reason == "window_not_expired"

    def test_should_flush_idle_compensation_requires_at_least_one_real_message(self):
        buf = MessageBuffer(batch_window_ms=100, batch_max_size=20, enable_idle_compensation=True)
        buf.add(make_message("a"), arrival_ms=1000)
        avg_interval_ms = 100.0
        now = 1000 + 100 + int(100 * avg_interval_ms)
        flush, reason = buf.should_flush(now, avg_interval_ms=avg_interval_ms)
        assert flush is True
        assert reason == "idle_compensation"


# ==================== TimingGate（Task 11 精简：仅强制触发判定） ====================


class TestTimingGate:
    def _gate(self, **overrides) -> TimingGate:
        kwargs = dict(
            force_data_types=["super_chat", "guard"],
            force_importance=0.8,
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

    def test_should_act_always_proceeds(self):
        """Task 11: 采样/退避已移除，should_act 恒通过（Planner 决定 should_reply）。"""
        gate = self._gate()
        act_forced, reason_forced = gate.should_act(forced=True)
        assert act_forced is True
        assert reason_forced == "forced"
        act_open, reason_open = gate.should_act(forced=False)
        assert act_open is True
        assert reason_open == "proceed"

    def test_record_result_is_noop(self):
        """Task 11: record_result 退化为 no-op（保留签名用于 API 稳定性）。"""
        gate = self._gate()
        # 任意次数调用都不应抛异常或改变状态
        gate.record_result(replied=False)
        gate.record_result(replied=False)
        gate.record_result(replied=True)
        # 无内部状态可断言，仅验证调用不抛异常


# ==================== AmaidesuDecider（双阶段编排） ====================


class TestAmaidesuDecider:
    # ---------- 两阶段编排：5 个核心测试 ----------

    @pytest.mark.asyncio
    async def test_two_stage_flow_forced(self):
        """强制批次 → Planner 1 次 + Replyer 1 次 → Intent 发布（共 2 次 LLM）。"""
        decider = make_decider(
            config={"type": "amaidesu", "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True, target="舰长")),
                make_llm_response(success=True, content=_replyer_json(text="谢谢老板的SC！", emotion="excited")),
            ],
        )
        await decider.decide(make_message("感谢支持", data_type="super_chat"))
        await decider._maybe_flush()

        # 共 2 次 LLM 调用（Planner 用 llm_fast，Replyer 用 llm）
        assert decider._llm_service.chat.await_count == 2
        planner_call = decider._llm_service.chat.await_args_list[0]
        replyer_call = decider._llm_service.chat.await_args_list[1]
        assert planner_call.kwargs["client_type"] == "llm_fast"
        assert replyer_call.kwargs["client_type"] == "llm"

        # 发布 1 次 decision.intent.generated
        decider._event_bus.emit.assert_awaited_once()
        args, _ = decider._event_bus.emit.call_args
        assert args[0] == CoreEvents.DECISION_INTENT_GENERATED

    @pytest.mark.asyncio
    async def test_two_stage_flow_no_action(self):
        """Planner 返回 should_reply=False → Replyer 不调用 → 不发布 Intent。"""
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=False, confidence=0.2)),
            ],
        )
        await decider.decide(make_message("刷屏内容"))
        await decider._maybe_flush()

        # 只调用 Planner 1 次，Replyer 未被调用
        assert decider._llm_service.chat.await_count == 1
        decider._event_bus.emit.assert_not_awaited()
        assert decider._total_no_action == 1
        assert decider._total_replies == 0

    @pytest.mark.asyncio
    async def test_planner_failure_silent(self):
        """Planner 异常（success=False / 返回 None）→ 不发布 Intent，planner_failures+1。"""
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_responses=[make_llm_response(success=False, error="planner boom")],
        )
        await decider.decide(make_message("你好"))
        await decider._maybe_flush()

        # Planner 调用 1 次（失败）→ Replyer 不调用
        assert decider._llm_service.chat.await_count == 1
        decider._event_bus.emit.assert_not_awaited()
        assert decider._planner_failures == 1
        assert decider._replyer_failures == 0
        assert decider._total_replies == 0

    @pytest.mark.asyncio
    async def test_replyer_failure_silent(self):
        """Replyer 异常（success=False / 返回 None）→ 不发布 Intent，replyer_failures+1。"""
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(success=False, error="replyer boom"),
            ],
        )
        await decider.decide(make_message("你好"))
        await decider._maybe_flush()

        # Planner 1 次（成功）+ Replyer 1 次（失败）
        assert decider._llm_service.chat.await_count == 2
        decider._event_bus.emit.assert_not_awaited()
        assert decider._planner_failures == 0
        assert decider._replyer_failures == 1
        assert decider._total_replies == 0

    @pytest.mark.asyncio
    async def test_event_bus_no_new_events(self):
        """Guardrail：运行期间仅 emit decision.intent.generated，不引入新事件。"""
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(success=True, content=_replyer_json(text="你好呀~", emotion="happy")),
            ],
        )
        await decider.decide(make_message("你好"))
        await decider._maybe_flush()

        # 所有 emit 调用的第一个参数都必须是 DECISION_INTENT_GENERATED（guardrail）
        for call in decider._event_bus.emit.await_args_list:
            args, _ = call
            assert args[0] == CoreEvents.DECISION_INTENT_GENERATED, (
                f"发现未授权的新事件: {args[0]}（仅允许 decision.intent.generated）"
            )
        assert decider._event_bus.emit.await_count == 1

    # ---------- 既有路径回归（已适配两阶段 mock） ----------

    @pytest.mark.asyncio
    async def test_forced_message_publishes_intent(self):
        """SC 强制 → 两阶段决策 → 发布 Intent。"""
        decider = make_decider(
            config={"type": "amaidesu", "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True, target="舰长")),
                make_llm_response(
                    success=True,
                    content=_replyer_json(text="谢谢老板的SC！", emotion="excited"),
                ),
            ],
        )
        await decider.decide(make_message("感谢支持", data_type="super_chat"))
        await decider._maybe_flush()

        # 两阶段共 2 次 LLM 调用
        assert decider._llm_service.chat.await_count == 2
        decider._event_bus.emit.assert_awaited_once()
        args, _ = decider._event_bus.emit.call_args
        assert args[0] == CoreEvents.DECISION_INTENT_GENERATED

    @pytest.mark.asyncio
    async def test_should_reply_false_does_not_publish(self):
        """Planner should_reply=False → 不发布 Intent。"""
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=False)),
            ],
        )
        await decider.decide(make_message("刷屏内容"))
        await decider._maybe_flush()

        # 只调 Planner 1 次（should_reply=False），Replyer 未被调用
        assert decider._llm_service.chat.await_count == 1
        decider._event_bus.emit.assert_not_awaited()
        assert decider._total_no_action == 1

    @pytest.mark.asyncio
    async def test_invalid_emotion_degrades_to_neutral(self):
        """Replyer 返回非法 emotion → Intent 情绪降级 neutral。"""
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(
                    success=True,
                    content=_replyer_json(text="嗨", emotion="不存在的情绪"),
                ),
            ],
        )
        await decider.decide(make_message("你好"))
        await decider._maybe_flush()

        decider._event_bus.emit.assert_awaited_once()
        payload = decider._event_bus.emit.call_args[0][1]
        intent = payload.to_intent()
        assert intent.emotion is not None
        assert intent.emotion.name == "neutral"

    @pytest.mark.asyncio
    async def test_silent_fallback_on_planner_failure(self):
        """Planner 调用失败 → silent 降级，planner_failures+1（原 test_silent_fallback_on_llm_failure）。"""
        decider = make_decider(
            config={
                "type": "amaidesu",
                "force_data_types": ["text"],
                "batch_window_ms": 0,
                "fallback_mode": "silent",
            },
            llm_responses=[make_llm_response(success=False, error="boom")],
        )
        await decider.decide(make_message("你好"))
        await decider._maybe_flush()

        decider._event_bus.emit.assert_not_awaited()
        assert decider._planner_failures == 1

    @pytest.mark.asyncio
    async def test_action_selection_valid_action(self):
        """Replyer 选有效动作 → Intent.action 携带；action_list 注入 Replyer prompt。"""
        provider = _FakeCapabilitiesProvider(make_capabilities("warudo.wave", "warudo.nod"))
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(
                    success=True,
                    content=json.dumps(
                        {
                            "text": "好嘞，挥个手~",
                            "emotion": "happy",
                            "action": "warudo.wave",
                            "action_parameters": {"duration_ms": 2000},
                        }
                    ),
                ),
            ],
            capabilities_provider=provider,
        )
        await decider.decide(make_message("挥个手"))
        await decider._maybe_flush()

        decider._event_bus.emit.assert_awaited_once()
        intent = decider._event_bus.emit.call_args[0][1].to_intent()
        assert intent.action is not None
        assert intent.action.name == "warudo.wave"
        assert intent.action.parameters == {"duration_ms": 2000}

        # action_list 注入到了某次 render_safe 调用（Planner 或 Replyer）
        all_kwargs = [c.kwargs for c in decider._prompt_service.render_safe.call_args_list]
        assert any("warudo.wave" in (kw.get("action_list") or "") for kw in all_kwargs), (
            "action_list 应至少注入一次（Planner 或 Replyer prompt）"
        )

    @pytest.mark.asyncio
    async def test_action_selection_invalid_action_dropped(self):
        """Replyer 选非法动作 → action 丢弃，speech 保留并发布。"""
        provider = _FakeCapabilitiesProvider(make_capabilities("warudo.wave"))
        decider = make_decider(
            config={"type": "amaidesu", "force_data_types": ["text"], "batch_window_ms": 0},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(
                    success=True,
                    content=json.dumps(
                        {
                            "text": "在的在的",
                            "emotion": "neutral",
                            "action": "warudo.unknown",
                            "action_parameters": {},
                        }
                    ),
                ),
            ],
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
        """窗口未到期且未达条数上限 → 缓冲保留，不调用 LLM。"""
        decider = make_decider(
            config={"type": "amaidesu", "batch_window_ms": 999999, "batch_max_size": 99},
        )
        await decider.decide(make_message("普通弹幕"))
        await decider._maybe_flush()

        decider._llm_service.chat.assert_not_awaited()
        assert decider._buffer.size == 1
