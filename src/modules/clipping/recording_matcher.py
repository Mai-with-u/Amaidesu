"""录播文件名解析与场次时间窗匹配。

匹配只依赖一件事：文件名里带**录制开始时刻**（本地时间）。支持两类命名：

- 录播姬（BililiveRecorder）原始命名
  ``录制-<房间号>-<YYYYMMDD>-<HHMMSS>-<毫秒>-<标题>.<flv|mp4>``；
- OBS 等通用录制软件的日期时间命名
  ``YYYY-MM-DD HH-MM-SS.<ext>``（分隔符兼容空格/连字符/下划线，可带
  `` (N)`` 分片序号），房间号缺省为空。

两种都解析不出来的文件（手工改名归档等）单独返回，供 WebUI 手动查看。
一场直播可能对应多个文件（断流重录、分段各带自己的开始时刻），匹配结果
按场次聚合、按开始时刻排序；切割时的文件内偏移 = 段时刻 − 文件开始时刻。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.modules.logging import get_logger

logger = get_logger("RecordingMatcher")

# 录播姬原始文件名（标题段可能自带连字符，用贪婪匹配兜到最后一个扩展名前）
_FILENAME_RE = re.compile(
    r"^录制-(?P<room>\d+)-(?P<date>\d{8})-(?P<time>\d{6})-(?P<ms>\d{1,3})-(?P<title>.+)\.(?P<ext>flv|mp4)$"
)

# 通用日期时间命名（OBS 默认形态）：日期与时刻内部分隔符为 -/_，日期与时刻
# 之间为空格/连字符/下划线；可选 " (N)" 分片序号（同刻多文件，按序号定先后）
_GENERIC_NAME_RE = re.compile(
    r"^(?P<date>\d{4}[-._]\d{2}[-._]\d{2})[ _-](?P<time>\d{2}[-._]\d{2}[-._]\d{2})"
    r"(?:[ ._-]\((?P<part>\d+)\))?\.(?P<ext>flv|mp4|mkv|mov|ts|avi)$"
)

# 文件开始时刻允许早于开播的余量（录制软件可能在开播信号前后一点启动）
_SESSION_MATCH_MARGIN_MS = 10 * 60 * 1000

# 参与匹配的视频扩展名
_VIDEO_EXTS = {".flv", ".mp4", ".mkv", ".mov", ".ts", ".avi"}


@dataclass
class RecordingFile:
    """一个解析成功的录播文件：文件系统事实 + 文件名里的开始时刻。"""

    path: Path
    room_id: str
    started_at_ms: int
    title: str
    # 同刻分片的先后序号（OBS 手动切分 "(2)" 等；无分片为 0）。只用于排序，
    # 不参与时刻换算——分片没有各自的开始时刻
    sort_index: int = 0


@dataclass
class SessionWindow:
    """匹配用的场次时间窗（从 live_sessions 读面摘出，避免依赖完整模型）。"""

    live_session_id: int
    started_at_ms: int
    ended_at_ms: Optional[int]  # None = 进行中


@dataclass
class RecordingScan:
    """一次目录扫描的结果：解析成功的文件与解析不了的文件。"""

    parsed: List[RecordingFile]
    unmatched: List[Path]


def parse_recording_filename(name: str) -> Optional[RecordingFile]:
    """解析录播姬原始文件名；不是该形态返回 None。

    文件名时刻按本地时区解释成墙钟毫秒（与 live_sessions 的时间戳同一坐标系）。
    """
    match = _FILENAME_RE.fullmatch(name)
    if match is None:
        return None
    try:
        naive = datetime.strptime(
            f"{match.group('date')}-{match.group('time')}-{match.group('ms')}", "%Y%m%d-%H%M%S-%f"
        )
    except ValueError:  # 形如日期但不合法（13 月 32 日）：当解析不了
        return None
    return RecordingFile(
        path=Path(name),
        room_id=match.group("room"),
        started_at_ms=int(naive.timestamp() * 1000),
        title=match.group("title"),
    )


def parse_generic_recording_filename(name: str) -> Optional[RecordingFile]:
    """解析通用日期时间命名（OBS 默认形态等）；不匹配返回 None。

    ``2026-11-08 20-00-00.mkv`` / ``2026-11-08_20-00-00.mp4`` /
    ``2026-11-08 20-00-00 (2).mp4`` 式的分片序号只进排序字段，开始时刻取
    文件名里的日期时间。房间号这类信息通用命名里没有，留空。
    """
    match = _GENERIC_NAME_RE.fullmatch(name)
    if match is None:
        return None
    date_text = match.group("date").replace("-", "").replace("_", "").replace(".", "")
    time_text = match.group("time").replace("-", "").replace("_", "").replace(".", "")
    try:
        naive = datetime.strptime(f"{date_text}-{time_text}", "%Y%m%d-%H%M%S")
    except ValueError:  # 形如日期但不合法（13 月 32 日）：当解析不了
        return None
    return RecordingFile(
        path=Path(name),
        room_id="",
        started_at_ms=int(naive.timestamp() * 1000),
        title="",
        sort_index=int(match.group("part") or 0),
    )


def parse_any_recording_filename(name: str) -> Optional[RecordingFile]:
    """依次尝试录播姬命名与通用日期时间命名，都失败返回 None。"""
    return parse_recording_filename(name) or parse_generic_recording_filename(name)


def scan_recording_dir(root: Path, *, exclude_dirs: Sequence[Path] = ()) -> RecordingScan:
    """递归扫描录播目录下的视频文件（排除指定子树，如切片输出目录）。"""
    excluded = {item.resolve() for item in exclude_dirs}
    parsed: List[RecordingFile] = []
    unmatched: List[Path] = []
    if not root.is_dir():
        logger.warning(f"录播目录不存在或不是目录: {root}")
        return RecordingScan(parsed=parsed, unmatched=unmatched)
    for item in sorted(root.rglob("*")):
        if not item.is_file() or item.suffix.lower() not in _VIDEO_EXTS:
            continue
        try:
            resolved = item.resolve()
        except OSError:  # 文件在扫描间隙被移动/删除：跳过这一项
            continue
        if any(resolved == excluded_path or excluded_path in resolved.parents for excluded_path in excluded):
            continue
        info = parse_any_recording_filename(item.name)
        if info is None:
            unmatched.append(item)
        else:
            info.path = item
            parsed.append(info)
    parsed.sort(key=lambda f: (f.started_at_ms, f.sort_index))
    return RecordingScan(parsed=parsed, unmatched=unmatched)


def file_covers_session_start(file: RecordingFile, session: SessionWindow) -> bool:
    """文件开始时刻是否落在该场的时间窗内（允许提前余量；进行中的场次不设上界）。"""
    upper = session.ended_at_ms if session.ended_at_ms is not None else float("inf")
    return session.started_at_ms - _SESSION_MATCH_MARGIN_MS <= file.started_at_ms <= upper


def match_files_to_sessions(
    files: Sequence[RecordingFile], sessions: Sequence[SessionWindow]
) -> Dict[int, List[RecordingFile]]:
    """把录播文件按时间窗归到场次，返回 场次id → 文件列表（按开始时刻排序）。

    一个文件只归一个场次（时间窗原则上不重叠）；同时落在多场窗内时归开始
    更早的那场。没有归属的文件不出现在结果里（unmatched 名单由扫描返回）。
    """
    result: Dict[int, List[RecordingFile]] = {}
    for file in files:
        candidates = [s for s in sessions if file_covers_session_start(file, s)]
        if not candidates:
            continue
        best = min(candidates, key=lambda s: s.started_at_ms)
        result.setdefault(best.live_session_id, []).append(file)
    for file_list in result.values():
        file_list.sort(key=lambda f: (f.started_at_ms, f.sort_index))
    return result
