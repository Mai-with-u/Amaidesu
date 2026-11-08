"""ffmpeg 子进程切割。

一个切片段按其覆盖的录播文件拆成若干份（一场多文件时逐文件切，产物名带
部分序号）。默认流复制（``-c copy``，秒级出片，边界按关键帧对齐可能偏差
几秒）；配置切重编码时边界精确到帧但速度接近实时。

ffmpeg 输出不走管道（Windows 管道解码易踩 GBK 坑），重定向到日志文件，
失败时取日志尾部作为错误信息。子进程挂起用"时长推算的超时 + 两段式终止"
兜底（先 terminate，5 秒不退再 kill，参照 vite_dev 的两段式）。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
from typing import List, Sequence

from src.modules.clipping.recording_matcher import RecordingFile
from src.modules.logging import get_logger

logger = get_logger("ClipCutter")

# 两段式终止的第二段时限
_TERMINATE_GRACE_S = 5.0


class ClipCutError(Exception):
    """切割失败（ffmpeg 非零退出、超时被杀等），携带可读原因。"""


@dataclass
class CutPortion:
    """一个切片段在某文件上的切割任务：文件内偏移与时长（秒）。"""

    file: RecordingFile
    offset_s: float
    duration_s: float
    part_index: int  # 跨文件时的部分序号（从 1 起；单文件段为 0 表示不带序号）


def plan_portions(segment_start_ms: int, segment_end_ms: int, files: Sequence[RecordingFile]) -> List[CutPortion]:
    """把墙钟毫秒段拆到各重叠文件上（文件按开始时刻排序）。

    非末位文件切到下一文件开始为止；末位文件时长超出实际文件长度时由
    ffmpeg 在文件结尾自然截断。
    """
    portions: List[CutPortion] = []
    ordered = sorted(files, key=lambda f: (f.started_at_ms, f.sort_index))
    for index, file in enumerate(ordered):
        next_start = ordered[index + 1].started_at_ms if index + 1 < len(ordered) else None
        overlap_start = max(segment_start_ms, file.started_at_ms)
        overlap_end = segment_end_ms if next_start is None else min(segment_end_ms, next_start)
        if overlap_end <= overlap_start:
            continue
        portions.append(
            CutPortion(
                file=file,
                offset_s=(overlap_start - file.started_at_ms) / 1000,
                duration_s=(overlap_end - overlap_start) / 1000,
                part_index=0 if len(ordered) == 1 else len(portions) + 1,
            )
        )
    return portions


def safe_filename_part(text: str, *, limit: int = 40) -> str:
    """标题清理成文件名安全片段（去 Windows 非法字符与控制符，截长）。"""
    cleaned = "".join(ch for ch in text if ch not in '<>:"/\\|?*' and ord(ch) >= 32).strip().strip(".")
    return cleaned[:limit]


def output_filename_for(*, segment_start_ms: int, title: str, index: int, part_index: int = 0) -> str:
    """切片产物文件名：``日期-时刻-序号-标题[-部分N].mp4``。"""
    stamp = datetime.fromtimestamp(segment_start_ms / 1000).strftime("%Y%m%d-%H%M%S")
    parts = [stamp, f"{index:02d}", safe_filename_part(title) or "切片"]
    if part_index:
        parts.append(f"部分{part_index}")
    return "-".join(parts) + ".mp4"


class ClipCutter:
    """ffmpeg 切割执行器（asyncio 子进程，输出重定向日志文件）。"""

    def __init__(self, *, ffmpeg_path: str = "", reencode: bool = False) -> None:
        self._ffmpeg = ffmpeg_path.strip() or "ffmpeg"
        self._reencode = reencode

    def _build_args(self, portion: CutPortion, output: Path) -> List[str]:
        args = [self._ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        args += ["-ss", f"{portion.offset_s:.3f}", "-i", str(portion.file.path)]
        args += ["-t", f"{portion.duration_s:.3f}"]
        if self._reencode:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac"]
        else:
            args += ["-c", "copy"]
        args += ["-avoid_negative_ts", "make_zero", str(output)]
        return args

    async def cut(self, portion: CutPortion, output: Path, *, log_dir: Path) -> Path:
        """切一份并返回产物路径；失败抛 ClipCutError（错误信息含日志尾部）。"""
        output.parent.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{output.stem}.ffmpeg.log"
        args = self._build_args(portion, output)
        logger.debug(f"切割 {' '.join(args)}")

        # 超时按时长推算：流复制远快于实时，重编码近似实时，都给 4 倍 + 5 分钟余量
        timeout_s = portion.duration_s * 4 + 300
        log_fh = await asyncio.to_thread(open, log_path, "w", encoding="utf-8", errors="replace")
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=log_fh,
                    stderr=log_fh,
                )
            except FileNotFoundError as exc:
                raise ClipCutError(
                    f"找不到 ffmpeg 可执行文件（{self._ffmpeg}）；请在配置 [clipping].ffmpeg_path 填本地路径"
                ) from exc
            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=timeout_s)
            except asyncio.TimeoutError as exc:
                await self._terminate(proc)
                raise ClipCutError(f"ffmpeg 超过 {timeout_s:.0f} 秒未完成，已强制终止（日志：{log_path}）") from exc
        finally:
            await asyncio.to_thread(log_fh.close)
        if returncode != 0:
            raise ClipCutError(f"ffmpeg 退出码 {returncode}：{self._log_tail(log_path)}")
        if not output.is_file() or output.stat().st_size == 0:
            raise ClipCutError(f"ffmpeg 正常退出但没有产物（日志：{log_path}）")
        return output

    async def remux_to_mp4(self, source: Path, output: Path, *, log_dir: Path) -> Path:
        """整文件流复制重封装成 mp4（预览缓存用）：不重编码，速度即磁盘读写速度。

        ``+faststart`` 把索引挪到文件头，浏览器拿到前几百 KB 就能开播与拖动。
        """
        output.parent.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{output.stem}.ffmpeg.log"
        args = [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        logger.debug(f"预览重封装 {' '.join(args)}")
        # 重封装是 I/O 限速：按源文件大小给超时（每 100MB 约 60 秒的保守余量）
        try:
            size_mb = source.stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 1024
        timeout_s = size_mb * 0.6 + 600
        log_fh = await asyncio.to_thread(open, log_path, "w", encoding="utf-8", errors="replace")
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=log_fh,
                    stderr=log_fh,
                )
            except FileNotFoundError as exc:
                raise ClipCutError(
                    f"找不到 ffmpeg 可执行文件（{self._ffmpeg}）；请在配置 [clipping].ffmpeg_path 填本地路径"
                ) from exc
            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=timeout_s)
            except asyncio.TimeoutError as exc:
                await self._terminate(proc)
                raise ClipCutError(f"预览重封装超过 {timeout_s:.0f} 秒未完成，已强制终止（日志：{log_path}）") from exc
        finally:
            await asyncio.to_thread(log_fh.close)
        if returncode != 0:
            raise ClipCutError(f"预览重封装退出码 {returncode}：{self._log_tail(log_path)}")
        if not output.is_file() or output.stat().st_size == 0:
            raise ClipCutError(f"预览重封装正常退出但没有产物（日志：{log_path}）")
        return output

    async def remux_range_to_mp4(
        self, source: Path, output: Path, *, start_s: float, duration_s: float, log_dir: Path
    ) -> Path:
        """只重封装源文件的一段（流复制，秒级）——段级预览用，免等整场转换。"""
        output.parent.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{output.stem}.ffmpeg.log"
        args = [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_s:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration_s:.3f}",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        logger.debug(f"段级预览重封装 {' '.join(args)}")
        timeout_s = duration_s * 4 + 120
        log_fh = await asyncio.to_thread(open, log_path, "w", encoding="utf-8", errors="replace")
        try:
            try:
                proc = await asyncio.create_subprocess_exec(*args, stdout=log_fh, stderr=log_fh)
            except FileNotFoundError as exc:
                raise ClipCutError(
                    f"找不到 ffmpeg 可执行文件（{self._ffmpeg}）；请在配置 [clipping].ffmpeg_path 填本地路径"
                ) from exc
            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=timeout_s)
            except asyncio.TimeoutError as exc:
                await self._terminate(proc)
                raise ClipCutError(
                    f"段级预览重封装超过 {timeout_s:.0f} 秒未完成，已强制终止（日志：{log_path}）"
                ) from exc
        finally:
            await asyncio.to_thread(log_fh.close)
        if returncode != 0:
            raise ClipCutError(f"段级预览重封装退出码 {returncode}：{self._log_tail(log_path)}")
        if not output.is_file() or output.stat().st_size == 0:
            raise ClipCutError(f"段级预览重封装正常退出但没有产物（日志：{log_path}）")
        return output

    def _ffprobe_path(self) -> str:
        """ffprobe 与 ffmpeg 同目录（配置了路径时）；否则用 PATH 上的。"""
        configured = self._ffmpeg.strip()
        if configured and configured.lower() != "ffmpeg":
            sibling = Path(configured).with_name("ffprobe.exe" if configured.lower().endswith(".exe") else "ffprobe")
            if sibling.is_file():
                return str(sibling)
        return "ffprobe"

    async def probe_creation_time_ms(self, source: Path) -> Optional[int]:
        """读容器元数据里的录制时刻（ffprobe creation_time；缺失/解析失败返回 None）。"""
        args = [
            self._ffprobe_path(),
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_entries",
            "format_tags=creation_time",
            str(source),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (FileNotFoundError, asyncio.TimeoutError, OSError) as exc:
            logger.debug(f"ffprobe 读取元数据失败（按无时刻处理）：{exc}")
            return None
        try:
            payload = json.loads(stdout.decode("utf-8", errors="replace"))
            raw = payload["format"]["tags"]["creation_time"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.debug(f"元数据里没有可用的 creation_time：{exc}")
            return None
        try:
            moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            logger.debug(f"creation_time 无法解析：{raw!r}")
            return None
        if moment.tzinfo is None:
            moment = moment.astimezone(timezone.utc).astimezone()  # 无时区标记按 UTC 存储的惯例解读
        return int(moment.timestamp() * 1000)

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        """两段式终止：先 terminate，5 秒不退再 kill。"""
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_S)
        except asyncio.TimeoutError:
            logger.warning("ffmpeg 未在 5s 内退出，强制 kill")
            proc.kill()
            await proc.wait()

    @staticmethod
    def _log_tail(log_path: Path, *, lines: int = 15) -> str:
        """取 ffmpeg 日志尾部作为错误信息；读不到就只给路径。"""
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            return "\n".join(content[-lines:])
        except OSError:
            return f"（日志读取失败：{log_path}）"


__all__ = ["ClipCutter", "ClipCutError", "CutPortion", "output_filename_for", "plan_portions", "safe_filename_part"]
