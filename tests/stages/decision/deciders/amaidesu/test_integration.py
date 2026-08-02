"""AmaidesuDecider 端到端集成测试（Task 12）

验证完整链路：弹幕 → MessageBuffer → Planner → Replyer → Intent → EventBus
（mock OutputHandlerManager 消费）。

与 test_amaidesu_decider.py 的区别：
- 本文件聚焦 **端到端链路**（Full Decider + 真实 EventBus + 模拟下游消费），
  而非单元级的双阶段 mock 断言。
- 使用真实 EventBus 验证 IntentPayload 序列化/反序列化无损往返。
- test_empty_history_startup 验证 ContextService 空历史启动的集成路径。
- test_room_state_injected_into_planner 验证态势缓存真实注入 Planner prompt。

覆盖（≥4 个用例）：
1. test_end_to_end_forced_sc — SC 强制 → 全链路 → EventBus 投递 IntentPayload
2. test_end_to_end_forced_gift — 礼物强制 → 全链路 → EventBus 投递 IntentPayload（forced 标志生效）
3. test_end_to_end_normal_danmaku — 普通弹幕批次 → Planner 判断 → Intent / no_action
4. test_empty_history_startup — ContextService 空历史启动，首条弹幕正常处理 + 上下文落盘
5. test_room_state_injected_into_planner — 真实 RoomState 注入 Planner prompt 断言
"""

# 预导入 config.schemas 种子，规避 deciders/__init__ 的预存在循环导入：
#   deciders/__init__ → llm → llm_decider → schemas → decision_schemas → llm_decider(未完成)
# 与 test_planner.py / test_replyer.py / test_amaidesu_decider.py 同样的 workaround。
import src.modules.config.schemas  # noqa: F401  # isort:skip

import asyncio
import json
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.context import ContextService, MessageRole
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.time_utils import now_ms
from src.modules.types import Intent
from src.modules.types.base.normalized_message import NormalizedMessage
from src.stages.decision.deciders.amaidesu.amaidesu_decider import AmaidesuDecider
from src.stages.decision.deciders.amaidesu.room_state import RoomState


# ==================== EventBus 后台任务排空 ====================


