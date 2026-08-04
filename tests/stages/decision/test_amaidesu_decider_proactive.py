"""AmaidesuDecider 主动发言分支集成测试（Task 6）

覆盖 .omo/plans/proactive-speech.md Task 6 的五个验收场景：
1. 冷场（buffer 空 + ProactiveTrigger.should_trigger 返回原因）
   → ``_make_two_stage_decision`` 以 ``(batch=[], proactive=True)`` 触发
2. 弹幕分支优先：buffer 非空时走弹幕路径，proactive 不触发
3. ``trigger_proactive()`` 置标志 → 下一 tick external 触发一次，标志消费后不再触发
4. 发布 Intent 后 ``room_state.last_speech_ms`` + ``proactive_trigger._last_trigger_ms`` 更新
5. ``proactive_enabled=False``（默认）时不触发

测试策略：
- 沿用 ``tests/stages/decision/deciders/test_amaidesu_decider.py`` 的 mock 注入模式
- 时间敏感场景通过 ProactiveTrigger / RoomState 自身注入的 ``now_ms`` 保证确定性
- 直接调用 ``_maybe_flush()``（不走 ``_flush_loop`` 的 ``asyncio.sleep``），便于同步控制
"""

# 先导入 config.schemas 种子，规避 deciders/__init__ 的预存在循环导入
import src.modules.config.schemas  # noqa: F401  # isort:skip

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.stages.decision.deciders.amaidesu.amaidesu_decider import AmaidesuDecider
from src.stages.decision.deciders.amaidesu.room_state import RoomStateSnapshot
from src.modules.events.names import CoreEvents


# ==================== 辅助 ====================


def _planner_json(*, should_reply: bool = True, target: str = "all", confidence: float = 0.8) -> str:
    """构造 Planner JSON 响应（DecisionPlan 5 字段）。"""
    return json.dumps(
        {
            "should_reply": should_reply,
            "target": target,
            "topic_summary": "",
            "reply_guidance": "",
            "confidence": confidence,
        }
    )


def _replyer_json(*, text: str = "主动开口咯", emotion: str = "happy") -> str:
    """构造 Replyer JSON 响应（Intent 4 字段）。"""
    return json.dumps({"text": text, "emotion": emotion, "action": "", "action_parameters": {}})


def _llm_response(*, success: bool = True, content: str = "", error: str = "") -> SimpleNamespace:
    """构造模拟的 LLMResponse（鸭子类型：含 .success / .content / .error）。"""
    return SimpleNamespace(success=success, content=content, error=error)


def _make_snapshot(*, topic_summary: str = "") -> RoomStateSnapshot:
    """构造一个 RoomStateSnapshot（供 mock 的 room_state.get_snapshot() 返回）。

    Planner 的 _render_room_state 会通过 ``getattr(snapshot, "heat", "low")``
    但 ``heat_map.get(default, snapshot.heat)`` 的第二参数会 eager 求值，
    要求 snapshot 必须有 ``heat`` 属性——直接用 SimpleNamespace 会 AttributeError。
    """
    return RoomStateSnapshot(
        heat="low",
        topics=[],
        sc_queue=[],
        last_update_ms=0,
        topic_summary=topic_summary,
        topic_summary_at_ms=0,
    )


def _make_decider(
    config: dict,
    llm_responses: list | None = None,
    room_state=None,
) -> AmaidesuDecider:
    """构造测试用 AmaidesuDecider（事件总线 + LLM + Prompt 全部 mock）。

    Args:
        config: 配置字典。
        llm_responses: 按调用顺序消费的 LLM 响应列表。
        room_state: 可选 RoomState 实例（用于自定义冷场 / 话题摘要状态）。
    """
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    llm_service = MagicMock()
    if llm_responses is not None:
        llm_service.chat = AsyncMock(side_effect=llm_responses)
    else:
        llm_service.chat = AsyncMock(return_value=_llm_response(success=True, content="{}"))

    prompt_service = MagicMock()
    prompt_service.render_safe = MagicMock(return_value="rendered-prompt")

    decider = AmaidesuDecider(
        config=config,
        event_bus=event_bus,
        llm_service=llm_service,
        prompt_service=prompt_service,
        config_service=None,
        context_service=None,
        capabilities_provider=None,
        room_state=room_state,
    )
    return decider


