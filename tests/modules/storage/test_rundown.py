"""流程单（Rundown）数据契约：构造与默认值、时长下界、extra=forbid、非空环节、跨字段校验、DEFAULT_RUNDOWN 形状"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.modules.storage.models.rundown import (
    DEBUT_ZATSUDAN_RUNDOWN,
    DEFAULT_RUNDOWN,
    GAME_OPENING_TALK_RUNDOWN,
    RUNDOWN_PRESETS,
    Rundown,
    RundownSegment,
)


def _segment(**overrides) -> RundownSegment:
    fields = {"id": "s", "title": "t", "task_description": "d", "expected_ms": 10_000, **overrides}
    return RundownSegment(**fields)


def test_construction_defaults_and_full_fields() -> None:
    minimal = RundownSegment(id="s1", title="开场", task_description="和观众打招呼", expected_ms=10_000)
    assert (minimal.min_duration_ms, minimal.notes, minimal.mode, minimal.beats) == (None, None, None, [])

    full = _segment(
        expected_ms=60_000,
        min_duration_ms=30_000,
        notes="参考开场白",
        mode="talk",
        beats=["  第一点  ", "", "第二点"],
    )
    assert (full.min_duration_ms, full.notes, full.mode, full.beats) == (
        30_000,
        "参考开场白",
        "talk",
        ["第一点", "第二点"],
    )

    rundown = Rundown(rundown_id="rd_1", title="测试流程单", segments=[minimal])
    assert (rundown.rundown_id, rundown.title, len(rundown.segments)) == ("rd_1", "测试流程单", 1)


@pytest.mark.parametrize(
    ("overrides", "valid"),
    [
        ({"expected_ms": 1000}, True),
        ({"expected_ms": 999}, False),
        ({"min_duration_ms": 1000}, True),
        ({"min_duration_ms": 999}, False),
        ({"min_duration_ms": None}, True),  # 未设置跳过下界校验
        ({"branches": []}, False),  # extra=forbid：v2 Agenda 遗留字段不允许
    ],
)
def test_segment_field_bounds_and_forbidden_extras(overrides: dict, valid: bool) -> None:
    if valid:
        _segment(**overrides)
    else:
        with pytest.raises(ValidationError):
            _segment(**overrides)


def test_rundown_rejects_extras_empty_segments_duplicate_ids_and_contradictory_durations() -> None:
    with pytest.raises(ValidationError):
        Rundown(rundown_id="r", title="t", segments=[_segment()], fallback_segment_id="s")  # v2 Agenda 遗留字段
    with pytest.raises(ValidationError):
        Rundown(rundown_id="r", title="t", segments=[])

    with pytest.raises(ValidationError) as dup:
        Rundown(rundown_id="r", title="t", segments=[_segment(id="dup", title="a"), _segment(id="dup", title="b")])
    assert "dup" in str(dup.value) and "重复 id" in str(dup.value)

    with pytest.raises(ValidationError) as contradictory:  # 最少停留不能大于预期时长
        Rundown(rundown_id="r", title="t", segments=[_segment(id="s1", expected_ms=10_000, min_duration_ms=20_000)])
    assert "min_duration_ms" in str(contradictory.value) and "expected_ms" in str(contradictory.value)

    equal = Rundown(rundown_id="r", title="t", segments=[_segment(expected_ms=10_000, min_duration_ms=10_000)])
    assert equal.segments[0].min_duration_ms == equal.segments[0].expected_ms  # 上下界重合仍是有效配置


def test_default_rundown_is_valid_and_has_the_agreed_shape() -> None:
    restored = Rundown.model_validate(DEFAULT_RUNDOWN.model_dump())
    assert (restored.rundown_id, restored.title) == (DEFAULT_RUNDOWN.rundown_id, DEFAULT_RUNDOWN.title)
    assert DEFAULT_RUNDOWN == DEBUT_ZATSUDAN_RUNDOWN

    assert DEFAULT_RUNDOWN.rundown_id == "default_first_stream"
    assert [seg.id for seg in DEFAULT_RUNDOWN.segments] == [
        "opening",
        "origin",
        "outfit",
        "stories",
        "qna",
        "closing",
    ]
    assert [seg.expected_ms for seg in DEFAULT_RUNDOWN.segments] == [
        240_000,
        420_000,
        360_000,
        420_000,
        600_000,
        240_000,
    ]  # 4/7/6/7/10/4 分钟，6 段合计 38 分钟
    assert sum(seg.expected_ms for seg in DEFAULT_RUNDOWN.segments) == 38 * 60_000
    assert all(seg.mode == "talk" and seg.min_duration_ms and seg.beats for seg in DEFAULT_RUNDOWN.segments)
    assert all(seg.task_description for seg in DEFAULT_RUNDOWN.segments)

    # 游戏场预设：开场 5 分钟 talk 热身段 + play 主段 + talk 收尾段
    assert set(RUNDOWN_PRESETS.keys()) == {"debut_zatsudan", "game_with_opening_talk"}
    opening = GAME_OPENING_TALK_RUNDOWN.segments[0]
    assert (opening.id, opening.mode, opening.expected_ms) == ("opening_talk", "talk", 300_000)
    assert len(opening.beats) >= 2
    assert GAME_OPENING_TALK_RUNDOWN.segments[1].mode == "play"