async def _drain_event_bus(bus: EventBus, timeout_s: float = 1.0) -> None:
    """等待真实 EventBus 的后台 handler 任务完成。

    EventBus.emit 默认 wait=False，handler 在后台 asyncio.Task 中执行。
    测试断言前需让出事件循环，使后台任务有机会跑完。
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while bus._background_tasks and loop.time() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)


# ==================== 辅助工厂 ====================


def make_message(
    text: str = "你好呀",
    data_type: str = "text",
    importance: float = 0.5,
    source: str = "bili_danmaku",
    nickname: str = "观众A",
) -> NormalizedMessage:
    """构造标准化弹幕消息。"""
    return NormalizedMessage(
        text=text,
        source=source,
        data_type=data_type,
        importance=importance,
        timestamp_ms=now_ms(),
        user_nickname=nickname,
    )


def make_llm_response(*, success: bool = True, content: str = "", error: str = "") -> SimpleNamespace:
    """构造模拟的 LLMResponse（鸭子类型：含 .success / .content / .error）。"""
    return SimpleNamespace(success=success, content=content, error=error)


def _planner_json(*, should_reply: bool = True, target: str = "用户", confidence: float = 0.8) -> str:
    """构造 Planner 风格的 JSON 响应（DecisionPlan 5 字段）。"""
    return json.dumps(
        {
            "should_reply": should_reply,
            "target": target,
            "topic_summary": "",
            "reply_guidance": "",
            "confidence": confidence,
        },
        ensure_ascii=False,
    )


def _replyer_json(*, text: str = "好的呀", emotion: str = "happy", action: str = "") -> str:
    """构造 Replyer 风格的 JSON 响应（Intent 4 字段）。"""
    return json.dumps(
        {"text": text, "emotion": emotion, "action": action, "action_parameters": {}},
        ensure_ascii=False,
    )


def _make_mock_llm_service(responses: List[SimpleNamespace]):
    """构造 mock LLMService，按顺序消费 responses（side_effect 模式）。"""
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=responses)
    return llm


def _make_mock_prompt_service():
    """构造 mock PromptService，render_safe 返回固定字符串。"""
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="rendered-prompt")
    return prompt


def build_decider(
    *,
    config: Optional[dict] = None,
    event_bus: Any = None,
    llm_responses: List[SimpleNamespace],
    prompt_service: Any = None,
    config_service: Any = None,
    context_service: Any = None,
    capabilities_provider: Any = None,
    room_state: Optional[RoomState] = None,
) -> AmaidesuDecider:
    """组装端到端测试用的 AmaidesuDecider。

    Args:
        config: 配置字典（默认 batch_window_ms=0 立即触发 flush）。
        event_bus: 事件总线（None 时用真实 EventBus）。
        llm_responses: LLM 响应序列（按 Planner→Replyer 顺序消费）。
        prompt_service: 提示词服务（None 时用 mock）。
        config_service: 配置服务（可选）。
        context_service: 上下文服务（可选）。
        capabilities_provider: 能力提供者（可选）。
        room_state: 房间态势（None 时由 decider 内部新建）。
    """
    cfg = {"type": "amaidesu", "batch_window_ms": 0}
    if config:
        cfg.update(config)

    eb = event_bus if event_bus is not None else EventBus()
    llm = _make_mock_llm_service(llm_responses)
    ps = prompt_service if prompt_service is not None else _make_mock_prompt_service()

    return AmaidesuDecider(
        config=cfg,
        event_bus=eb,
        llm_service=llm,
        prompt_service=ps,
        config_service=config_service,
        context_service=context_service,
        capabilities_provider=capabilities_provider,
        room_state=room_state,
    )


# ==================== 测试 1：SC 强制端到端全链路 ====================


class TestEndToEndForcedSC:
    """SC 消息 → MessageBuffer → Planner → Replyer → Intent → EventBus（mock 消费）。"""

    @pytest.mark.asyncio
    async def test_end_to_end_forced_sc(self) -> None:
        """SC 消息经完整双阶段链路后，EventBus 投递的 IntentPayload 可被下游无损消费。

        链路：decide(SC) → _maybe_flush → planner.plan → replyer.generate
              → event_bus.emit(DECISION_INTENT_GENERATED, IntentPayload)

        断言：
        - LLM 被调用恰好 2 次（Planner 用 llm_fast，Replyer 用 llm）
        - 真实 EventBus 的订阅者（模拟 OutputHandlerManager）收到 IntentPayload
        - IntentPayload.to_intent() 还原的 Intent 含正确的 speech / emotion
        - IntentPayload.name == "amaidesu"
        """
        # 使用真实 EventBus，注册模拟下游消费者（模拟 OutputHandlerManager._on_decision_intent）
        real_bus = EventBus()
        received_payloads: List[IntentPayload] = []
        received_sources: List[str] = []

        async def mock_output_consumer(event_name: str, payload: IntentPayload, source: str) -> None:
            """模拟 OutputHandlerManager._on_decision_intent 的消费行为。"""
            assert event_name == CoreEvents.DECISION_INTENT_GENERATED
            received_payloads.append(payload)
            received_sources.append(source)

        real_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            mock_output_consumer,
            model_class=IntentPayload,
            priority=50,
        )

        decider = build_decider(
            event_bus=real_bus,
            llm_responses=[
                make_llm_response(
                    success=True,
                    content=_planner_json(should_reply=True, target="土豪酱", confidence=0.95),
                ),
                make_llm_response(
                    success=True,
                    content=_replyer_json(text="谢谢土豪酱的SC！超级感动！", emotion="excited"),
                ),
            ],
        )

        # 注入 SC 消息并触发决策
        sc_msg = make_message(
            text="主播太棒了！给你个SC！",
            data_type="super_chat",
            importance=0.95,
            nickname="土豪酱",
        )
        await decider.decide(sc_msg)
        await decider._maybe_flush()
        await _drain_event_bus(real_bus)

        # ① LLM 调用恰好 2 次（Planner + Replyer）
        assert decider._llm_service.chat.await_count == 2
        planner_call = decider._llm_service.chat.await_args_list[0]
        replyer_call = decider._llm_service.chat.await_args_list[1]
        assert planner_call.kwargs["client_type"] == "llm_fast"
        assert replyer_call.kwargs["client_type"] == "llm"

        # ② 模拟下游消费者收到 1 个 IntentPayload
        assert len(received_payloads) == 1, f"下游应收到 1 个 IntentPayload，实际: {len(received_payloads)}"
        assert received_sources == ["AmaidesuDecider"]

        payload = received_payloads[0]
        assert isinstance(payload, IntentPayload)
        assert payload.name == "amaidesu"

        # ③ IntentPayload → Intent 无损还原（验证序列化往返）
        intent = payload.to_intent()
        assert isinstance(intent, Intent)
        assert intent.speech == "谢谢土豪酱的SC！超级感动！"
        assert intent.emotion is not None
        assert intent.emotion.name == "excited"

        # ④ 统计计数正确
        stats = decider.get_statistics()
        assert stats["total_messages"] == 1
        assert stats["total_batches"] == 1
        assert stats["total_replies"] == 1
        assert stats["planner_failures"] == 0
        assert stats["replyer_failures"] == 0

        await real_bus.cleanup()


# ==================== 测试 2：礼物强制端到端全链路 ====================


class TestEndToEndForcedGift:
    """礼物消息 → MessageBuffer → Planner(forced=True) → Replyer → Intent → EventBus（mock 消费）。

    与 TestEndToEndForcedSC 对称，验证 force_data_types 含 "gift" 时礼物消息走强制路径：
    decide(gift) → timing_gate.is_forced=True → buffer.add(forced=True)
                 → _maybe_flush → planner.plan(forced=True) → replyer.generate
                 → event_bus.emit(DECISION_INTENT_GENERATED, IntentPayload)
    """

    @pytest.mark.asyncio
    async def test_end_to_end_forced_gift(self) -> None:
        """礼物消息经完整双阶段链路后，EventBus 投递的 IntentPayload 可被下游无损消费。

        断言：
        - LLM 被调用恰好 2 次（Planner 用 llm_fast，Replyer 用 llm）
        - 真实 EventBus 的订阅者收到 1 个 IntentPayload，且 name == "amaidesu"
        - Planner 的 render_safe 调用 kwargs 中 forced == "true"（强制标志生效）
        - 统计计数 total_replies == 1
        """
        real_bus = EventBus()
        received_payloads: List[IntentPayload] = []

        async def mock_output_consumer(event_name: str, payload: IntentPayload, source: str) -> None:
            assert event_name == CoreEvents.DECISION_INTENT_GENERATED
            received_payloads.append(payload)

        real_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            mock_output_consumer,
            model_class=IntentPayload,
            priority=50,
        )

        # 独立 mock prompt_service，便于断言 Planner 的 forced kwarg
        prompt_service = _make_mock_prompt_service()

        decider = build_decider(
            event_bus=real_bus,
            prompt_service=prompt_service,
            llm_responses=[
                make_llm_response(
                    success=True,
                    content=_planner_json(should_reply=True, target="舰长酱", confidence=0.95),
                ),
                make_llm_response(
                    success=True,
                    content=_replyer_json(text="谢谢舰长酱的礼物！太开心啦！", emotion="excited"),
                ),
            ],
        )

        # 注入礼物消息（data_type="gift"，默认 force_data_types 已含 gift → 强制路径）
        gift_msg = make_message(
            text="送给主播一个小电视！",
            data_type="gift",
            importance=0.6,
            nickname="舰长酱",
        )
        await decider.decide(gift_msg)
        await decider._maybe_flush()
        await _drain_event_bus(real_bus)

        # ① LLM 调用恰好 2 次（Planner + Replyer）
        assert decider._llm_service.chat.await_count == 2
        planner_call = decider._llm_service.chat.await_args_list[0]
        replyer_call = decider._llm_service.chat.await_args_list[1]
        assert planner_call.kwargs["client_type"] == "llm_fast"
        assert replyer_call.kwargs["client_type"] == "llm"

        # ② Planner 的 render_safe 调用 kwargs 中 forced == "true"（强制标志生效）
        all_render_calls = prompt_service.render_safe.call_args_list
        planner_renders = [c for c in all_render_calls if "forced" in c.kwargs]
        assert len(planner_renders) >= 1, (
            f"Planner 的 render_safe 应包含 forced kwarg，实际所有调用: {[c.kwargs for c in all_render_calls]}"
        )
        assert planner_renders[0].kwargs["forced"] == "true", (
            f"礼物强制路径下 forced 应为 'true'，实际: {planner_renders[0].kwargs['forced']!r}"
        )

        # ③ 模拟下游消费者收到 1 个 IntentPayload
        assert len(received_payloads) == 1, f"下游应收到 1 个 IntentPayload，实际: {len(received_payloads)}"
        payload = received_payloads[0]
        assert isinstance(payload, IntentPayload)
        assert payload.name == "amaidesu"

        # ④ IntentPayload → Intent 无损还原
        intent = payload.to_intent()
        assert isinstance(intent, Intent)
        assert intent.speech == "谢谢舰长酱的礼物！太开心啦！"
        assert intent.emotion is not None
        assert intent.emotion.name == "excited"

        # ⑤ 统计计数：total_replies == 1
        stats = decider.get_statistics()
        assert stats["total_replies"] == 1

        await real_bus.cleanup()


# ==================== 测试 3：普通弹幕批次端到端 ====================


class TestEndToEndNormalDanmaku:
    """普通弹幕批次 → Planner 判断 → Intent 或 no_action。"""

    @pytest.mark.asyncio
    async def test_end_to_end_normal_danmaku(self) -> None:
        """普通弹幕批次经双阶段决策后产出 Intent。

        场景：多条普通弹幕（非 SC）→ Planner 判断 should_reply=True
              → Replyer 生成回复 → 发布 Intent。

        断言：
        - 普通弹幕（非强制类型）也能走完整链路并发布 Intent
        - Intent 内容由 Replyer 的 LLM 返回决定
        - event_bus.emit 被调用 1 次
        """
        # 使用真实 EventBus + mock 消费者
        real_bus = EventBus()
        received: List[IntentPayload] = []

        async def consumer(event_name: str, payload: IntentPayload, source: str) -> None:
            received.append(payload)

        real_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            consumer,
            model_class=IntentPayload,
        )

        decider = build_decider(
            event_bus=real_bus,
            llm_responses=[
                make_llm_response(
                    success=True,
                    content=_planner_json(should_reply=True, target="提问观众", confidence=0.75),
                ),
                make_llm_response(
                    success=True,
                    content=_replyer_json(text="这个问题我来解答~", emotion="happy"),
                ),
            ],
        )

        # 注入普通弹幕（非 SC / guard，importance 低于 force 阈值）
        await decider.decide(make_message("主播这个游戏怎么玩的？", nickname="提问观众"))
        await decider._maybe_flush()
        await _drain_event_bus(real_bus)

        # 普通弹幕也能走完整链路
        assert decider._llm_service.chat.await_count == 2
        assert len(received) == 1

        intent = received[0].to_intent()
        assert intent.speech == "这个问题我来解答~"
        assert intent.emotion is not None
        assert intent.emotion.name == "happy"

        await real_bus.cleanup()

    @pytest.mark.asyncio
    async def test_end_to_end_normal_danmaku_no_action(self) -> None:
        """Planner 判断普通弹幕不值得回应（should_reply=False）→ 不发布 Intent。

        场景：刷屏 / 无意义弹幕 → Planner should_reply=False
              → Replyer 不调用 → 不发布 Intent → no_action 计数 +1。
        """
        real_bus = EventBus()
        received: List[IntentPayload] = []

        async def consumer(event_name: str, payload: IntentPayload, source: str) -> None:
            received.append(payload)

        real_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            consumer,
            model_class=IntentPayload,
        )

        decider = build_decider(
            event_bus=real_bus,
            config={"force_data_types": ["text"]},  # 让 text 也进强制路径以便立即 flush
            llm_responses=[
                make_llm_response(
                    success=True,
                    content=_planner_json(should_reply=False, confidence=0.2),
                ),
            ],
        )

        await decider.decide(make_message("6666666666", nickname="刷屏怪"))
        await decider._maybe_flush()
        await _drain_event_bus(real_bus)

        # Planner 调用 1 次，should_reply=False → Replyer 不调用
        assert decider._llm_service.chat.await_count == 1
        assert len(received) == 0  # 下游未收到任何 Intent
        assert decider._total_no_action == 1
        assert decider._total_replies == 0

        await real_bus.cleanup()


# ==================== 测试 4：ContextService 空历史启动 ====================


class TestEmptyHistoryStartup:
    """ContextService 空历史启动，第一条弹幕正常处理。"""

    @pytest.mark.asyncio
    async def test_empty_history_startup(self) -> None:
        """空历史的 ContextService 启动后，首条弹幕能正常走完全链路并落盘上下文。

        场景：
        - 真实 ContextService（initialize() 后历史为空）
        - 第一条弹幕 → 双阶段决策 → Intent 发布
        - 上下文落盘：USER (弹幕批次) + ASSISTANT (回复) 各 1 条

        断言：
        - 启动前 get_history 返回空列表
        - 决策后 get_history 返回 2 条消息（USER + ASSISTANT）
        - Intent 正常发布
        """
        # 真实 ContextService
        ctx = ContextService()
        await ctx.initialize()

        # 确认空历史（decider 在消息无 session_id 时默认存入 "live" 会话）
        session_id = "live"
        history_before = await ctx.get_history(session_id)
        assert history_before == [], f"启动前历史应为空，实际: {history_before}"

        # 真实 EventBus + mock 消费者
        real_bus = EventBus()
        received: List[IntentPayload] = []

        async def consumer(event_name: str, payload: IntentPayload, source: str) -> None:
            received.append(payload)

        real_bus.on(CoreEvents.DECISION_INTENT_GENERATED, consumer, model_class=IntentPayload)

        decider = build_decider(
            event_bus=real_bus,
            context_service=ctx,
            config={"force_data_types": ["text"]},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True, target="新人")),
                make_llm_response(
                    success=True,
                    content=_replyer_json(text="欢迎新朋友来到直播间！", emotion="happy"),
                ),
            ],
        )

        # 第一条弹幕
        await decider.decide(make_message("主播好呀，第一次来", nickname="新人"))
        await decider._maybe_flush()
        await _drain_event_bus(real_bus)

        # Intent 正常发布
        assert len(received) == 1
        intent = received[0].to_intent()
        assert intent.speech == "欢迎新朋友来到直播间！"

        # 上下文落盘：USER + ASSISTANT 各 1 条
        history_after = await ctx.get_history(session_id)
        assert len(history_after) == 2, f"决策后历史应有 2 条，实际: {len(history_after)}"
        roles = [msg.role for msg in history_after]
        assert MessageRole.USER in roles
        assert MessageRole.ASSISTANT in roles

        # ASSISTANT 消息内容为回复文本
        assistant_msgs = [msg for msg in history_after if msg.role == MessageRole.ASSISTANT]
        assert len(assistant_msgs) == 1
        assert "欢迎新朋友" in assistant_msgs[0].content

        await real_bus.cleanup()
        await ctx.cleanup()


# ==================== 测试 5：RoomState 注入 Planner prompt ====================


class TestRoomStateInjectedIntoPlanner:
    """真实 RoomState 的态势快照注入 Planner prompt 断言。"""

    @pytest.mark.asyncio
    async def test_room_state_injected_into_planner(self) -> None:
        """真实 RoomState 的热度信号被注入到 Planner 的 prompt 渲染变量中。

        场景：
        - 构造真实 RoomState，预先 push 多条弹幕建立 high 热度
        - 注入到 decider，运行完整链路
        - 断言 Planner 的 render_safe 调用包含 room_state kwarg
        - room_state 文本含热度等级描述（"高热"/"正常"/"冷场"之一）

        验证点：RoomState → snapshot → _render_room_state → render_safe(room_state=...)
        """
        # 构造真实 RoomState，预推 10 条弹幕建立高热度（10 条/1秒 = 10 msg/s >> 0.5 阈值）
        room_state = RoomState()
        base_ts = now_ms()
        for i in range(10):
            room_state.update(
                make_message(f"加油加油{i}", nickname=f"粉丝{i}"),
                now_ms=base_ts + i * 100,  # 每 100ms 一条，1 秒内 10 条
            )

        # 独立 mock prompt_service，便于精确断言 render_safe 调用
        prompt_service = _make_mock_prompt_service()

        decider = build_decider(
            prompt_service=prompt_service,
            room_state=room_state,
            config={"force_data_types": ["text"]},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True, target="all")),
                make_llm_response(
                    success=True,
                    content=_replyer_json(text="谢谢大家的支持！", emotion="excited"),
                ),
            ],
        )

        # 注入一条新弹幕并触发决策（decide 内部也会 update room_state）
        await decider.decide(make_message("主播加油！", nickname="观众Z"))
        await decider._maybe_flush()

        # Planner 的 render_safe 调用应包含 room_state kwarg
        # 注意：Planner 和 Replyer 共享同一个 prompt_service mock，需找到 Planner 的调用
        all_calls = prompt_service.render_safe.call_args_list
        assert len(all_calls) >= 1, "至少应有一次 render_safe 调用（Planner）"

        # 找到含 room_state kwarg 的调用（Planner 专属）
        planner_calls = [c for c in all_calls if "room_state" in c.kwargs]
        assert len(planner_calls) >= 1, (
            f"Planner 的 render_safe 调用应包含 room_state kwarg，实际所有调用 kwargs: {[c.kwargs for c in all_calls]}"
        )

        room_state_text = planner_calls[0].kwargs["room_state"]
        assert isinstance(room_state_text, str)
        assert len(room_state_text) > 0, "room_state 文本不应为空"

        # 热度等级描述应出现在渲染文本中（3 选 1）
        # _render_room_state 映射：low→"冷场", medium→"正常节奏", high→"高热"
        heat_indicators = ["高热", "正常节奏", "冷场"]
        assert any(h in room_state_text for h in heat_indicators), (
            f"room_state 文本应含热度等级描述（{heat_indicators} 之一），实际: {room_state_text!r}"
        )

        # 由于预推了 10 条密集弹幕，热度应为 high（"高热"）
        # 注意：decide() 会再加 1 条，但热度仍为 high（11 条/约 1 秒）
        assert "高热" in room_state_text, (
            f"预推 10 条密集弹幕后热度应为 high（高热），实际 room_state: {room_state_text!r}"
        )