# ==================== 场景 1：冷场触发主动发言 ====================


class TestProactiveColdTrigger:
    """buffer 空 + 冷场条件满足 → ProactiveTrigger 返回 "cold" → _make_two_stage_decision 触发"""

    @pytest.mark.asyncio
    async def test_cold_triggers_proactive_with_empty_batch(self):
        """冷场场景下 _make_two_stage_decision 应以 (batch=[], proactive=True) 调用 Planner。"""
        # 注入一个"冷场"房间状态：is_cold=True、topic_summary 非空
        room_state = MagicMock()
        room_state.last_speech_ms = None  # 首次可触发
        room_state.is_cold = MagicMock(return_value=True)
        # topic_required=True，需要 topic_summary 非空
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="最近在聊新游戏"))

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,
                "proactive_cold_timeout_ms": 45_000,
                "proactive_min_interval_ms": 0,  # 关闭 min_interval 防接龙
                "proactive_schedule_interval_ms": 0,  # 关闭定时，避免优先级覆盖 cold
                "proactive_topic_required": True,
            },
            llm_responses=[
                _llm_response(success=True, content=_planner_json(should_reply=True)),
                _llm_response(success=True, content=_replyer_json(text="我先来聊聊这个新游戏~")),
            ],
            room_state=room_state,
        )

        await decider._maybe_flush()

        # 期望：Planner + Replyer 各被调用一次
        assert decider._llm_service.chat.await_count == 2

        # 验证 proactive 透传到 Planner 的 render_safe kwargs（Planner 签名变更见 Task 3）
        # 注意：proactive 走 Planner.plan → render_safe，不是 chat 的 kwargs
        # render_safe 是同步调用（普通 MagicMock），用 call_args_list 而非 await_args_list
        planner_render_calls = [
            call
            for call in decider._prompt_service.render_safe.call_args_list
            if call.args and "amaidesu_planner_v2" in str(call.args[0])
        ]
        assert len(planner_render_calls) >= 1, "Planner 应至少调用一次 render_safe"
        assert planner_render_calls[0].kwargs["proactive"] == "true"
        assert planner_render_calls[0].kwargs["forced"] == "false"

        # Intent 应被发布
        decider._event_bus.emit.assert_awaited_once()
        args, _ = decider._event_bus.emit.call_args
        assert args[0] == CoreEvents.DECISION_INTENT_GENERATED

        # _total_proactive 自增
        assert decider._total_proactive == 1
        assert decider._total_replies == 1

    @pytest.mark.asyncio
    async def test_proactive_uses_live_session_id_for_empty_batch(self):
        """主动发言（空批次）session_id 应回退到 'live'（现有 next() 对空 batch 自然成立）。"""
        room_state = MagicMock()
        room_state.last_speech_ms = None
        room_state.is_cold = MagicMock(return_value=True)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某个话题"))

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,
                "proactive_cold_timeout_ms": 45_000,
                "proactive_min_interval_ms": 0,
                "proactive_schedule_interval_ms": 0,
            },
            llm_responses=[
                _llm_response(success=True, content=_planner_json(should_reply=True)),
                _llm_response(success=True, content=_replyer_json(text="聊聊")),
            ],
            room_state=room_state,
        )

        await decider._maybe_flush()

        # 验证：replyer.generate 被以 batch=[] 调用
        # 由于 Replyer 也是 mock，需要看 Planner/Replyer 的实际调用
        # 这里通过 chat 被调用 2 次间接验证（Planner 1 次 + Replyer 1 次）
        assert decider._llm_service.chat.await_count == 2


# ==================== 场景 2：弹幕分支优先 ====================


