"""DecisionRoundExecutor 单元测试：决策结果视图 + 可观测事件 + 失败分支。

全链路（弹幕 → flush → 决策 → 发言）由 ``test_decision_loop`` 覆盖；
本文件直接打 executor，锁定执行半的分支行为与早返回语义
（planner 失败早返回的字面行为在 ``test_decision_loop`` 有全链路依赖）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.decision_executor import (
    DecisionRoundExecutor,
    _silent_reason_text,
    _trigger_reason_text,
)
from src.agents.streamer.stats import StreamerStats
from src.modules.events.names import CoreEvents

_OKAY_OUTCOME = {
    "replied": True,
    "target": "m1",
    "reply_to": "m1",
    "topic_summary": "闲聊",
    "reply_guidance": "热情一点",
    "confidence": 0.9,
    "silent_reason": None,
    "reply_duration_ms": 12,
    "reply_payload": {"speech": "你好呀", "emotion": {"name": "happy", "intensity": 0.5}, "actions": []},
}


def _make_event_bus() -> tuple[MagicMock, list]:
    """emit 记录式 stub：(调用参数列表, bus)。"""
    emissions: list = []
    bus = MagicMock()

    async def _emit(event_name, payload, source=None):
        emissions.append((event_name, payload, source))

    bus.emit = AsyncMock(side_effect=_emit)
    return bus, emissions


def _make_executor(outcome, *, planner_error: bool = False) -> tuple[DecisionRoundExecutor, dict]:
    """构造 executor + 依赖桩；返回 (executor, deps) 便于断言。"""
    planner = MagicMock()
    if planner_error:
        planner.plan = AsyncMock(side_effect=RuntimeError("boom"))
        planner.last_raw_content = ""
        planner.last_request_id = None
        planner.last_failure = "llm down"
    else:
        planner.plan = AsyncMock(return_value=outcome)
        planner.last_raw_content = "RAW"
        planner.last_request_id = "req_1"

    speech = MagicMock()
    # dispatch 现为 async（发言事件同步发出契约）；返回值三元组不变
    speech.dispatch = AsyncMock(return_value=("你好呀", "happy", "utt_1_1"))

    room_state = MagicMock()
    proactive_trigger = MagicMock()
    stats = StreamerStats()
    bus, emissions = _make_event_bus()

    executor = DecisionRoundExecutor(
        planner=planner,
        speech=speech,
        event_bus=bus,
        room_state=room_state,
        proactive_trigger=proactive_trigger,
        stats=stats,
        thinking_sink=None,
        thinking_enabled=False,
        history_provider=AsyncMock(return_value=[]),
        rundown_text_provider=MagicMock(return_value=None),
        game_narrative_provider=MagicMock(return_value=""),
    )
    deps = {
        "planner": planner,
        "speech": speech,
        "room_state": room_state,
        "proactive_trigger": proactive_trigger,
        "stats": stats,
        "emissions": emissions,
    }
    return executor, deps


# ---------------------------------------------------------------------------
# happy：一轮决策的完整产出
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_full_round_emits_stage_and_decision():
    executor, deps = _make_executor(dict(_OKAY_OUTCOME))
    batch = [MagicMock(message_id="m1", content="主播好", user=MagicMock(id="u1", name="观众"))]

    result = await executor.execute(batch, forced=False, trigger_reason="batch:flush")

    # 结果视图键完整（外部契约）
    assert set(result.keys()) == {
        "round_id",
        "trigger_reason",
        "proactive",
        "forced",
        "plan",
        "speech",
        "emotion",
        "utterance_id",
        "reply_to_message_id",
        "silent_reason",
        "error",
        "planner_raw",
        "llm_request_id",
        "planner_duration_ms",
        "reply_duration_ms",
        "total_duration_ms",
    }
    assert result["speech"] == "你好呀"
    assert result["emotion"] == "happy"
    assert result["utterance_id"] == "utt_1_1"
    assert result["plan"]["should_reply"] is True
    assert result["error"] is None
    assert result["round_id"].startswith("rnd_")

    # 恰好两条 stage（planning → idle）+ 恰好一条 planner.decision
    events = [(name, source) for name, _, source in deps["emissions"]]
    assert events.count((CoreEvents.STREAMER_STAGE, "streamer_agent.stage")) == 2
    assert events.count((CoreEvents.PLANNER_DECISION, "streamer_agent.decision")) == 1
    # 顺序：planning → decision → idle
    order = [name for name, _, _ in deps["emissions"]]
    assert order == [CoreEvents.STREAMER_STAGE, CoreEvents.PLANNER_DECISION, CoreEvents.STREAMER_STAGE]

    # 发言管线与记账
    deps["speech"].dispatch.assert_called_once()
    deps["room_state"].record_speech.assert_called_once()
    assert deps["stats"].total_replies == 1
    assert deps["stats"].total_no_action == 0

    # 非 proactive 不记录触发
    deps["proactive_trigger"].record_trigger.assert_not_called()


@pytest.mark.asyncio
async def test_execute_proactive_records_trigger_with_reason_body():
    outcome = dict(_OKAY_OUTCOME)
    outcome["reply_payload"] = {"speech": "大家好", "emotion": "", "actions": []}
    executor, deps = _make_executor(outcome)

    await executor.execute([], forced=False, trigger_reason="proactive:cold_start", proactive=True)

    deps["proactive_trigger"].record_trigger.assert_called_once()
    reason = deps["proactive_trigger"].record_trigger.call_args.args[0]
    assert reason == "cold_start"
    assert deps["stats"].total_proactive == 0  # total_proactive 由调度半记，执行半不碰


# ---------------------------------------------------------------------------
# 边界：planner 失败路径（早返回语义逐字锁定）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_exception_short_circuits_with_error_prefix():
    executor, deps = _make_executor(None, planner_error=True)

    result = await executor.execute([], forced=False, trigger_reason="batch:flush")

    assert result["error"].startswith("planner_failed:")
    assert result["plan"] is None
    assert result["speech"] is None
    assert deps["stats"].planner_failures == 1
    assert deps["stats"].total_no_action == 1
    assert deps["stats"].total_replies == 0
    # 失败也收口：planning → decision → idle 三事件齐全
    order = [name for name, _, _ in deps["emissions"]]
    assert order == [CoreEvents.STREAMER_STAGE, CoreEvents.PLANNER_DECISION, CoreEvents.STREAMER_STAGE]
    deps["speech"].dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_planner_no_reply_counts_no_action_and_replyer_failures():
    outcome = {
        "replied": False,
        "silent_reason": "low_confidence",
        "reply_failures": 2,
        "error": None,
    }
    executor, deps = _make_executor(outcome)

    result = await executor.execute([], forced=False, trigger_reason="batch:flush")

    assert result["plan"]["should_reply"] is False
    assert result["silent_reason"] == "low_confidence"
    assert result["error"] is None  # outcome 无 error 时不标 planner 失败
    assert deps["stats"].total_no_action == 1
    assert deps["stats"].replyer_failures == 2
    assert deps["stats"].planner_failures == 0
    deps["speech"].dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_planner_no_reply_with_error_marks_planner_failure():
    outcome = {"replied": False, "reply_failures": 0, "error": "reply tool exploded"}
    executor, deps = _make_executor(outcome)

    result = await executor.execute([], forced=False, trigger_reason="batch:flush")

    assert result["error"] == "planner_failed: reply tool exploded"
    assert deps["stats"].planner_failures == 1
    assert deps["stats"].total_no_action == 1


# ---------------------------------------------------------------------------
# 阶段补充文案：原因码 → 可读中文（面板 detail 不裸奔英文单词）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("forced", "付费触发（SC / 礼物 / 上舰）"),
        ("batch_full", "弹幕攒满一批"),
        ("window_expired", "聚合窗口到期"),
        ("idle_compensation", "冷场补足一批"),
        ("proactive:cold", "冷场主动开麦"),
        ("proactive:rundown", "流程单推进"),
        ("proactive:dashboard_debug", "控制台手动触发"),
        ("dashboard:debug_test", "控制台决策测试"),
        ("mystery_reason", "mystery_reason"),
        ("", ""),
    ],
)
def test_trigger_reason_text_maps_known_codes(reason: str, expected: str):
    assert _trigger_reason_text(reason) == expected


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("natural", "自然终止"),
        ("max_steps", "超出步数上限"),
        ("brand_new_reason", "brand_new_reason"),
    ],
)
def test_silent_reason_text_maps_known_codes(reason: str, expected: str):
    assert _silent_reason_text(reason) == expected


@pytest.mark.asyncio
async def test_execute_emits_human_readable_stage_detail():
    executor, deps = _make_executor(dict(_OKAY_OUTCOME))

    await executor.execute([], forced=False, trigger_reason="window_expired")

    stage_details = [p.detail for name, p, _ in deps["emissions"] if name == CoreEvents.STREAMER_STAGE]
    assert stage_details == ["聚合窗口到期", "决策轮结束：发言已出"]
