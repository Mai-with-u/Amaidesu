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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
import asyncio
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

import time as time_module

from src.modules.background.registry import BackgroundTaskRegistry, get_registry
from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.cutter import (
    ClipCutError,
    ClipCutter,
    ClipMediaTimeInfo,
    output_filename_for,
    plan_portions,
)
from src.modules.clipping.recording_matcher import (
    RecordingFile,
    RecordingScan,
    SessionWindow,
    file_covers_session_start,
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
from src.modules.storage.repos.sessions import MAX_SETTLE_ATTEMPTS
from src.modules.types.session_kind import SESSION_KIND_LIVE
from src.modules.utils.task_utils import spawn_background_task

logger = get_logger("ClippingService")


class SessionDensityData(NamedTuple):
    """场次聊天密度（密度轨取数结果，API 层转响应模型）。

    ``counts[i]`` 覆盖墙钟区间 ``[started_at_ms + i*bucket_ms, started_at_ms +
    (i+1)*bucket_ms)``，从场起点起连续补零。
    """

    bucket_ms: int
    started_at_ms: int
    counts: List[int]


@dataclass
class UnmatchedCandidate:
    """一个解析不出时刻的录播文件，附判断归属用的推断信息。

    推算 = 文件修改时间 − 时长（mtime≈录制停止时刻）；mtime 会被复制/同步
    重置，推断窗只用于展示与人工指派预填，不自动归场。
    """

    path: Path
    file_name: str
    size_bytes: int
    mtime_ms: int
    duration_s: Optional[float]
    inferred_start_ms: Optional[int]
    inferred_end_ms: Optional[int]


@dataclass
class CatalogEntry:
    """录播目录视频清单的一行（"匹配录播文件"选择器的数据源）。"""

    path: Path
    file_name: str
    size_bytes: int
    mtime_ms: int
    duration_s: Optional[float]
    inferred_start_ms: Optional[int]
    inferred_end_ms: Optional[int]
    # 文件名/元数据/推断识别出的开始时刻；人工指派的按 override.start_ms；
    # 都没有为 None（纯待归档）
    started_at_ms: Optional[int]
    started_at_source: Optional[str]
    # 自动匹配到的场次（时间窗命中；可能多个理论上不存在，留列表兜底）
    matched_session_ids: List[int]
    # 人工指派申报的场次（无人工指派为 None）
    assigned_session_id: Optional[int]


# 下播自动选段排队等本场记忆整理跑完的上限与轮询间隔（秒）：切片排在下播整理之后，避免抢占同一模型配额
_WAIT_SETTLE_MAX_S = 600.0
_WAIT_SETTLE_POLL_S = 1.0

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
    """切片页场次列表的一行：场次基本信息 + 录播文件数与切片段任务态计数。"""

    live_session_id: int
    started_at_ms: int
    ended_at_ms: Optional[int]
    title: str
    file_count: int
    pending_count: int = 0
    done_count: int = 0


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
        selector: Optional[ClipSelector] = None,
    ) -> None:
        self._event_bus = event_bus
        self._database = database
        self._config = config
        self._task_registry = task_registry
        self._subscribed = False
        self._selector: Optional[ClipSelector] = None
        if selector is not None:
            self._selector = selector
        elif llm_manager is not None:
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
        # 自动识别探测缓存：（路径, mtime 毫秒, 大小）→ ffprobe 媒体时间信息
        # （None = 探过但容器没有可用标记，同样缓存避免反复跑 ffprobe）
        self._creation_time_cache: Dict[Tuple[str, int, int], Optional[ClipMediaTimeInfo]] = {}
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

    async def wait_background_tasks_done(self) -> None:
        """等在飞后台任务（切割/选段/预览转换）结束。

        测试用它替代"固定窗口轮询段状态"（轮询猜时长是负载偶发的来源）；
        关闭链也可共用。``return_exceptions=True``：失败任务已在任务内部登记
        结果（registry.finish + spawn 层 error 日志），汇合时不外传异常。
        """
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)

    async def _on_live_ended(self, event_name: str, payload: LiveEndedPayload, source: str) -> None:
        """下播后自动选段（fire-and-forget；空场次已被丢弃，不处理）。

        事件总线统一按 (事件名, 载荷, 事件源) 三参调用订阅者，本方法签名与之对齐。
        """
        if payload.empty_discarded:
            return
        spawn_background_task(
            self._auto_select(payload.live_session_id),
            logger=logger,
            tasks=self._background_tasks,
            label=f"clip-auto-select-{payload.live_session_id}",
        )

    async def _auto_select(self, live_session_id: int) -> None:
        """自动选段：匹配到录播文件后，先等该场下播整理跑完，再排跑 AI 选段。"""
        try:
            files, _ = await self._scan_and_match_one(live_session_id)
            if not files:
                logger.info(f"场次 {live_session_id} 下播，但录播目录里没有匹配到文件，跳过自动选段")
                return
            await self.wait_for_session_settle(live_session_id)
            count = await self.select_segments(live_session_id=live_session_id)
            logger.info(f"场次 {live_session_id} 下播自动选段完成，AI 提议 {count} 段")
        except Exception as exc:  # noqa: BLE001 - 自动选段失败不影响下播流程，记日志即可
            logger.error(f"场次 {live_session_id} 下播自动选段失败：{exc}", exc=exc)

    async def wait_for_session_settle(
        self,
        live_session_id: int,
        *,
        poll_s: float = _WAIT_SETTLE_POLL_S,
        max_wait_s: float = _WAIT_SETTLE_MAX_S,
    ) -> None:
        """若该场下播整理正在跑或刚进入收尾等待，等它结束再开始切片选段（防切片抢在档案整理前）。"""
        settle_task_id = f"settle:{int(live_session_id)}"
        # 让同批 live.ended 订阅者（如道别轮与收播整理登记）先获得调度机会
        await asyncio.sleep(0)
        elapsed = 0.0
        grace_polls = 3
        step = max(poll_s, 0.001)
        while elapsed < max_wait_s:
            registry = self._task_registry or get_registry()
            in_registry = registry is not None and any(t.task_id == settle_task_id for t in registry.snapshot())
            if in_registry:
                await asyncio.sleep(poll_s)
                elapsed += step
                continue
            session = await self._database.sessions.get_live_session(live_session_id=int(live_session_id))
            if (
                session is None
                or session.kind != SESSION_KIND_LIVE
                or session.settled_at_ms is not None
                or session.settle_attempts >= MAX_SETTLE_ATTEMPTS
            ):
                return
            # 场次尚未整理完但任务尚未登记进面板（可能正在跑道别轮或等队尾播完）：短暂礼让几次，若仍未启动整理则不死等
            if grace_polls <= 0:
                return
            grace_polls -= 1
            await asyncio.sleep(poll_s)
            elapsed += step

    # ---------- 场次 × 录播文件 ----------

    async def _scan(self) -> RecordingScan:
        """扫描录播目录（排除切片输出目录，避免把产物当素材），并对文件名
        解析失败的文件做自动识别。

        自动识别两级：容器 creation_time 直接当录制开始时刻；没有该标记
        （FLV、被转码剥掉标签）则用 修改时间−时长 推算，推算开始唯一落进
        某个场次时间窗才归场（窗口歧义或落窗外的留待人工）。人工覆盖登记过
        的文件不参与自动识别（人工表态优先，见 _apply_overrides）。探测结果
        按（路径, mtime, 大小）缓存在内存，文件没变不重复跑 ffprobe。
        """
        scan = scan_recording_dir(Path(self._config.recording_dir.strip()), exclude_dirs=[self.output_dir])
        if not scan.unmatched:
            return scan
        overridden = {override.path for override in self._overrides.load()}
        candidates = [p for p in scan.unmatched if str(p) not in overridden]
        if not candidates:
            return scan
        windows = await self._session_windows()
        semaphore = asyncio.Semaphore(4)

        async def probe_one(path: Path) -> Optional[RecordingFile]:
            key = self._creation_time_cache_key(path)
            if key is not None and key in self._creation_time_cache:
                info = self._creation_time_cache[key]
            else:
                async with semaphore:
                    info = await self._cutter.probe_media_info(path)
                if key is not None:
                    self._creation_time_cache[key] = info
            if info is None or (info.creation_time_ms is None and info.duration_s is None):
                return None
            # 第一级：容器 creation_time 是录制软件写入的开始时刻，直接归场
            if info.creation_time_ms is not None:
                return RecordingFile(
                    path=path, room_id="", started_at_ms=info.creation_time_ms, title="", started_at_source="metadata"
                )
            # 第二级：修改时间 − 时长 推算开始；唯一落进某场时间窗才自动归场
            # （多窗歧义或落窗外说明推算不可信，留待人工）
            try:
                mtime_ms = int(path.stat().st_mtime * 1000)
            except OSError:
                return None
            inferred_start = mtime_ms - int(info.duration_s * 1000)
            probe_file = RecordingFile(path=path, room_id="", started_at_ms=inferred_start, title="")
            hits = [w for w in windows if file_covers_session_start(probe_file, w)]
            if len(hits) != 1:
                return None
            return RecordingFile(
                path=path, room_id="", started_at_ms=inferred_start, title="", started_at_source="inferred"
            )

        recovered = await asyncio.gather(*(probe_one(path) for path in candidates))
        for info in recovered:
            if info is None:
                continue
            scan.unmatched.remove(info.path)
            scan.parsed.append(info)
        scan.parsed.sort(key=lambda f: (f.started_at_ms, f.sort_index))
        return scan

    def _creation_time_cache_key(self, path: Path) -> Optional[Tuple[str, int, int]]:
        """兜底缓存键（路径, mtime 毫秒, 大小）；文件 stat 失败按不可缓存处理。"""
        try:
            stat = path.stat()
        except OSError:  # 文件在扫描间隙被移动/删除：不缓存不兜底
            return None
        return (str(path), int(stat.st_mtime * 1000), stat.st_size)

    async def _media_info_cached(self, path: Path) -> Optional[ClipMediaTimeInfo]:
        """查兜底缓存的探测结果；未命中共探一次（未识别文件的时长/推算展示用）。"""
        key = self._creation_time_cache_key(path)
        if key is None:
            return None
        if key not in self._creation_time_cache:
            self._creation_time_cache[key] = await self._cutter.probe_media_info(path)
        return self._creation_time_cache.get(key)

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
        scan = await self._scan()
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
        """切片页场次列表：全部场次（含暂无匹配录播文件的，按开始时间倒序）。

        零匹配的场次必须可见——否则文件名识别不了的场次在页面上没有入口
        （选不了场就到不了"未识别候选→指派"的人工路径），形成死锁。
        """
        scan = await self._scan()
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
        status_counts = await self._database.clips.count_by_status_by_session()
        for window in windows:
            files = matched.get(window.live_session_id)
            count = (len(files) if files else 0) + manual_counts.get(window.live_session_id, 0)
            session = await self._database.sessions.get_live_session(live_session_id=window.live_session_id)
            counts = status_counts.get(window.live_session_id, {})
            infos.append(
                RecordedSessionInfo(
                    live_session_id=window.live_session_id,
                    started_at_ms=window.started_at_ms,
                    ended_at_ms=window.ended_at_ms,
                    title=session.title if session is not None else "",
                    file_count=count,
                    pending_count=counts.get("suggested", 0) + counts.get("confirmed", 0),
                    done_count=counts.get("done", 0),
                )
            )
            if len(infos) >= limit:
                break
        return infos

    async def get_session_recordings(
        self, live_session_id: int
    ) -> Tuple[List[RecordingFile], List[RecordingFile], List[UnmatchedCandidate]]:
        """单场录播文件三组：生效文件 / 被人工移出的文件（可恢复）/ 待归档候选。

        待归档候选附推断信息（大小/时长/推算录制窗）供"匹配录播文件"选择器
        判断归属；自动识别成功的文件不会出现在这一组。
        """
        scan = await self._scan()
        session = await self._database.sessions.get_live_session(live_session_id=live_session_id)
        if session is None:
            return [], [], []
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
        candidates = [
            candidate for candidate in [await self._unmatched_candidate(p) for p in unmatched] if candidate is not None
        ]
        return effective, detached, candidates

    async def _unmatched_candidate(self, path: Path) -> Optional[UnmatchedCandidate]:
        """把一个未识别路径装配成带推断信息的候选；文件已消失返回 None。"""
        try:
            stat = path.stat()
        except OSError:
            return None
        info = await self._media_info_cached(path)
        duration_s = info.duration_s if info is not None else None
        mtime_ms = int(stat.st_mtime * 1000)
        inferred_start = mtime_ms - int(duration_s * 1000) if duration_s is not None else None
        return UnmatchedCandidate(
            path=path,
            file_name=path.name,
            size_bytes=stat.st_size,
            mtime_ms=mtime_ms,
            duration_s=duration_s,
            inferred_start_ms=inferred_start,
            inferred_end_ms=mtime_ms,
        )

    async def recording_catalog(self) -> List[CatalogEntry]:
        """录播目录全部视频清单（"匹配录播文件"选择器的数据源）。

        每行带识别出的开始时刻（含来源）与推断录制窗（解析不出时刻的文件），
        以及自动匹配/人工指派的场次归属，供按场次挑文件时判断。
        """
        scan = await self._scan()
        windows = await self._session_windows()
        matched = match_files_to_sessions(scan.parsed, windows)
        assigned: Dict[str, int] = {o.path: o.live_session_id for o in self._overrides.load() if o.action == "assign"}
        entries: List[CatalogEntry] = []
        for path in sorted({f.path for f in scan.parsed} | set(scan.unmatched)):
            parsed = next((f for f in scan.parsed if f.path == path), None)
            try:
                stat = path.stat()
            except OSError:
                continue
            info = await self._media_info_cached(path)
            duration_s = info.duration_s if info is not None else None
            mtime_ms = int(stat.st_mtime * 1000)
            inferred_start = mtime_ms - int(duration_s * 1000) if duration_s is not None else None
            assigned_session = assigned.get(str(path))
            entries.append(
                CatalogEntry(
                    path=path,
                    file_name=path.name,
                    size_bytes=stat.st_size,
                    mtime_ms=mtime_ms,
                    duration_s=duration_s,
                    inferred_start_ms=inferred_start,
                    inferred_end_ms=mtime_ms,
                    started_at_ms=parsed.started_at_ms if parsed is not None else None,
                    started_at_source=parsed.started_at_source if parsed is not None else None,
                    matched_session_ids=sorted(
                        sid for sid, files in matched.items() if any(f.path == path for f in files)
                    ),
                    assigned_session_id=assigned_session,
                )
            )
        entries.sort(key=lambda e: e.mtime_ms, reverse=True)
        return entries

    # ---------- AI 选段 ----------

    async def select_segments(self, *, live_session_id: int) -> None:
        """受理 AI 选段：同步只校验装配态，LLM 调用转后台任务（HTTP 立即返回）。

        LLM 读整场时间线耗时不可控，不能占住请求——完成后整批替换该场的
        AI 段，前端经任务终态回读刷新段列表。
        """
        if self._selector is None:
            raise ClipSelectionError("LLM 未装配，无法 AI 选段")
        spawn_background_task(
            self._run_select(live_session_id=live_session_id),
            logger=logger,
            tasks=self._background_tasks,
            label=f"clip-select-{live_session_id}",
        )

    async def _run_select(self, *, live_session_id: int) -> None:
        """后台执行 AI 选段并整批替换该场的 AI 段（进度登记）。"""
        if self._selector is None:  # 受理后装配被摘除的窗口；按失败登记而非崩溃
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
        except Exception as exc:
            if registry is not None:
                await registry.finish(task_id, success=False, failure_reason=str(exc))
            raise
        logger.info(f"场次 {live_session_id} AI 选段完成：提议 {count} 段")

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

    # ---------- 产物定位 ----------

    def reveal_in_file_manager(self, path: str) -> None:
        """在系统文件管理器中定位一个切片产物/录播文件（越界路径拒绝）。

        WebUI 跑在本机，reveal 是运营拿走成片的最后一跳；路径必须落在输出
        目录或录播目录子树内且存在，防目录越界暴露任意位置。
        """
        roots = [self.output_dir.resolve()]
        recording_root = self._config.recording_dir.strip()
        if recording_root:
            roots.append(Path(recording_root).resolve())
        try:
            resolved = Path(path).resolve()
        except OSError as exc:
            raise ValueError(f"路径无法解析：{path}") from exc
        if not any(resolved == root or root in resolved.parents for root in roots):
            raise ValueError("路径不在输出目录或录播目录内")
        if not resolved.is_file():
            raise ValueError(f"文件不存在：{resolved.name}")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(resolved)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(resolved)])
        else:
            subprocess.Popen(["xdg-open", str(resolved.parent)])

    # ---------- 密度 ----------

    async def session_density(self, *, live_session_id: int, bucket_seconds: int) -> Optional[SessionDensityData]:
        """场次聊天密度（时间轴密度轨取数）：按桶计数，从场起点连续补零到末桶。

        场次不存在返回 None（API 层转 404）。桶宽入参夹取到 10~300 秒；
        桶数封顶，未收播的长场不会撑爆载荷。
        """
        session = await self._database.sessions.get_live_session(live_session_id=live_session_id)
        if session is None:
            return None
        bucket_ms = max(10, min(300, bucket_seconds)) * 1000
        buckets = await self._database.chat.count_session_density(
            live_session_id=live_session_id,
            bucket_start_ms=session.started_at_ms,
            bucket_ms=bucket_ms,
        )
        counts_by_start = {b.bucket_start_ms: b.count for b in buckets}
        end_ms = session.ended_at_ms if session.ended_at_ms is not None else int(time_module.time() * 1000)
        total = min(2000, max(1, (end_ms - session.started_at_ms) // bucket_ms + 1))
        counts = [counts_by_start.get(session.started_at_ms + i * bucket_ms, 0) for i in range(total)]
        return SessionDensityData(bucket_ms=bucket_ms, started_at_ms=session.started_at_ms, counts=counts)

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
