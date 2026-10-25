"""DecisionRoundExecutor：决策结果视图、可观测事件顺序、失败与沉默分支的记账，以及原因码中文文案"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.decision_executor import (
    DecisionRoundExecutor,
    _silent_reason_text,
    _trigger_reason_text,
)
from src.agents.streamer.stats import StreamerStats
from src.agents.streamer.timeline import GameEventBatch
from src.modules.events.names import CoreEvents

_OKAY_OUTCOME = {
    "replied": True,
    "target": "m1",
    "reply_to": "m1",
    "topic_summary": "闲聊",
    "reply_guidance": "热情一点",
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
    else:
        # 原始输出与请求指针随 outcome 带回（plan() 的返回值契约）
        planner.plan = AsyncMock(return_value={**outcome, "raw_content": "RAW", "llm_request_id": "req_1"})

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
        game_events_provider=MagicMock(return_value=GameEventBatch()),
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


async def test_window_boundary_runs_once_before_history_read():
    """窗边界收口（出账）每轮恰好一次，且先于历史读取——出账行才能进本轮投影。"""
    calls: list[str] = []

    def _boundary() -> None:
        calls.append("boundary")

    async def _history() -> list:
        calls.append("history")
        return []

    planner = MagicMock()
    planner.plan = AsyncMock(return_value={**_OKAY_OUTCOME, "raw_content": "RAW"})
    speech = MagicMock()
    speech.dispatch = AsyncMock(return_value=None)
    executor = DecisionRoundExecutor(
        planner=planner,
        speech=speech,
        event_bus=None,
        room_state=MagicMock(),
        proactive_trigger=MagicMock(),
        stats=StreamerStats(),
        thinking_sink=None,
        thinking_enabled=False,
        history_provider=_history,
        rundown_text_provider=MagicMock(return_value=None),
        window_boundary=_boundary,
    )

    await executor.execute([], forced=False, trigger_reason="batch:flush")

    assert calls == ["boundary", "history"], "收口先于读取，且每轮各恰好一次"


# happy：一轮决策的完整产出


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
    assert (result["speech"], result["emotion"], result["utterance_id"]) == ("你好呀", "happy", "utt_1_1")
    assert result["plan"]["should_reply"] is True and result["error"] is None
    assert result["round_id"].startswith("rnd_")

    # planning → decision → idle，各自带来源标识
    assert [(name, source) for name, _, source in deps["emissions"]] == [
        (CoreEvents.STREAMER_STAGE, "streamer_agent.stage"),
        (CoreEvents.PLANNER_DECISION, "streamer_agent.decision"),
        (CoreEvents.STREAMER_STAGE, "streamer_agent.stage"),
    ]

    # 发言管线与记账
    deps["speech"].dispatch.assert_called_once()
    deps["room_state"].record_speech.assert_called_once()
    assert deps["stats"].total_replies == 1
    assert deps["stats"].total_no_action == 0

    # 非 proactive 不记录触发
    deps["proactive_trigger"].record_trigger.assert_not_called()


async def test_execute_proactive_records_trigger_with_reason_body():
    outcome = dict(_OKAY_OUTCOME)
    outcome["reply_payload"] = {"speech": "大家好", "emotion": "", "actions": []}
    executor, deps = _make_executor(outcome)

    await executor.execute([], forced=False, trigger_reason="proactive:cold_start", proactive=True)

    deps["proactive_trigger"].record_trigger.assert_called_once()
    reason = deps["proactive_trigger"].record_trigger.call_args.args[0]
    assert reason == "cold_start"
    assert deps["stats"].total_proactive == 0  # total_proactive 由调度半记，执行半不碰


# 边界：planner 失败路径（早返回语义逐字锁定）


async def test_planner_exception_short_circuits_with_error_prefix():
    executor, deps = _make_executor(None, planner_error=True)

    result = await executor.execute([], forced=False, trigger_reason="batch:flush")

    assert result["error"].startswith("planner_failed:")
    assert result["plan"] is None and result["speech"] is None
    stats = deps["stats"]
    assert (stats.planner_failures, stats.total_no_action, stats.total_replies) == (1, 1, 0)
    # 失败也收口：planning → decision → idle 三事件齐全
    order = [name for name, _, _ in deps["emissions"]]
    assert order == [CoreEvents.STREAMER_STAGE, CoreEvents.PLANNER_DECISION, CoreEvents.STREAMER_STAGE]
    deps["speech"].dispatch.assert_not_called()


@pytest.mark.parametrize(
    ("outcome", "error", "planner_failures", "replyer_failures"),
    [
        ({"replied": False, "silent_reason": "max_steps", "reply_failures": 2, "error": None}, None, 0, 2),
        (
            {"replied": False, "reply_failures": 0, "error": "reply tool exploded"},
            "planner_failed: reply tool exploded",
            1,
            0,
        ),
    ],
    ids=["silent", "silent_with_error"],
)
async def test_no_reply_outcomes_count_no_action(outcome, error, planner_failures, replyer_failures):
    executor, deps = _make_executor(outcome)
    result = await executor.execute([], forced=False, trigger_reason="batch:flush")

    assert result["plan"]["should_reply"] is False and result["error"] == error
    assert result["silent_reason"] == outcome.get("silent_reason")
    stats = deps["stats"]
    assert (stats.total_no_action, stats.planner_failures, stats.replyer_failures) == (
        1,
        planner_failures,
        replyer_failures,
    )
    deps["speech"].dispatch.assert_not_called()


# 阶段补充文案：原因码 → 可读中文（面板 detail 不裸奔英文单词）


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


def test_silent_reason_text_maps_known_codes():
    texts = [_silent_reason_text(r) for r in ("natural", "max_steps", "brand_new_reason")]
    assert texts == ["自然终止", "超出步数上限", "brand_new_reason"]


async def test_execute_emits_human_readable_stage_detail():
    executor, deps = _make_executor(dict(_OKAY_OUTCOME))

    await executor.execute([], forced=False, trigger_reason="window_expired")

    stage_details = [p.detail for name, p, _ in deps["emissions"] if name == CoreEvents.STREAMER_STAGE]
    assert stage_details == ["聚合窗口到期", "决策轮结束：发言已出"]
