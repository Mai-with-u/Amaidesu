"""lead_mode.format_rundown_nudge_notice 格式化单元测试。"""

from __future__ import annotations

from src.agents.minecraft.lead_mode import LEAD_MODE_PROMPT_SECTION, format_rundown_nudge_notice


def test_format_rundown_nudge_notice_with_next_segment_and_last_segment() -> None:
    """带下一段提示包含 next/renew 指引；末段不再诱导调用 next，仅提示计划时间已到、收尾交权或 renew 续期。"""
    with_next = format_rundown_nudge_notice(
        segment_id="play_1",
        segment_title="主线探索",
        elapsed_ms=960_000,
        expected_ms=900_000,
        next_segment_id="closing",
        next_segment_title="收尾告别",
    )
    assert with_next.startswith("[流程单超时提醒]")
    assert "主线探索" in with_next
    assert "16 分钟" in with_next and "15 分钟" in with_next
    assert "收尾告别" in with_next and "closing" in with_next
    assert "rundown_control" in with_next and "next" in with_next and "renew" in with_next

    last_seg = format_rundown_nudge_notice(
        segment_id="closing",
        segment_title="收尾告别",
        elapsed_ms=420_000,
        expected_ms=300_000,
    )
    assert last_seg.startswith("[流程单超时提醒]")
    assert "最后一段" in last_seg and "计划时间已到" in last_seg and "renew" in last_seg
    assert "交还控制权" in last_seg
    assert "next" not in last_seg
    assert "[流程单超时提醒]" in LEAD_MODE_PROMPT_SECTION
