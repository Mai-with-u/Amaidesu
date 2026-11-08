"""ffmpeg 切割执行器：段拆份、产物命名、子进程执行（假 ffmpeg）与失败路径。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

from src.modules.clipping.cutter import (
    ClipCutter,
    ClipCutError,
    output_filename_for,
    plan_portions,
    safe_filename_part,
)
from src.modules.clipping.recording_matcher import RecordingFile

_SESSION_START = 1_000_000


def _file(name: str, started_at_ms: int) -> RecordingFile:
    return RecordingFile(path=Path(name), room_id="1", started_at_ms=started_at_ms, title="t")


def test_plan_portions_single_file() -> None:
    files = [_file("a.flv", _SESSION_START)]
    portions = plan_portions(_SESSION_START + 10_000, _SESSION_START + 70_000, files)
    assert len(portions) == 1
    assert portions[0].offset_s == pytest.approx(10.0)
    assert portions[0].duration_s == pytest.approx(60.0)
    assert portions[0].part_index == 0


def test_plan_portions_spanning_two_files() -> None:
    files = [_file("a.flv", _SESSION_START), _file("b.flv", _SESSION_START + 60_000)]
    portions = plan_portions(_SESSION_START + 50_000, _SESSION_START + 120_000, files)
    assert [(p.file.path.name, round(p.offset_s, 3), round(p.duration_s, 3), p.part_index) for p in portions] == [
        ("a.flv", 50.0, 10.0, 1),
        ("b.flv", 0.0, 60.0, 2),
    ]


def test_plan_portions_no_overlap_yields_nothing() -> None:
    files = [_file("a.flv", _SESSION_START + 3600_000)]
    assert plan_portions(_SESSION_START, _SESSION_START + 60_000, files) == []


def test_output_filename_and_safe_part() -> None:
    assert safe_filename_part('a<b>c:"d/\\|?*e') == "abcde"
    name = output_filename_for(segment_start_ms=_SESSION_START, title="烤海带", index=3)
    stamp = datetime.fromtimestamp(_SESSION_START / 1000).strftime("%Y%m%d-%H%M%S")
    assert name == f"{stamp}-03-烤海带.mp4"
    # 跨文件部分带序号
    part_name = output_filename_for(segment_start_ms=_SESSION_START, title="x", index=1, part_index=2)
    assert "部分2" in part_name
    # 标题全被清掉时兜底"切片"
    assert output_filename_for(segment_start_ms=_SESSION_START, title="<>", index=1).endswith("-切片.mp4")


def _write_fake_ffmpeg(dir_: Path, *, exit_code: int = 0) -> Path:
    """造一个假 ffmpeg：把最后一个参数当成输出路径写个文件（或直接按退出码失败）。

    Windows 用 .bat 转发到 python 脚本（CreateProcess 不直接认 .py）；POSIX 直接
    用可执行的 python shebang 脚本。
    """
    script = dir_ / "fake_ffmpeg.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"if {exit_code} != 0:\n"
        "    print('模拟切割失败', file=sys.stderr)\n"
        f"    sys.exit({exit_code})\n"
        "Path(sys.argv[-1]).write_bytes(b'fake-clip')\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        fake = dir_ / "fake-ffmpeg.bat"
        fake.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        fake = dir_ / "fake-ffmpeg"
        fake.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{script}' \"$@\"\n", encoding="utf-8")
        fake.chmod(0o755)
    return fake


async def test_cut_writes_output_via_fake_ffmpeg(tmp_path: Path) -> None:
    source = tmp_path / "src.flv"
    source.write_bytes(b"video")
    cutter = ClipCutter(ffmpeg_path=str(_write_fake_ffmpeg(tmp_path)))
    portion = plan_portions(_SESSION_START, _SESSION_START + 30_000, [_file(source, _SESSION_START)])[0]
    output = tmp_path / "out" / "a.mp4"
    produced = await cutter.cut(portion, output, log_dir=tmp_path / "logs")
    assert produced == output
    assert output.read_bytes() == b"fake-clip"


async def test_cut_missing_ffmpeg_raises_friendly(tmp_path: Path) -> None:
    cutter = ClipCutter(ffmpeg_path=str(tmp_path / "不存在的ffmpeg.exe"))
    portion = plan_portions(_SESSION_START, _SESSION_START + 30_000, [_file("a.flv", _SESSION_START)])[0]
    with pytest.raises(ClipCutError, match="ffmpeg_path"):
        await cutter.cut(portion, tmp_path / "a.mp4", log_dir=tmp_path)


async def test_cut_nonzero_exit_raises_with_log_tail(tmp_path: Path) -> None:
    source = tmp_path / "src.flv"
    source.write_bytes(b"video")
    cutter = ClipCutter(ffmpeg_path=str(_write_fake_ffmpeg(tmp_path, exit_code=1)))
    portion = plan_portions(_SESSION_START, _SESSION_START + 30_000, [_file(source, _SESSION_START)])[0]
    with pytest.raises(ClipCutError, match="模拟切割失败"):
        await cutter.cut(portion, tmp_path / "a.mp4", log_dir=tmp_path / "logs")
