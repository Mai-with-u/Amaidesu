"""流程单（Rundown）数据契约单元测试。

覆盖：
- 基础构造、字段默认值（``key_points`` / ``min_duration_ms`` / ``notes``）
- ``expected_ms`` / ``min_duration_ms`` 的 ``ge=1000`` 下界
- ``extra="forbid"`` 拒绝未知字段
- ``Rundown.segments`` 非空（``min_length=1``）
- 跨字段：环节 ``id`` 在流程单内唯一；任一环节 ``min_duration_ms`` 不大于 ``expected_ms``
- ``DEFAULT_RUNDOWN`` 通过校验且形状合规（rundown_id / title / 4 个环节 / 时长）
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.streamer.rundown.rundown import (
    DEFAULT_RUNDOWN,
    Rundown,
    RundownSegment,
)


# ---------------------------------------------------------------------------
# 基础构造与默认值
# ---------------------------------------------------------------------------


def test_segment_minimal_construction() -> None:
    """环节只填必填字段（``key_points`` / ``min_duration_ms`` / ``notes`` 走默认）。"""
    seg = RundownSegment(
        id="s1",
        title="开场",
        task_description="和观众打招呼",
        expected_ms=10_000,
    )
    assert seg.id == "s1"
    assert seg.title == "开场"
    assert seg.task_description == "和观众打招呼"
    assert seg.key_points == []
    assert seg.expected_ms == 10_000
    assert seg.min_duration_ms is None
    assert seg.notes is None


def test_segment_full_construction() -> None:
    """环节全字段填齐（含 ``min_duration_ms`` / ``notes``）。"""
    seg = RundownSegment(
        id="s1",
        title="开场",
        task_description="和观众打招呼",
        key_points=["问好", "自我介绍"],
        expected_ms=60_000,
        min_duration_ms=30_000,
        notes="参考开场白：大家好，欢迎来到直播间",
    )
    assert seg.key_points == ["问好", "自我介绍"]
    assert seg.min_duration_ms == 30_000
    assert seg.notes == "参考开场白：大家好，欢迎来到直播间"


def test_rundown_minimal_construction() -> None:
    """流程单只填必填字段。"""
    rundown = Rundown(
        rundown_id="rd_1",
        title="测试流程单",
        segments=[
            RundownSegment(
                id="s1",
                title="开场",
                task_description="打招呼",
                expected_ms=10_000,
            ),
        ],
    )
    assert rundown.rundown_id == "rd_1"
    assert rundown.title == "测试流程单"
    assert len(rundown.segments) == 1


# ---------------------------------------------------------------------------
# 字段下界与可选性
# ---------------------------------------------------------------------------


def test_segment_expected_ms_ge_1000() -> None:
    """``expected_ms`` 接受 1000（边界），拒绝 999。"""
    RundownSegment(id="s", title="t", task_description="d", expected_ms=1000)
    with pytest.raises(ValidationError):
        RundownSegment(id="s", title="t", task_description="d", expected_ms=999)


def test_segment_min_duration_ms_ge_1000_when_set() -> None:
    """``min_duration_ms`` 设置时须 ``>= 1000``；未设置（``None``）跳过该校验。"""
    RundownSegment(
        id="s",
        title="t",
        task_description="d",
        expected_ms=10_000,
        min_duration_ms=1000,
    )
    with pytest.raises(ValidationError):
        RundownSegment(
            id="s",
            title="t",
            task_description="d",
            expected_ms=10_000,
            min_duration_ms=999,
        )
    # 未设置时 None 合法
    seg = RundownSegment(id="s", title="t", task_description="d", expected_ms=10_000, min_duration_ms=None)
    assert seg.min_duration_ms is None


# ---------------------------------------------------------------------------
# extra="forbid"
# ---------------------------------------------------------------------------


def test_segment_extra_field_rejected() -> None:
    """环节拒绝未知字段。"""
    with pytest.raises(ValidationError):
        RundownSegment(
            id="s",
            title="t",
            task_description="d",
            expected_ms=10_000,
            branches=[],  # v2 Agenda 遗留字段，本 v3 Rundown 不允许
        )


def test_rundown_extra_field_rejected() -> None:
    """流程单拒绝未知字段。"""
    with pytest.raises(ValidationError):
        Rundown(
            rundown_id="r",
            title="t",
            segments=[
                RundownSegment(id="s", title="t", task_description="d", expected_ms=10_000),
            ],
            fallback_segment_id="s",  # v2 Agenda 遗留字段，本 v3 Rundown 不允许
        )


# ---------------------------------------------------------------------------
# 列表长度
# ---------------------------------------------------------------------------


def test_rundown_segments_min_length_one() -> None:
    """``segments`` 为空列表被拒绝（``min_length=1``）。"""
    with pytest.raises(ValidationError):
        Rundown(rundown_id="r", title="t", segments=[])


# ---------------------------------------------------------------------------
# 跨字段：id 唯一 / min_duration_ms <= expected_ms
# ---------------------------------------------------------------------------


def test_rundown_segment_id_unique_within_rundown() -> None:
    """环节 ``id`` 在流程单内必须唯一；重复时 ``ValueError`` 含重复列表。"""
    with pytest.raises(ValidationError) as exc_info:
        Rundown(
            rundown_id="r",
            title="t",
            segments=[
                RundownSegment(id="dup", title="a", task_description="x", expected_ms=10_000),
                RundownSegment(id="dup", title="b", task_description="y", expected_ms=10_000),
            ],
        )
    msg = str(exc_info.value)
    assert "dup" in msg
    assert "重复 id" in msg


def test_rundown_min_duration_le_expected_ms() -> None:
    """任一环节 ``min_duration_ms`` 不能大于 ``expected_ms``（语义矛盾）。"""
    with pytest.raises(ValidationError) as exc_info:
        Rundown(
            rundown_id="r",
            title="t",
            segments=[
                RundownSegment(
                    id="s1",
                    title="矛盾",
                    task_description="d",
                    expected_ms=10_000,
                    min_duration_ms=20_000,
                ),
            ],
        )
    msg = str(exc_info.value)
    assert "min_duration_ms" in msg
    assert "expected_ms" in msg


def test_rundown_min_duration_equal_to_expected_allowed() -> None:
    """``min_duration_ms == expected_ms`` 合法（下界与上界重合，仍是有效配置）。"""
    rundown = Rundown(
        rundown_id="r",
        title="t",
        segments=[
            RundownSegment(
                id="s1",
                title="t",
                task_description="d",
                expected_ms=10_000,
                min_duration_ms=10_000,
            ),
        ],
    )
    assert rundown.segments[0].min_duration_ms == rundown.segments[0].expected_ms


# ---------------------------------------------------------------------------
# DEFAULT_RUNDOWN
# ---------------------------------------------------------------------------


def test_default_rundown_validates() -> None:
    """``DEFAULT_RUNDOWN`` 通过模型校验（自身即合规配置）。"""
    # 重新构造（DEFAULT_RUNDOWN 是模块级单例，构造一次后再用 model_validate 也应通过）
    reconstructed = Rundown.model_validate(DEFAULT_RUNDOWN.model_dump())
    assert reconstructed.rundown_id == DEFAULT_RUNDOWN.rundown_id
    assert reconstructed.title == DEFAULT_RUNDOWN.title
    assert len(reconstructed.segments) == len(DEFAULT_RUNDOWN.segments)


def test_default_rundown_shape() -> None:
    """``DEFAULT_RUNDOWN`` 形状：rundown_id / title / 4 个环节 / 各环节时长合理。"""
    assert DEFAULT_RUNDOWN.rundown_id == "default_first_stream"
    assert DEFAULT_RUNDOWN.title == "初次直播"
    assert len(DEFAULT_RUNDOWN.segments) == 4

    # 切片 1 拍板的四个环节标题
    titles = [seg.title for seg in DEFAULT_RUNDOWN.segments]
    assert titles == ["开场问候", "自我介绍", "互动闲聊", "收尾预告"]

    # 各环节 expected_ms 按拍板值（3 / 10 / 20 / 5 分钟）
    expected_ms_list = [seg.expected_ms for seg in DEFAULT_RUNDOWN.segments]
    assert expected_ms_list == [180_000, 600_000, 1_200_000, 300_000]

    # 各环节都有非空 key_points
    for seg in DEFAULT_RUNDOWN.segments:
        assert len(seg.key_points) >= 1
        assert seg.task_description != ""

    # 各环节 id 在流程单内唯一（防御性复检）
    ids = [seg.id for seg in DEFAULT_RUNDOWN.segments]
    assert len(ids) == len(set(ids))


def test_default_rundown_segments_total_minutes() -> None:
    """``DEFAULT_RUNDOWN`` 各环节时长之和约为 38 分钟（拍板值校验）。"""
    total_ms = sum(seg.expected_ms for seg in DEFAULT_RUNDOWN.segments)
    # 180000 + 600000 + 1200000 + 300000 = 2280000 ms = 38 min
    assert total_ms == 2_280_000
