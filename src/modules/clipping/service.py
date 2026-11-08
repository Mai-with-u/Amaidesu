"""切片编排：下播自动选段、WebUI 触发的选段与切割、预览缓存、后台任务登记。

订阅 ``live.ended`` 实现下播自动选段（enabled 且录播目录已配置且匹配到文件
才跑）；选段与切割都经 ``BackgroundTaskRegistry`` 登记进度（前端底栏可见）。
切割时长坐标换算：墙钟毫秒段 → 文件内偏移（matcher 给出文件开始时刻）→
ffmpeg ``-ss/-t`` 秒参数。

预览：浏览器播不了 flv/mkv 容器，非 mp4 录播在后台流复制重封装成 mp4 缓存
（``data/clipping/preview/``，缓存键 = 文件名 + mtime + 大小），mp4 直服。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import asyncio
from typing import Dict, List, Optional, Sequence, Tuple

from src.modules.background.registry import BackgroundTaskRegistry, get_registry
from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.cutter import ClipCutError, ClipCutter, output_filename_for, plan_portions
from src.modules.clipping.recording_matcher import (
    RecordingFile,
    RecordingScan,
    SessionWindow,
    match_files_to_sessions,
    parse_any_recording_filename,
    scan_recording_dir,
)
from src.modules.clipping.overrides import RecordingOverride, RecordingOverrideStore
from src.modules.clipping.selector import ClipSelector, ClipSelectionError
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload
from src.modules.logging import get_logger
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.clipping import ClipSegment, NewClipSegment
from src.modules.storage.repos.clips import ClipRepo
from src.modules.utils.task_utils import spawn_background_task

logger = get_logger("ClippingService")

# ffmpeg 日志目录（项目 logs/ 下，与其他诊断产物同级）
_FFMPEG_LOG_DIR = Path("logs") / "clipping"

# 改名时文件名不允许出现的字符（Windows 保留字符集）
_FILENAME_FORBIDDEN_CHARS = set(r'<>:"/\|?*')
# 预览缓存目录（data/clipping/ 子目录，与归属覆盖同域；整目录可随时删除重建）
_PREVIEW_CACHE_DIR = Path("data") / "clipping" / "preview"


class PreviewSourceError(Exception):
    """预览源不可用（路径越界、不存在等）。"""


@dataclass
class RecordedSessionInfo:
    """切片页场次列表的一行：场次基本信息 + 匹配到的录播文件数。"""

    live_session_id: int
    started_at_ms: int
    ended_at_ms: Optional[int]
    title: str
    file_count: int


class ClippingService:
    """切片功能编排器（组合根按 ``[clipping].enabled`` 装配；未启用零装配）。"""

    def __init__(
        self,
        *,
        event_bus: EventBus,
        database: SQLiteDatabase,
        llm_manager: object,
        config: ClippingConfigSchema,
        task_registry: Optional[BackgroundTaskRegistry] = None,
        cutter: Optional[ClipCutter] = None,
        override_store: Optional[RecordingOverrideStore] = None,
    ) -> None:
        self._event_bus = event_bus
        self._database = database
        self._config = config
        self._task_registry = task_registry
        self._subscribed = False
        self._selector: Optional[ClipSelector] = None
        if llm_manager is not None:
            self._selector = ClipSelector(
                llm_manager,  # type: ignore[arg-type]
                chat_repo=database.chat,
                event_repo=database.events,
                session_repo=database.sessions,
                topic_repo=database.topics,
                config=config,
            )
        self._cutter = (
            cutter if cutter is not None else ClipCutter(ffmpeg_path=config.ffmpeg_path, reencode=config.reencode)
        )
        # fire-and-forget 后台任务（自动选段/切割/预览转换）的强引用持有（spawn_background_task 要求）
        self._background_tasks: set = set()
        # 预览转换中的缓存键（去重防重复发起；转换中不算缓存就绪）
        self._preview_inflight: set = set()
        # 归属人工覆盖（本地 JSON，不入库）
        self._overrides = override_store if override_store is not None else RecordingOverrideStore()
        # 段级预览的并发锁（同一段并发请求共享同一次重封装，不并发写同一缓存文件）
        self._range_locks: Dict[str, asyncio.Lock] = {}

    # ---------- 生命周期 ----------

    @property
    def clips_repo(self) -> ClipRepo:
        """切片段仓储（API 层的段增删改查走这里）。"""
        return self._database.clips

    @property
    def recording_dir(self) -> str:
        """录播目录配置原值（API 层判"未配置"用）。"""
        return self._config.recording_dir.strip()

    @property
    def enabled(self) -> bool:
        """功能是否可用（装配开关 + 录播目录已配置）。"""
        return self._config.enabled and bool(self._config.recording_dir.strip())

    @property
    def output_dir(self) -> Path:
        """切片输出目录（配置留空 = 录播目录下的 切片 子目录）。"""
        configured = self._config.output_dir.strip()
        if configured:
            return Path(configured)
        return Path(self._config.recording_dir.strip()) / "切片"

    def start(self) -> None:
        """订阅下播事件（未配置录播目录时不订阅，只在日志里说明）。"""
        if self._subscribed:
            return
        if not self._config.recording_dir.strip():
            logger.warning("[clipping].enabled=true 但未配置 recording_dir，下播自动选段不生效（切片页同样不可用）")
            return
        self._event_bus.on(CoreEvents.LIVE_ENDED, self._on_live_ended, model_class=LiveEndedPayload)
        self._subscribed = True
        logger.info("ClippingService 已订阅 live.ended（下播后自动 AI 选段）")

    def stop(self) -> None:
        """摘除订阅（进程收口用）。"""
        if not self._subscribed:
            return
        self._event_bus.off(CoreEvents.LIVE_ENDED, self._on_live_ended)
        self._subscribed = False

    async def stop_async(self) -> None:
        """stop() 的异步壳（关闭链统一 await 形态）。"""
        self.stop()

    async def _on_live_ended(self, payload: LiveEndedPayload) -> None:
        """下播后自动选段（fire-and-forget；空场次已被丢弃，不处理）。"""
        if payload.empty_discarded:
            return
        spawn_background_task(
            self._auto_select(payload.live_session_id),
            logger=logger,
            tasks=self._background_tasks,
            label=f"clip-auto-select-{payload.live_session_id}",
        )

    async def _auto_select(self, live_session_id: int) -> None:
        """自动选段：匹配到录播文件才跑 AI 选段。"""
        try:
            files, _ = self._scan_and_match_one(live_session_id)
            if not files:
                logger.info(f"场次 {live_session_id} 下播，但录播目录里没有匹配到文件，跳过自动选段")
                return
            count = await self.select_segments(live_session_id=live_session_id)
            logger.info(f"场次 {live_session_id} 下播自动选段完成，AI 提议 {count} 段")
        except Exception as exc:  # noqa: BLE001 - 自动选段失败不影响下播流程，记日志即可
            logger.error(f"场次 {live_session_id} 下播自动选段失败：{exc}", exc=exc)

    # ---------- 场次 × 录播文件 ----------

    def _scan(self) -> RecordingScan:
        """扫描录播目录（排除切片输出目录，避免把产物当素材）。"""
        return scan_recording_dir(Path(self._config.recording_dir.strip()), exclude_dirs=[self.output_dir])

    async def _session_windows(self) -> List[SessionWindow]:
        """全部场次的匹配时间窗（历史场次 + 进行中场次，按开始时间倒序）。"""
        rows = await self._database.sessions.list_live_sessions(limit=1000)
        return [
            SessionWindow(live_session_id=row.id, started_at_ms=row.started_at_ms, ended_at_ms=row.ended_at_ms)
            for row in rows
        ]

    def _apply_overrides(
        self,
        live_session_id: int,
        scan: RecordingScan,
        auto_matched: List[RecordingFile],
    ) -> Tuple[List[RecordingFile], List[RecordingFile]]:
        """把人工覆盖叠加到自动匹配上：detach 的移出，assign 的注入。

        assign 的文件若在扫描结果里（解析得出时刻）沿用文件名时刻；解析不出
        的（手工改名归档）用人工给的时刻构造。返回 (生效文件, 被移出的文件)。
        """
        detached_paths: set[str] = set()
        manual: List[RecordingOverride] = []
        for override in self._overrides.for_session(live_session_id):
            if override.action == "detach":
                detached_paths.add(override.path)
            elif override.action == "assign":
                manual.append(override)

        effective = [f for f in auto_matched if str(f.path) not in detached_paths]
        known: dict[str, RecordingFile] = {str(f.path): f for f in scan.parsed}
        assigned: List[RecordingFile] = []
        for override in manual:
            existing = known.get(override.path)
            if existing is not None:
                assigned.append(existing)
                continue
            if override.start_ms is None:
                logger.warning(f"人工指派的文件解析不出时刻且未提供开始时刻，跳过：{override.path}")
                continue
            assigned.append(
                RecordingFile(
                    path=Path(override.path),
                    room_id="",
                    started_at_ms=int(override.start_ms),
                    title="",
                )
            )
        effective.extend(assigned)
        effective.sort(key=lambda f: (f.started_at_ms, f.sort_index))
        detached = [f for f in auto_matched if str(f.path) in detached_paths]
        return effective, detached

    async def _scan_and_match_one(self, live_session_id: int) -> Tuple[List[RecordingFile], RecordingScan]:
        """扫描目录并返回指定场次的生效文件（自动匹配 + 人工覆盖）。"""
        scan = self._scan()
        session = await self._database.sessions.get_live_session(live_session_id=live_session_id)
        if session is None:
            return [], scan
        matched = match_files_to_sessions(
            scan.parsed,
            [
                SessionWindow(
                    live_session_id=session.id, started_at_ms=session.started_at_ms, ended_at_ms=session.ended_at_ms
                )
            ],
        )
        effective, _detached = self._apply_overrides(live_session_id, scan, matched.get(live_session_id, []))
        return effective, scan

    async def assign_recording(self, *, live_session_id: int, source_path: str, start_ms: Optional[int]) -> None:
        """人工把一个录播文件指派到某场（覆盖存储持久化）。"""
        source = self.resolve_preview_source(source_path)  # 同一套"必须在录播目录内"校验
        parsed = parse_any_recording_filename(source.name)
        effective_start = start_ms if start_ms is not None else (parsed.started_at_ms if parsed else None)
        if effective_start is None:
            raise PreviewSourceError("该文件名解析不出开始时刻，请填写录制开始时刻")
        self._overrides.upsert(
            RecordingOverride(
                path=str(source), live_session_id=live_session_id, action="assign", start_ms=effective_start
            )
        )

    async def detach_recording(self, *, live_session_id: int, source_path: str) -> None:
        """人工把一个文件移出某场（撤销自动匹配）。"""
        source = self.resolve_preview_source(source_path)
        self._overrides.upsert(RecordingOverride(path=str(source), live_session_id=live_session_id, action="detach"))

    async def rename_recording(self, *, source_path: str, new_name: str) -> Path:
        """在 WebUI 里把录播文件改名（同目录、扩展名不可改），并迁移其归属覆盖。

        改成录播姬命名格式后文件即恢复自动匹配能力。
        """
        source = self.resolve_preview_source(source_path)
        cleaned = new_name.strip()
        if not cleaned:
            raise PreviewSourceError("新文件名不能为空")
        if any(ch in cleaned for ch in _FILENAME_FORBIDDEN_CHARS) or any(ord(ch) < 32 for ch in cleaned):
            raise PreviewSourceError("新文件名含非法字符")
        if source.suffix and not cleaned.lower().endswith(source.suffix.lower()):
            raise PreviewSourceError(f"扩展名不能改（须保留 {source.suffix}）")
        target = source.parent / cleaned
        if target.exists():
            raise PreviewSourceError(f"同名文件已存在：{cleaned}")
        try:
            os.replace(source, target)
        except OSError as exc:
            raise PreviewSourceError(f"改名失败（文件被占用？）：{exc}") from exc
        self._overrides.replace_path(str(source), str(target))
        logger.info(f"录播文件已改名：{source.name} → {target.name}")
        return target

    async def source_creation_time_ms(self, source_path: str) -> Optional[int]:
        """读一个录播文件容器元数据里的录制时刻（没有则 None）。"""
        source = self.resolve_preview_source(source_path)
        return await self._cutter.probe_creation_time_ms(source)

    async def restore_recording(self, *, live_session_id: int, source_path: str) -> None:
        """撤销一条人工覆盖（恢复自动匹配结果）。"""
        source = self.resolve_preview_source(source_path)
        removed_assign = self._overrides.drop(
            RecordingOverride(path=str(source), live_session_id=live_session_id, action="assign")
        )
        removed_detach = self._overrides.drop(
            RecordingOverride(path=str(source), live_session_id=live_session_id, action="detach")
        )
        if not (removed_assign or removed_detach):
            raise PreviewSourceError("该文件没有人工覆盖记录，无需恢复")

    async def list_recorded_sessions(self, *, limit: int = 100) -> List[RecordedSessionInfo]:
        """切片页场次列表：有（自动匹配或人工指派）录播文件的场次（按开始时间倒序）。"""
        scan = self._scan()
        windows = await self._session_windows()
        matched = match_files_to_sessions(scan.parsed, windows)
        # 人工指派也算数：只被指派过、自动匹配不到的场次也要出现在列表里
        manual_counts: dict[int, int] = {}
        for override in self._overrides.load():
            if override.action != "assign":
                continue
            auto = matched.get(override.live_session_id, [])
            if any(str(f.path) == override.path for f in auto):
                continue  # 已在自动匹配里，不重复计
            manual_counts[override.live_session_id] = manual_counts.get(override.live_session_id, 0) + 1
        infos: List[RecordedSessionInfo] = []
        for window in windows:
            files = matched.get(window.live_session_id)
            count = (len(files) if files else 0) + manual_counts.get(window.live_session_id, 0)
            if count == 0:
                continue
            session = await self._database.sessions.get_live_session(live_session_id=window.live_session_id)
            infos.append(
                RecordedSessionInfo(
                    live_session_id=window.live_session_id,
                    started_at_ms=window.started_at_ms,
                    ended_at_ms=window.ended_at_ms,
                    title=session.title if session is not None else "",
                    file_count=count,
                )
            )
            if len(infos) >= limit:
                break
        return infos

    async def get_session_recordings(
        self, live_session_id: int
    ) -> Tuple[List[RecordingFile], List[RecordingFile], List[Path]]:
        """单场录播文件三组：生效文件 / 被人工移出的文件（可恢复）/ 未识别候选（可指派）。"""
        scan = self._scan()
        session = await self._database.sessions.get_live_session(live_session_id=live_session_id)
        if session is None:
            return [], [], scan.unmatched
        matched = match_files_to_sessions(
            scan.parsed,
            [
                SessionWindow(
                    live_session_id=session.id, started_at_ms=session.started_at_ms, ended_at_ms=session.ended_at_ms
                )
            ],
        )
        effective, detached = self._apply_overrides(live_session_id, scan, matched.get(live_session_id, []))
        assigned_paths = {o.path for o in self._overrides.for_session(live_session_id) if o.action == "assign"}
        unmatched = [p for p in scan.unmatched if str(p) not in assigned_paths]
        return effective, detached, unmatched

    # ---------- AI 选段 ----------

    async def select_segments(self, *, live_session_id: int) -> int:
        """对一场跑 AI 选段并整批替换该场的 AI 段，返回提议段数。"""
        if self._selector is None:
            raise ClipSelectionError("LLM 未装配，无法 AI 选段")
        registry = self._task_registry or get_registry()
        task_id = f"clip-select-{live_session_id}"
        if registry is not None:
            await registry.begin(task_id, f"AI 选段（场次 {live_session_id}）", progress_text="取本场素材中…")
        try:
            suggestions = await self._selector.select(live_session_id=live_session_id)
            segments = [
                NewClipSegment(
                    live_session_id=live_session_id,
                    origin="ai",
                    start_ms=s.start_ms,
                    end_ms=s.end_ms,
                    title=s.title,
                    reason=s.reason,
                )
                for s in suggestions
            ]
            count = await self._database.clips.replace_ai_segments(live_session_id=live_session_id, segments=segments)
            if registry is not None:
                await registry.finish(task_id, success=True)
            return count
        except Exception as exc:
            if registry is not None:
                await registry.finish(task_id, success=False, failure_reason=str(exc))
            raise

    # ---------- 切割 ----------

    async def cut_segments(self, *, live_session_id: int, segment_ids: Sequence[int]) -> int:
        """受理切割：同步只做校验，实际 ffmpeg 执行转后台任务（HTTP 立即返回）。

        切割耗时不可控（重编码接近实时），不能占住请求——逐段状态回填到
        clip_segments，进度走后台任务条。返回受理段数。
        """
        segments = await self._database.clips.list_segments_by_ids(segment_ids=list(segment_ids))
        segments = [s for s in segments if s.live_session_id == live_session_id]
        if not segments:
            raise ClipCutError("没有可切割的段（段不存在或不属于该场次）")
        files, _ = await self._scan_and_match_one(live_session_id)
        if not files:
            raise ClipCutError("该场次没有匹配到录播文件，无法切割")
        spawn_background_task(
            self._run_cut(live_session_id=live_session_id, segments=segments, files=files),
            logger=logger,
            tasks=self._background_tasks,
            label=f"clip-cut-{live_session_id}",
        )
        return len(segments)

    async def _run_cut(self, *, live_session_id: int, segments: List[ClipSegment], files: List[RecordingFile]) -> None:
        """后台执行整批切割（进度登记 + 逐段状态回填）。"""
        registry = self._task_registry or get_registry()
        task_id = f"clip-cut-{live_session_id}"
        if registry is not None:
            await registry.begin(task_id, f"切割切片（场次 {live_session_id}）", progress_text=f"共 {len(segments)} 段")
        done = 0
        try:
            for index, segment in enumerate(segments, start=1):
                if registry is not None:
                    await registry.update(
                        task_id, progress_text=f"第 {index} 段/共 {len(segments)} 段：{segment.title}"
                    )
                if await self._cut_one(
                    segment_id=segment.id,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    title=segment.title,
                    order=index,
                    files=files,
                ):
                    done += 1
            if registry is not None:
                await registry.finish(task_id, success=True)
        except Exception as exc:
            if registry is not None:
                await registry.finish(task_id, success=False, failure_reason=str(exc))
            raise
        logger.info(f"场次 {live_session_id} 切割完成：{done}/{len(segments)} 段出片")

    async def _cut_one(
        self, *, segment_id: int, start_ms: int, end_ms: int, title: str, order: int, files: List[RecordingFile]
    ) -> bool:
        """切一段（可能跨文件多份），逐份回填状态；单段失败不中断整批。"""
        portions = plan_portions(start_ms, end_ms, files)
        if not portions:
            await self._database.clips.set_status(
                segment_id=segment_id, status="failed", error_message="没有覆盖该时段的录播文件"
            )
            return False
        await self._database.clips.set_status(segment_id=segment_id, status="cutting")
        outputs: List[str] = []
        try:
            for portion in portions:
                name = output_filename_for(
                    segment_start_ms=start_ms, title=title, index=order, part_index=portion.part_index
                )
                produced = await self._cutter.cut(portion, self.output_dir / name, log_dir=_FFMPEG_LOG_DIR)
                outputs.append(str(produced))
            await self._database.clips.set_status(segment_id=segment_id, status="done", output_path="\n".join(outputs))
            return True
        except ClipCutError as exc:
            logger.error(f"切片段 {segment_id} 切割失败：{exc}", exc=exc)
            await self._database.clips.set_status(segment_id=segment_id, status="failed", error_message=str(exc)[:2000])
            return False

    # ---------- 产物 ----------

    def session_outputs_summary(self, outputs: Sequence[str]) -> List[str]:
        """产物路径串（多份换行分隔）摊平成列表（API 响应用）。"""
        return [line for text in outputs for line in text.splitlines() if line.strip()]

    # ---------- 预览 ----------

    def resolve_preview_source(self, source_path: str) -> Path:
        """校验预览源：必须在录播目录子树内且存在（防目录越界读任意文件）。"""
        recording_root = self._config.recording_dir.strip()
        if not recording_root:
            raise PreviewSourceError("未配置录播目录，无法预览")
        root = Path(recording_root).resolve()
        try:
            resolved = Path(source_path).resolve()
        except OSError as exc:
            raise PreviewSourceError(f"预览源路径无法解析：{source_path}") from exc
        if resolved != root and root not in resolved.parents:
            raise PreviewSourceError("预览源不在录播目录内")
        if not resolved.is_file():
            raise PreviewSourceError(f"源文件不存在：{resolved.name}")
        return resolved

    def _preview_cache_path(self, source: Path) -> Path:
        """缓存文件路径：文件名 + mtime + 大小做键（源文件变了自动换新缓存）。"""
        stat = source.stat()
        key = hashlib.sha256(f"{source.name}|{stat.st_mtime_ns}|{stat.st_size}".encode("utf-8")).hexdigest()[:16]
        return _PREVIEW_CACHE_DIR / f"{source.stem}-{key}.mp4"

    def preview_file_or_none(self, source: Path) -> Optional[Path]:
        """可直接服务的 mp4：mp4 源直服本身；其他容器查缓存（转换中不算就绪）。"""
        if source.suffix.lower() == ".mp4":
            return source
        cache = self._preview_cache_path(source)
        if cache.stem in self._preview_inflight:
            return None
        if cache.is_file() and cache.stat().st_size > 0:
            return cache
        return None

    def _range_cache_path(self, source: Path, start_s: float, duration_s: float) -> Path:
        """段级缓存路径：文件名 + mtime + 大小 + 起止秒做键。"""
        stat = source.stat()
        key_raw = f"{source.name}|{stat.st_mtime_ns}|{stat.st_size}|{start_s:.3f}|{duration_s:.3f}"
        key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:16]
        return _PREVIEW_CACHE_DIR / f"{source.stem}-seg{int(start_s)}-{key}.mp4"

    async def range_preview_file(self, source: Path, *, start_s: float, duration_s: float) -> Path:
        """段级预览文件：缓存命中直接回；否则（并发去重后）内联重封装该段。

        只重封装请求的那几分钟，秒级可播；整场缓存由 prepare 流程另行预热。
        """
        cache = self._range_cache_path(source, start_s, duration_s)
        if cache.is_file() and cache.stat().st_size > 0:
            return cache
        lock = self._range_locks.setdefault(cache.name, asyncio.Lock())
        async with lock:
            if cache.is_file() and cache.stat().st_size > 0:
                return cache
            await self._cutter.remux_range_to_mp4(
                source, cache, start_s=start_s, duration_s=duration_s, log_dir=_FFMPEG_LOG_DIR
            )
            return cache

    def prepare_previews(self, source_paths: Sequence[str]) -> int:
        """批量为非 mp4 源发起后台重封装（去重幂等），返回新发起的任务数。"""
        started = 0
        for raw in source_paths:
            try:
                source = self.resolve_preview_source(raw)
            except PreviewSourceError as exc:
                logger.warning(f"预览准备跳过：{exc}")
                continue
            if source.suffix.lower() == ".mp4":
                continue
            if self.preview_file_or_none(source) is not None:
                continue
            cache = self._preview_cache_path(source)
            if cache.stem in self._preview_inflight:
                continue
            self._preview_inflight.add(cache.stem)
            spawn_background_task(
                self._run_preview_remux(source, cache),
                logger=logger,
                tasks=self._background_tasks,
                label=f"clip-preview-{cache.stem[:8]}",
            )
            started += 1
        return started

    async def _run_preview_remux(self, source: Path, cache: Path) -> None:
        """后台重封装一份预览缓存（登记处可见；失败清半截产物）。"""
        registry = self._task_registry or get_registry()
        task_id = f"clip-preview-{cache.stem[:8]}"
        if registry is not None:
            await registry.begin(task_id, f"预览转换：{source.name}", progress_text="流复制重封装中…")
        try:
            await self._cutter.remux_to_mp4(source, cache, log_dir=_FFMPEG_LOG_DIR)
            if registry is not None:
                await registry.finish(task_id, success=True)
            logger.info(f"预览缓存已生成：{cache.name}")
        except Exception as exc:  # noqa: BLE001 - 边界：转封装失败不拖垮预览服务
            try:
                cache.unlink(missing_ok=True)
            except OSError:
                pass
            if registry is not None:
                await registry.finish(task_id, success=False, failure_reason=str(exc))
            logger.error(f"预览转换失败 {source.name}：{exc}", exc=exc)
        finally:
            self._preview_inflight.discard(cache.stem)


__all__ = ["ClippingService", "PreviewSourceError", "RecordedSessionInfo"]