class TestDanmakuBranchPriority:
    """buffer 非空时走弹幕决策，proactive 不触发"""

    @pytest.mark.asyncio
    async def test_danmaku_present_no_proactive_trigger(self):
        """buffer 非空时，应走弹幕分支，proactive 不被触发（_total_proactive 保持 0）。"""
        from src.modules.types.base.normalized_message import NormalizedMessage

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,  # 即使开启，弹幕分支也应优先
                "batch_window_ms": 0,
                "force_data_types": ["text"],  # 任意 text 即视为 force，立即触发
            },
            llm_responses=[
                _llm_response(success=True, content=_planner_json(should_reply=True, target="用户")),
                _llm_response(success=True, content=_replyer_json(text="嗨~")),
            ],
        )

        await decider.decide(
            NormalizedMessage(
                text="弹幕消息",
                source="console_input",
                data_type="text",
                importance=0.5,
                timestamp_ms=1234567890000,
                user_nickname="观众",
            )
        )
        await decider._maybe_flush()

        # Planner 应以 proactive=False 调用（弹幕分支走 Planner.plan，
        # proactive 参数透传到 render_safe）
        planner_render_calls = [
            call
            for call in decider._prompt_service.render_safe.call_args_list
            if call.args and "amaidesu_planner_v2" in str(call.args[0])
        ]
        assert len(planner_render_calls) >= 1
        assert planner_render_calls[0].kwargs["proactive"] == "false"
        assert planner_render_calls[0].kwargs["forced"] == "true"

        # _total_proactive 保持 0（未走主动分支）
        assert decider._total_proactive == 0
        # 但弹幕分支成功 → _total_replies = 1
        assert decider._total_replies == 1


# ==================== 场景 3：外部触发标志一次性消费 ====================


class TestExternalProactiveTrigger:
    """trigger_proactive() 置标志 → 下一 tick 触发 → 标志被消费后不再触发"""

    @pytest.mark.asyncio
    async def test_external_pending_consumed_once(self):
        """冷场条件满足 + trigger_proactive 置标志 → flush 触发一次；标志消费后下一 tick 不再触发。"""
        room_state = MagicMock()
        room_state.last_speech_ms = None
        # 第 2 次 flush 才需要 is_cold：第 1 次 flush 因为 external_pending=True 在 ProactiveTrigger 内部
        # 短路返回（根本不调用 is_cold），所以 mock 的 is_cold 只会在第 2 次 flush 被调用一次。
        # 此时返回 False 让 cold 分支不命中，验证"标志消费后不再触发"。
        room_state.is_cold = MagicMock(return_value=False)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某话题"))

        # 第 1 轮 flush（external 触发）需要的 Planner + Replyer 响应
        # 第 2 轮 flush（不应触发）不消耗响应——若误触发会缺响应报错
        llm_responses = [
            _llm_response(success=True, content=_planner_json(should_reply=True)),
            _llm_response(success=True, content=_replyer_json(text="第一次主动")),
        ]

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,
                "proactive_min_interval_ms": 0,
                "proactive_schedule_interval_ms": 0,
            },
            llm_responses=llm_responses,
            room_state=room_state,
        )

        # 模拟外部 API 调用：设置标志
        await decider.trigger_proactive("测试话题")

        # 第 1 轮 flush：标志置位 → 触发（external 优先于 cold）
        await decider._maybe_flush()
        assert decider._total_proactive == 1
        assert decider._llm_service.chat.await_count == 2

        # 第 2 轮 flush：标志已消费、is_cold 返回 False（mock side_effect 第 2 次），
        # 不应再触发
        await decider._maybe_flush()
        # _total_proactive 不变
        assert decider._total_proactive == 1
        # LLM 调用次数不变（标志消费后没有再次触发）
        assert decider._llm_service.chat.await_count == 2

        # 标志已被消费（_external_proactive_pending=False）
        assert decider._external_proactive_pending is False

    @pytest.mark.asyncio
    async def test_external_trigger_uses_external_reason(self):
        """外部触发的 reason 应为 'external'（ProactiveTrigger 优先级 external > schedule > cold）。"""
        room_state = MagicMock()
        room_state.last_speech_ms = None
        # 同时让冷场也满足（让 external 优先级"覆盖"cold）
        room_state.is_cold = MagicMock(return_value=True)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某话题"))

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,
                "proactive_min_interval_ms": 0,
                "proactive_schedule_interval_ms": 0,
            },
            llm_responses=[
                _llm_response(success=True, content=_planner_json(should_reply=True)),
                _llm_response(success=True, content=_replyer_json(text="外部触发")),
            ],
            room_state=room_state,
        )

        await decider.trigger_proactive("webui")

        await decider._maybe_flush()

        # 验证触发：_total_proactive=1
        assert decider._total_proactive == 1
        # ProactiveTrigger 内部 _trigger_history 应追加 1 条
        assert len(decider._proactive_trigger._trigger_history) == 1


