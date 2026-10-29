"""Planner 参考段组装器：有数据才渲染段、无占位文本、段序固定（长期记忆 → 上场回顾 → 环节描述 → 直播间快照 → 人物画像），时刻按本地时区"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import src.agents.streamer.planner_context as planner_context_module
from src.agents.streamer.planner_context import AssemblerInputs, EnvironmentBlock, PlannerAssembler


def _production_inputs() -> AssemblerInputs:
    """生产型输入——模拟 planner 生产路径实际传递的数据形态。"""
    return AssemblerInputs(
        long_memory_section="## 答应过的事\n- 我答应 alice 画新皮肤（未兑现）",
        session_recap_section="【2026-10-13 的直播，时长 2 小时】这场我答应 alice 下次画新皮肤",
        stage_descriptions="## 开场聊天\n自由闲聊，观察弹幕节奏。",
        environment=EnvironmentBlock(
            minute_bucket_ms=1_760_000_000_000,
            duration_so_far_ms=45 * 60_000,
            unread_summary="3 条未读互动",
        ),
        person_profile_section="- bilibili/alice: 上次问过新皮肤",
    )


def test_sections_render_in_fixed_order() -> None:
    text = PlannerAssembler().assemble(_production_inputs())
    sections = (
        "## 长期记忆-内部参考",
        "## 上场回顾-内部参考",
        "## 环节描述",
        "## 直播间快照",
        "## 人物画像-内部参考",
    )
    assert [text.index(section) for section in sections] == sorted(text.index(section) for section in sections)
    # 空输入整段省略，连段标题也不出现
    assert PlannerAssembler().assemble(AssemblerInputs()) == ""
    assert "上场回顾-内部参考" not in PlannerAssembler().assemble(AssemblerInputs(stage_descriptions="有环节"))


@pytest.mark.parametrize(
    "inputs",
    [
        AssemblerInputs(),
        AssemblerInputs(stage_descriptions="有环节"),
        AssemblerInputs(environment=EnvironmentBlock(minute_bucket_ms=0, duration_so_far_ms=0)),
        _production_inputs(),
    ],
)
def test_no_placeholder_text_in_output(inputs: AssemblerInputs) -> None:
    text = PlannerAssembler().assemble(inputs)
    assert not any(p in text for p in ("（未配置 persona）", "（暂无）", "（空）", "（无）"))


def test_environment_render_details() -> None:
    """快照渲染使用运行环境的本地时刻；零时长/空字段行省略。"""
    text = PlannerAssembler().assemble(
        AssemblerInputs(environment=EnvironmentBlock(minute_bucket_ms=0, duration_so_far_ms=0))
    )
    expected_time = datetime.fromtimestamp(0).strftime("%Y-%m-%d %H:%M")
    assert f"- 时刻: {expected_time}" in text
    assert "已开播时长" not in text
    assert "0 分钟" not in text


@pytest.mark.parametrize(
    ("offset_hours", "expected_time"),
    [(0, "1970-01-01 00:01"), (8, "1970-01-01 08:01"), (-5, "1969-12-31 19:01")],
)
def test_environment_uses_local_time_conversion(
    monkeypatch: pytest.MonkeyPatch, offset_hours: int, expected_time: str
) -> None:
    """隔离系统时区接口，同时验证毫秒换算、分钟格式与跨日结果。"""
    local_datetime = Mock()
    local_datetime.fromtimestamp.side_effect = lambda value: datetime.fromtimestamp(
        value, tz=timezone(timedelta(hours=offset_hours))
    )
    monkeypatch.setattr(planner_context_module, "datetime", local_datetime)

    text = PlannerAssembler().assemble(
        AssemblerInputs(environment=EnvironmentBlock(minute_bucket_ms=60_000, duration_so_far_ms=0))
    )

    local_datetime.fromtimestamp.assert_called_once_with(60)
    assert f"- 时刻: {expected_time}" in text
