"""场次复盘 API（``/api/v1/review``）

只读聚合面（``GET /review``）与数据包导出（``GET /review/export``）。
聚合口径见 ``services.session_review``；本层只做参数解析、装配取数与
响应/文件流编排，不含指标语义。
"""

import asyncio
import json
import os
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.review import (
    ReviewExportManifest,
    ReviewSessionBrief,
    SessionReviewResponse,
)
from src.modules.dashboard.services.session_events import SessionEventReader
from src.modules.dashboard.services.session_review import SessionReviewAggregator
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("ReviewAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]

# 数据包内 manifest 文件名
_MANIFEST_NAME = "manifest.json"


def _parse_ids(ids: str) -> List[int]:
    """ "1,2,3" → 去重保序主键列表；空串/非法值即 400。"""
    parsed: List[int] = []
    for part in ids.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"非法场次 id: {part}") from exc
        if value not in parsed:
            parsed.append(value)
    if not parsed:
        raise HTTPException(status_code=400, detail="ids 不能为空（逗号分隔的场次 id）")
    return parsed


async def _resolve_sessions(server: "DashboardServer", ids: List[int]) -> List[Dict[str, Any]]:
    """按 id 列表取场次行（保持调用顺序）；未指定时回退最新已结束场次。"""
    manager = getattr(server, "session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="LiveSessionManager 未装配")

    if ids:
        rows: List[Dict[str, Any]] = []
        for sid in ids:
            row = await manager.sessions.get_live_session(live_session_id=sid)
            if row is None:
                raise HTTPException(status_code=404, detail=f"场次不存在: {sid}")
            rows.append(dict(row))
        return rows

    listed = await manager.list_sessions(limit=200)
    rows = [dict(row) for row in listed]
    ended = [row for row in rows if row["ended_at_ms"] is not None]
    chosen = ended[0] if ended else (rows[0] if rows else None)
    if chosen is None:
        raise HTTPException(status_code=404, detail="暂无场次记录")
    return [chosen]


def _slow_thresholds(server: "DashboardServer") -> Dict[str, int]:
    """从 model.toml 配置取每 profile 慢调用阈值（与运行期告警同源）。"""
    thresholds: Dict[str, int] = {}
    config_service = getattr(server, "config_service", None)
    if config_service is None:
        return thresholds
    profiles = config_service.get_file_section("model", {}).get("llm_profiles", {})
    if isinstance(profiles, dict):
        for name, section in profiles.items():
            if isinstance(section, dict) and isinstance(section.get("slow_threshold_ms"), int):
                thresholds[str(name)] = int(section["slow_threshold_ms"])
    return thresholds


def _events_dir_of(server: "DashboardServer") -> Optional[Path]:
    """诊断事件流目录（logs/events）；诊断未装配时返回 None（决策侧降级）。"""
    writer = getattr(server, "diagnostics_writer", None)
    if writer is None:
        return None
    return writer.base_dir / "events"


def _build_aggregator(server: "DashboardServer") -> SessionReviewAggregator:
    """从装配取齐聚合依赖；缺件按 None 传入由聚合器逐节降级。"""
    reader = SessionEventReader(
        event_history=getattr(server, "event_history", None),
        events_dir=_events_dir_of(server),
    )
    return SessionReviewAggregator(
        chat_repo=getattr(server, "chat_repo", None),
        llm_repo=getattr(server, "llm_repo", None),
        event_repo=getattr(server, "event_repo", None),
        event_reader=reader,
        slow_thresholds=_slow_thresholds(server),
    )


@router.get("", response_model=SessionReviewResponse)
async def session_review(
    server: ServerDep,
    ids: Annotated[str, Query(description="场次 id 列表（逗号分隔）；缺省=最新已结束场次")] = "",
) -> SessionReviewResponse:
    """场次复盘聚合（单场或多选合并共用一份响应形状）。"""
    session_rows = await _resolve_sessions(server, _parse_ids(ids) if ids else [])
    aggregator = _build_aggregator(server)
    return await aggregator.build(session_rows)


# ---------------------------------------------------------------------- #
# S3 数据包导出                                                          #
# ---------------------------------------------------------------------- #


# 单场时间窗覆盖的自然日序列（本地日，含首尾）
def _covered_days(start_ms: int, end_ms: int) -> List[str]:
    first = datetime.fromtimestamp(start_ms / 1000).date()
    last = datetime.fromtimestamp(end_ms / 1000).date()
    days: List[str] = []
    day = first
    while day <= last:
        days.append(day.strftime("%Y-%m-%d"))
        day += timedelta(days=1)
    return days


def _git_head() -> Optional[str]:
    """当前 HEAD 完整 hash；非 git 环境（打包发行）返回 None。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        head = out.stdout.strip()
        return head or None
    except Exception as exc:  # noqa: BLE001 - 导出兜底路径，不阻断打包
        logger.warning(f"git rev-parse HEAD 失败，manifest 不含 git_head: {exc}")
        return None


def _collect_log_files(logs_root: Path, days: List[str]) -> List[Tuple[Path, str]]:
    """收集场次涉及自然日的日志文件 → (磁盘路径, 包内路径) 列表。

    布局：logs/app/*<日期>.jsonl、logs/events/<日期>/、logs/llm_requests/<日期>/。
    整日打包（设计定案分叉①）：日志按天轮转，零过滤零遗漏；缺失的目录/文件
    静默跳过（旁路数据允许缺失）。
    """
    pairs: List[Tuple[Path, str]] = []
    for day in days:
        app_dir = logs_root / "app"
        if app_dir.is_dir():
            for f in sorted(app_dir.glob(f"*{day}.jsonl")):
                pairs.append((f, f"logs/app/{f.name}"))
        for kind in ("events", "llm_requests"):
            day_dir = logs_root / kind / day
            if not day_dir.is_dir():
                continue
            for f in sorted(day_dir.rglob("*")):
                if f.is_file():
                    pairs.append((f, f"logs/{kind}/{day}/{f.relative_to(day_dir).as_posix()}"))
    return pairs


def _build_export_zip(
    zip_path: Path,
    briefs: List[ReviewSessionBrief],
    windows: List[List[int]],
    logs_root: Path,
    db_path: Optional[Path],
) -> ReviewExportManifest:
    """同步构建 zip（线程池内执行）：db 副本 + 日志原样打包 + manifest。"""
    files: List[Dict[str, object]] = []
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if db_path is not None and db_path.exists():
            # SQLite backup API 出一致性快照副本（运行中库的逐字节拷贝可能撕裂）
            backup_path = zip_path.with_suffix(".db.tmp")
            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                dst = sqlite3.connect(backup_path)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            zf.write(backup_path, f"db/{db_path.name}")
            files.append({"path": f"db/{db_path.name}", "size_bytes": backup_path.stat().st_size})
            backup_path.unlink(missing_ok=True)

        days = sorted({d for w in windows for d in _covered_days(w[0], w[1])})
        for disk_path, arcname in _collect_log_files(logs_root, days):
            zf.write(disk_path, arcname)
            files.append({"path": arcname, "size_bytes": disk_path.stat().st_size})

        manifest = ReviewExportManifest(
            sessions=briefs,
            windows_ms=windows,
            git_head=_git_head(),
            files=files,
        )
        manifest_bytes = json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")
        zf.writestr(_MANIFEST_NAME, manifest_bytes)
    return manifest


@router.get("/export")
async def export_session_data(
    server: ServerDep,
    ids: Annotated[str, Query(description="场次 id 列表（逗号分隔）；缺省=最新已结束场次")] = "",
) -> FileResponse:
    """导出数据包（zip，流式下发）：db 副本 + 场次涉及自然日的日志目录原样打包。

    含观众数据（llm_requests 全文 / live_chat），manifest 注明"仅限开发组"；
    脱敏开关为后续独立需求。
    """
    session_rows = await _resolve_sessions(server, _parse_ids(ids) if ids else [])
    aggregator = _build_aggregator(server)
    review = await aggregator.build(session_rows)
    windows = [
        [b.started_at_ms, b.ended_at_ms if b.ended_at_ms is not None else b.started_at_ms + b.duration_ms]
        for b in review.sessions
    ]

    writer = getattr(server, "diagnostics_writer", None)
    logs_root = writer.base_dir if writer is not None else Path("logs")
    db_path = getattr(server, "storage_db_path", None)

    fd, tmp_name = tempfile.mkstemp(prefix="amaidesu-review-", suffix=".zip")
    os.close(fd)
    zip_path = Path(tmp_name)

    session_ids = ",".join(str(b.live_session_id) for b in review.sessions)
    try:
        manifest = await asyncio.to_thread(_build_export_zip, zip_path, review.sessions, windows, logs_root, db_path)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"review-sessions-{session_ids}-{stamp}.zip"
    logger.info(
        f"数据包已生成: {filename}（{len(manifest.files)} 个文件，{zip_path.stat().st_size} 字节，场次 {session_ids}）"
    )
    return FileResponse(
        path=zip_path,
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(zip_path.unlink),
    )


__all__ = ["router"]
