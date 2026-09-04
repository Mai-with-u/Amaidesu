"""AgendaState 单元测试（Wave 6 Streamer Agent 节目单运行时状态机 + Storage 适配）。"""

from __future__ import annotations

from src.agents.streamer.agenda.agenda import Agenda, AgendaSegment
from src.agents.streamer.agenda.agenda_state import AgendaState, AgendaStatus


def _make_agenda() -> Agenda:
    return Agenda(
        agenda_id="agenda_test",
        title="测试节目单",
        segments=[
            AgendaSegment(
                id="s1",
                title="开场",
                task_description="自我介绍",
                duration_ms=10_000,
                min_duration_ms=5_000,
                key_points=["问候", "介绍"],
            ),
            AgendaSegment(
                id="s2",
                title="聊天",
                task_description="和观众互动",
                duration_ms=20_000,
            ),
            AgendaSegment(
                id="s3",
                title="结束",
                task_description="说再见",
                duration_ms=10_000,
            ),
        ],
        fallback_segment_id="s2",
    )


class TestAgendaStateLifecycle:
    """start / pause / resume / skip / rewind / jump_to / advance_to / unload。"""

    def test_initial_state(self) -> None:
        state = AgendaState()
        assert state.status == AgendaStatus.INACTIVE
        assert state.current_segment_id is None
        assert state.completed_segment_ids == []

    def test_start_advances_to_first_segment(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        assert state.status == AgendaStatus.RUNNING
        assert state.current_segment_id == "s1"
        assert state.agenda_started_at_ms == 1_000

    def test_pause_resume_segment_time_freeze(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        state.pause(now_ms=3_000)
        assert state.is_paused is True
        state.resume(now_ms=5_000)
        assert state.is_paused is False
        assert state.get_current_segment_elapsed_ms(now_ms=6_000) == 3_000

    def test_skip_advances_and_marks_completed(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        state.skip(now_ms=2_000)
        assert state.current_segment_id == "s2"
        assert "s1" in state.completed_segment_ids

    def test_skip_to_end_marks_completed(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        state.skip(now_ms=2_000)  # s1 → s2
        state.skip(now_ms=3_000)  # s2 → s3
        state.skip(now_ms=4_000)  # s3 → COMPLETED
        assert state.status == AgendaStatus.COMPLETED
        assert state.current_segment_id is None
        assert "s3" in state.completed_segment_ids

    def test_rewind_goes_back(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        state.skip(now_ms=2_000)
        state.rewind(now_ms=3_000)
        assert state.current_segment_id == "s1"

    def test_jump_to_invalid_raises(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        import pytest

        with pytest.raises(ValueError):
            state.jump_to("nonexistent", now_ms=2_000)

    def test_jump_to_valid_segment(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        state.jump_to("s3", now_ms=2_000)
        assert state.current_segment_id == "s3"

    def test_advance_to_does_not_set_manually_overridden(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        state.skip(now_ms=2_000)
        assert state.manually_overridden is True
        # advance_to clears manually_overridden
        state.advance_to("s2", now_ms=3_000)
        assert state.manually_overridden is False

    def test_unload_resets_state(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        state.unload(now_ms=3_000)
        assert state.status == AgendaStatus.UNLOADED
        assert state.agenda is None
        assert state.current_segment_id is None
        assert state.completed_segment_ids == []


class TestAgendaStateSnapshot:
    """get_snapshot 返回 Dashboard 渲染所需的全部字段。"""

    def test_snapshot_initial(self) -> None:
        state = AgendaState()
        snap = state.get_snapshot(now_ms=1_000)
        assert snap["status"] == "inactive"
        assert snap["current_segment"] is None
        assert snap["next_segment"] is None
        assert snap["completed_count"] == 0
        assert snap["total_count"] == 0

    def test_snapshot_running_with_progress(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        snap = state.get_snapshot(now_ms=2_500)
        assert snap["status"] == "running"
        assert snap["current_segment"]["id"] == "s1"
        assert snap["current_segment"]["title"] == "开场"
        assert snap["next_segment"]["id"] == "s2"
        assert snap["completed_count"] == 0
        assert snap["total_count"] == 3
        assert snap["agenda_id"] == "agenda_test"


class TestAgendaStateProgress:
    """get_elapsed_live_ms / get_progress_percent。"""

    def test_progress_unloaded_returns_none(self) -> None:
        state = AgendaState()
        assert state.get_elapsed_live_ms(now_ms=1_000) is None
        assert state.get_progress_percent(now_ms=1_000) is None

    def test_progress_running(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        # 40s 总时长；5s 后进度 = 5/40 = 12.5%
        pct = state.get_progress_percent(now_ms=6_000)
        assert pct is not None
        assert 12.0 < pct < 13.0

    def test_progress_clamped_to_100(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        pct = state.get_progress_percent(now_ms=999_000)
        assert pct == 100.0


class TestAgendaStateExpansion:
    """needs_expansion / cache_expanded / get_expanded。"""

    def test_expansion_set_on_start(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        assert state.needs_expansion("s1") is True

    def test_cache_expanded_removes_from_needs(self) -> None:
        state = AgendaState()
        agenda = _make_agenda()
        state.start(agenda, now_ms=1_000)
        from src.agents.streamer.agenda.agenda_loader import ExpandedSegment

        expanded = ExpandedSegment(segment_id="s1", opening_line="嗨", topic_guidance="嗨")
        state.cache_expanded(expanded)
        assert state.needs_expansion("s1") is False
        assert state.get_expanded("s1") is expanded


class TestAgendaParseToml:
    """parse_agenda_toml（纯 TOML 解析）。"""

    def test_parse_valid_toml(self, tmp_path):
        toml_content = """
agenda_id = "x"
title = "test"

[[segments]]
id = "s1"
title = "open"
task_description = "hello"
duration_ms = 5000
"""
        path = tmp_path / "test.toml"
        path.write_text(toml_content, encoding="utf-8")

        from src.agents.streamer.agenda.agenda import parse_agenda_toml

        agenda = parse_agenda_toml(path)
        assert agenda.agenda_id == "x"
        assert agenda.title == "test"
        assert len(agenda.segments) == 1
        assert agenda.segments[0].id == "s1"
        assert agenda.segments[0].duration_ms == 5000

    def test_parse_invalid_toml_raises(self, tmp_path):
        toml_content = """
[agenda]
agenda_id = "x"
title = "test"

[[agenda.segments]]
id = "s1"
title = "open"
task_description = "hello"
duration_ms = 500
"""
        path = tmp_path / "invalid.toml"
        path.write_text(toml_content, encoding="utf-8")

        from src.agents.streamer.agenda.agenda import parse_agenda_toml
        import pytest

        with pytest.raises(Exception):  # ValidationError
            parse_agenda_toml(path)