"""录播姬文件名解析与场次时间窗匹配。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.modules.clipping.recording_matcher import (
    RecordingFile,
    SessionWindow,
    match_files_to_sessions,
    parse_recording_filename,
    scan_recording_dir,
)


def _make_file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x")
    return path


def test_parse_recording_filename_local_time() -> None:
    parsed = parse_recording_filename("录制-10000-20261104-232239-540-标题带-连字符.flv")
    assert parsed is not None
    assert parsed.room_id == "10000"
    assert parsed.title == "标题带-连字符"
    expected = datetime(2026, 11, 4, 23, 22, 39, 540000).timestamp() * 1000
    assert parsed.started_at_ms == int(expected)


def test_parse_rejects_non_recorder_names() -> None:
    assert parse_recording_filename("Maicraft直播-20261105-烤海带.mp4") is None
    assert parse_recording_filename("2026-10-07MC直播录屏.mp4") is None
    assert parse_recording_filename("录制-10000-20261104-232239-540-标题.txt") is None
    assert parse_recording_filename("随便.flv") is None


def test_parse_generic_obs_naming() -> None:
    from src.modules.clipping.recording_matcher import parse_generic_recording_filename

    # OBS 默认命名（空格分隔，秒级无毫秒）
    parsed = parse_generic_recording_filename("2026-11-08 20-00-05.mkv")
    assert parsed is not None
    assert parsed.room_id == ""
    expected = datetime(2026, 11, 8, 20, 0, 5).timestamp() * 1000
    assert parsed.started_at_ms == int(expected)
    # 分隔符兼容连字符/下划线；扩展名覆盖常见录制容器
    for name in ("2026-11-08_20-00-05.mp4", "2026-11-08-20-00-05.flv", "2026-11-08 20-00-05.mov"):
        assert parse_generic_recording_filename(name) is not None, name
    # 分片序号只进排序字段，不动开始时刻
    part2 = parse_generic_recording_filename("2026-11-08 20-00-05 (2).mp4")
    assert part2 is not None
    assert part2.sort_index == 2
    assert part2.started_at_ms == int(expected)
    # 不是日期时间命名的照样拒绝
    assert parse_generic_recording_filename("烤海带.mp4") is None
    assert parse_generic_recording_filename("2026-13-08 20-00-05.mp4") is None


def test_parse_any_prefers_recorder_naming() -> None:
    from src.modules.clipping.recording_matcher import parse_any_recording_filename

    # 录播姬命名优先走专用解析（带房间号与标题）
    parsed = parse_any_recording_filename("录制-10000-20261104-232239-540-测试.flv")
    assert parsed is not None
    assert parsed.room_id == "10000"
    assert parsed.title == "测试"
    # 通用命名兜第二位
    generic = parse_any_recording_filename("2026-11-08 20-00-05.mkv")
    assert generic is not None
    assert generic.room_id == ""
    # 都不认识 → None
    assert parse_any_recording_filename("手工改名.mp4") is None


def test_scan_splits_parsed_and_unmatched_and_excludes_output_dir(tmp_path: Path) -> None:
    parsed_file = _make_file(tmp_path, "录制-10000-20261104-232239-540-测试.flv")
    subdir = tmp_path / "原始录播"
    subdir.mkdir()
    nested = _make_file(subdir, "录制-10000-20261104-232756-448-测试.mp4")
    renamed = _make_file(tmp_path, "Maicraft直播-20261105-烤海带.mp4")
    ignored = _make_file(tmp_path, "notes.txt")

    output_dir = tmp_path / "切片"
    output_dir.mkdir()
    product = _make_file(output_dir, "录制-10000-20261104-232239-540-假产物.flv")

    scan = scan_recording_dir(tmp_path, exclude_dirs=[output_dir])
    parsed_paths = {f.path for f in scan.parsed}
    assert parsed_paths == {parsed_file, nested}
    assert renamed in scan.unmatched
    assert ignored not in scan.unmatched
    assert product not in scan.unmatched  # 输出目录整棵被排除
    # 已按开始时刻排序
    starts = [f.started_at_ms for f in scan.parsed]
    assert starts == sorted(starts)


def test_match_files_to_sessions_single_and_multi(tmp_path: Path) -> None:
    started = int(datetime(2026, 11, 4, 20, 0).timestamp() * 1000)
    ended = started + 3 * 3600 * 1000
    session = SessionWindow(live_session_id=1, started_at_ms=started, ended_at_ms=ended)

    def file_with_start(hour: int, minute: int, name: str) -> RecordingFile:
        ms = int(datetime(2026, 11, 4, hour, minute).timestamp() * 1000)
        return RecordingFile(path=Path(name), room_id="10000", started_at_ms=ms, title="t")

    f1 = file_with_start(20, 0, "a.flv")
    f2 = file_with_start(21, 30, "b.flv")
    before = file_with_start(19, 55, "c.flv")  # 提前 5 分钟：在余量内，归这场
    other = file_with_start(23, 5, "d.flv")  # 下播之后：不归这场

    matched = match_files_to_sessions([f2, f1, before, other], [session])
    assert matched[1] == [before, f1, f2]  # 按开始时刻排序

    # 进行中场次（ended=None）：开播之后开始的文件都归它
    ongoing = SessionWindow(live_session_id=2, started_at_ms=started, ended_at_ms=None)
    matched_ongoing = match_files_to_sessions([f1, f2, other], [ongoing])
    assert matched_ongoing[2] == [f1, f2, other]


def test_match_no_candidates_leaves_file_out() -> None:
    file = RecordingFile(path=Path("x.flv"), room_id="1", started_at_ms=10**12, title="t")
    session = SessionWindow(live_session_id=1, started_at_ms=10**12 + 3600 * 1000, ended_at_ms=10**12 + 7200 * 1000)
    assert match_files_to_sessions([file], [session]) == {}