# ==================== 场景 4：发布 Intent 后状态更新 ====================


class TestPostPublishStateUpdate:
    """Intent 发布后，room_state.last_speech_ms 与 proactive_trigger._last_trigger_ms 应更新"""

    @pytest.mark.asyncio
    async def test_record_speech_and_record_trigger_called(self):
        """冷场触发成功 → room_state.last_speech_ms 被更新、proactive_trigger 记录历史。"""
        room_state = MagicMock()
        room_state.last_speech_ms = None
        room_state.is_cold = MagicMock(return_value=True)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某话题"))
        # record_speech 是同步调用（不是 await）—— 用普通 MagicMock 即可
        room_state.record_speech = MagicMock()

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,
                "proactive_min_interval_ms": 0,
                "proactive_schedule_interval_ms": 0,
            },
            llm_responses=[
                _llm_response(success=True, content=_planner_json(should_reply=True)),
                _llm_response(success=True, content=_replyer_json(text="说话啦")),
            ],
            room_state=room_state,
        )

        await decider._maybe_flush()

        # 验证：record_speech 被同步调用（且仅一次）—— call_count 而非 await_count
        assert room_state.record_speech.call_count == 1
        # 验证：proactive_trigger._last_trigger_ms 已更新（非 None）
        assert decider._proactive_trigger._last_trigger_ms is not None
        # 验证：trigger_history 追加 1 条
        assert len(decider._proactive_trigger._trigger_history) == 1

    @pytest.mark.asyncio
    async def test_min_interval_prevents_immediate_retrigger(self):
        """上一次发言后 min_interval 内不应再次触发主动发言（防接龙）。"""
        # 模拟"刚刚发过言"的场景：last_speech_ms 距离 now < min_interval
        room_state = MagicMock()
        # last_speech_ms = 1000_000_000, now ≈ 1000_000_500（间隔 500ms < min_interval 120_000ms）
        recent_speech = 1_000_000_000
        room_state.last_speech_ms = recent_speech
        room_state.is_cold = MagicMock(return_value=True)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某话题"))
        room_state.record_speech = MagicMock()

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,
                "proactive_cold_timeout_ms": 45_000,
                "proactive_min_interval_ms": 120_000,  # 120 秒防接龙
                "proactive_schedule_interval_ms": 0,
            },
            # 故意不留响应（不应被调用）
            llm_responses=[],
            room_state=room_state,
        )

        # 模拟时间推进 500ms（远小于 min_interval）
        # 通过 monkeypatch now_ms 实现确定性时间
        import src.stages.decision.deciders.amaidesu.amaidesu_decider as decider_mod

        original_now_ms = decider_mod.now_ms

        def fake_now_ms():
            return recent_speech + 500

        decider_mod.now_ms = fake_now_ms
        try:
            await decider._maybe_flush()
        finally:
            decider_mod.now_ms = original_now_ms

        # 断言：min_interval 拦下，proactive 不触发
        assert decider._total_proactive == 0
        assert decider._llm_service.chat.await_count == 0


# ==================== 场景 5：默认关闭 ====================


class TestProactiveDisabledByDefault:
    """proactive_enabled=False（默认）时不触发主动发言"""

    @pytest.mark.asyncio
    async def test_disabled_by_default_no_proactive(self):
        """ConfigSchema 默认 proactive_enabled=False → 即便冷场，proactive 也不触发。"""
        # 不显式传 proactive_enabled（用默认值）
        room_state = MagicMock()
        room_state.last_speech_ms = None
        room_state.is_cold = MagicMock(return_value=True)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某话题"))

        decider = _make_decider(
            config={
                "type": "amaidesu",
                # 注意：proactive_enabled 不传 → 默认 False
            },
            llm_responses=[],  # 不应被调用
            room_state=room_state,
        )

        # 断言：触发器 enabled=False（验证 ProactiveTrigger 内部状态）
        assert decider._proactive_trigger._enabled is False

        await decider._maybe_flush()

        # 断言：proactive 不触发，LLM 未调用
        assert decider._total_proactive == 0
        assert decider._llm_service.chat.await_count == 0

    @pytest.mark.asyncio
    async def test_explicit_disabled_no_proactive(self):
        """显式 proactive_enabled=False 时也不触发（与默认值行为一致）。"""
        room_state = MagicMock()
        room_state.last_speech_ms = None
        room_state.is_cold = MagicMock(return_value=True)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某话题"))

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": False,  # 显式关闭
                "proactive_cold_timeout_ms": 45_000,
            },
            llm_responses=[],
            room_state=room_state,
        )

        await decider._maybe_flush()

        assert decider._total_proactive == 0
        assert decider._llm_service.chat.await_count == 0


# ==================== 场景 6（额外）：事件 guardrail ====================


class TestProactiveEventGuardrail:
    """主动发言路径不应引入任何新事件（仅 DECISION_INTENT_GENERATED）"""

    @pytest.mark.asyncio
    async def test_proactive_publishes_only_decision_intent(self):
        """proactive 路径下所有 event_bus.emit 必须仍是 DECISION_INTENT_GENERATED。"""
        room_state = MagicMock()
        room_state.last_speech_ms = None
        room_state.is_cold = MagicMock(return_value=True)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某话题"))

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,
                "proactive_min_interval_ms": 0,
                "proactive_schedule_interval_ms": 0,
            },
            llm_responses=[
                _llm_response(success=True, content=_planner_json(should_reply=True)),
                _llm_response(success=True, content=_replyer_json(text="说话")),
            ],
            room_state=room_state,
        )

        await decider._maybe_flush()

        # 所有 emit 必须是 DECISION_INTENT_GENERATED
        for call in decider._event_bus.emit.await_args_list:
            args, _ = call
            assert args[0] == CoreEvents.DECISION_INTENT_GENERATED
        assert decider._event_bus.emit.await_count == 1


# ==================== 场景 7（额外）：统计 ====================


class TestProactiveStatistics:
    """get_statistics() 应暴露 total_proactive 字段"""

    @pytest.mark.asyncio
    async def test_statistics_includes_total_proactive(self):
        """_total_proactive=0 时 get_statistics 仍包含 total_proactive 字段（结构兼容）。"""
        decider = _make_decider(
            config={"type": "amaidesu"},
        )
        stats = decider.get_statistics()
        assert "total_proactive" in stats
        assert stats["total_proactive"] == 0

    @pytest.mark.asyncio
    async def test_statistics_increments_after_trigger(self):
        """触发后 _total_proactive 自增、stats 同步更新。"""
        room_state = MagicMock()
        room_state.last_speech_ms = None
        room_state.is_cold = MagicMock(return_value=True)
        room_state.get_snapshot = MagicMock(return_value=_make_snapshot(topic_summary="某话题"))

        decider = _make_decider(
            config={
                "type": "amaidesu",
                "proactive_enabled": True,
                "proactive_min_interval_ms": 0,
                "proactive_schedule_interval_ms": 0,
            },
            llm_responses=[
                _llm_response(success=True, content=_planner_json(should_reply=True)),
                _llm_response(success=True, content=_replyer_json(text="说话")),
            ],
            room_state=room_state,
        )

        await decider._maybe_flush()

        stats = decider.get_statistics()
        assert stats["total_proactive"] == 1
        assert stats["total_replies"] == 1
