"""直播切片 API（``/api/v1/clips``）。

录播文件匹配（文件系统事实，请求时扫描）、AI 选段触发、切片段的增删改查、
切割任务受理、页内配置卡（读 + 保存并免重启重装配）。业务编排由
``ClippingService`` 承载（``[clipping]`` 未启用时组件缺席 → 503，前端
``getApiErrorMessage`` 直接可读；配置端点不受此限——服务未装配时也能读改）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse, JSONResponse

from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.recording_matcher import RecordingFile
from src.modules.clipping.service import ClippingService, PreviewSourceError
from src.modules.dashboard.dependencies import get_dashboard_server, server_component
from src.modules.dashboard.services.config_adapter import apply_config_updates
from src.modules.dashboard.utils.component_helper import config_dir as get_config_dir
from src.modules.dashboard.schemas.clips import (
    ClipConfigApplyResponse,
    ClipConfigResponse,
    ClippedSessionItem,
    ClippedSessionListResponse,
    CutRequest,
    CutResponse,
    PreviewPrepareRequest,
    PreviewPrepareResponse,
    RecordingAssignRequest,
    RecordingDetachRequest,
    RecordingFileInfo,
    RecordingRenameRequest,
    RecordingRenameResponse,
    RecordingTimeResponse,
    SegmentCreateRequest,
    SegmentListResponse,
    SegmentMutationResponse,
    SegmentUpdateRequest,
    SelectResponse,
    SessionRecordingsResponse,
    UnmatchedFileInfo,
)
from src.modules.logging import get_logger
from src.modules.storage.models.clipping import ClipSegmentUpdate, NewClipSegment

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("ClipsAPI")

ClippingDep = Annotated[
    ClippingService,
    Depends(server_component("clipping_service", missing_detail="切片功能未装配（检查 [clipping] 配置与录播目录）")),
]

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


def _current_config(server: "DashboardServer") -> ClippingConfigSchema:
    """从配置服务读 [clipping] 段当前值（服务未装配时也能读）。"""
    raw = server.config_service.get_section("clipping", {}) if server.config_service is not None else {}
    return ClippingConfigSchema(**raw) if isinstance(raw, dict) else ClippingConfigSchema()


def _dir_exists(recording_dir: str) -> bool:
    return bool(recording_dir.strip()) and Path(recording_dir.strip()).is_dir()


@router.get("/config", response_model=ClipConfigResponse)
async def get_clip_config(server: ServerDep) -> ClipConfigResponse:
    """切片配置当前值 + 装配态（页内配置卡；服务未装配时照常可读）。"""
    config = _current_config(server)
    return ClipConfigResponse(
        assembled=server.clipping_service is not None,
        config=config,
        recording_dir_exists=_dir_exists(config.recording_dir),
    )


@router.post("/config", response_model=ClipConfigApplyResponse)
async def update_clip_config(request: ClippingConfigSchema, server: ServerDep) -> ClipConfigApplyResponse:
    """保存切片配置并免重启生效：写盘（统一配置管线）→ 停旧服务 → 运行期工厂重装配。

    写盘先行且失败整体拒绝（磁盘零写入，旧服务原样保留——先拆服务再写盘会把
    服务留在已拆状态）；重装配缺工厂（极简启动/测试装配）时写盘生效但服务
    保持原状，响应里说明需要重启。
    """
    changes = [
        (f"infra.clipping.{field_name}", getattr(request, field_name))
        for field_name in ClippingConfigSchema.model_fields
    ]
    outcome = await apply_config_updates(server.config_service, get_config_dir(server), changes)
    if not outcome.success:
        message = "；".join(outcome.errors) if outcome.errors else outcome.message
        logger.error(f"切片配置写盘失败：{message}")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"配置写盘失败：{message}")

    # 写盘成功后才动服务：旧服务停掉摘除，enabled 时经工厂重建
    old = server.clipping_service
    if old is not None:
        old.stop()
        server.clipping_service = None

    if not request.enabled:
        return ClipConfigApplyResponse(
            success=True,
            message="切片功能已停用（配置已写盘）",
            assembled=False,
            recording_dir_exists=_dir_exists(request.recording_dir),
        )

    factory = server.clipping_factory
    if factory is None:
        message = "配置已写盘，但运行期装配工厂未注入（极简启动），重启应用后生效"
        logger.warning(message)
        return ClipConfigApplyResponse(
            success=True, message=message, assembled=False, recording_dir_exists=_dir_exists(request.recording_dir)
        )
    try:
        server.clipping_service = await factory()
    except Exception as exc:  # noqa: BLE001 - 边界：重装配失败转可读错误
        logger.exception(f"切片服务运行期重装配失败: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"重装配失败：{exc}") from exc
    return ClipConfigApplyResponse(
        success=True,
        message="切片配置已保存并生效",
        assembled=True,
        recording_dir_exists=_dir_exists(request.recording_dir),
    )


def _file_info(file: RecordingFile) -> RecordingFileInfo:
    return RecordingFileInfo(
        path=str(file.path),
        file_name=file.path.name,
        started_at_ms=file.started_at_ms,
        title=file.title,
    )


@router.get("/sessions", response_model=ClippedSessionListResponse)
async def list_sessions(service: ClippingDep, limit: int = 100) -> ClippedSessionListResponse:
    """有匹配录播文件的场次列表（按开播时间倒序）。"""
    infos = await service.list_recorded_sessions(limit=max(1, min(limit, 500)))
    return ClippedSessionListResponse(
        sessions=[
            ClippedSessionItem(
                live_session_id=info.live_session_id,
                started_at_ms=info.started_at_ms,
                ended_at_ms=info.ended_at_ms,
                title=info.title or "",
                file_count=info.file_count,
            )
            for info in infos
        ]
    )


@router.get("/sessions/{live_session_id}/recordings", response_model=SessionRecordingsResponse)
async def list_session_recordings(live_session_id: int, service: ClippingDep) -> SessionRecordingsResponse:
    """单场录播文件：生效（自动+人工指派）/ 被移出（可恢复）/ 未识别（可指派）三组。"""
    files, detached, unmatched = await service.get_session_recordings(live_session_id=live_session_id)
    return SessionRecordingsResponse(
        live_session_id=live_session_id,
        files=[_file_info(file) for file in files],
        detached_files=[_file_info(file) for file in detached],
        unmatched_files=[UnmatchedFileInfo(path=str(path), file_name=path.name) for path in unmatched],
    )


@router.post("/sessions/{live_session_id}/recordings/assign", response_model=SegmentMutationResponse)
async def assign_recording(
    live_session_id: int, request: RecordingAssignRequest, service: ClippingDep
) -> SegmentMutationResponse:
    """人工把一个录播文件指派到本场（解析不出时刻的文件必须给 start_ms）。"""
    try:
        await service.assign_recording(
            live_session_id=live_session_id, source_path=request.path, start_ms=request.start_ms
        )
    except PreviewSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return SegmentMutationResponse(deleted=False)


@router.post("/sessions/{live_session_id}/recordings/detach", response_model=SegmentMutationResponse)
async def detach_recording(
    live_session_id: int, request: RecordingDetachRequest, service: ClippingDep
) -> SegmentMutationResponse:
    """人工把一个文件移出本场（撤销自动匹配）。"""
    try:
        await service.detach_recording(live_session_id=live_session_id, source_path=request.path)
    except PreviewSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return SegmentMutationResponse(deleted=False)


@router.get("/recordings/creation-time", response_model=RecordingTimeResponse)
async def recording_creation_time(path: str, service: ClippingDep) -> RecordingTimeResponse:
    """读容器元数据里的录制时刻（ffprobe creation_time；指派对话框的取时来源）。"""
    try:
        moment = await service.source_creation_time_ms(path)
    except PreviewSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return RecordingTimeResponse(creation_time_ms=moment)


@router.post("/recordings/rename", response_model=RecordingRenameResponse)
async def rename_recording(request: RecordingRenameRequest, service: ClippingDep) -> RecordingRenameResponse:
    """WebUI 里给录播文件改名（同目录、扩展名不可改；归属覆盖随新路径迁移）。"""
    try:
        target = await service.rename_recording(source_path=request.path, new_name=request.new_name)
    except PreviewSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return RecordingRenameResponse(path=str(target))


@router.post("/sessions/{live_session_id}/recordings/restore", response_model=SegmentMutationResponse)
async def restore_recording(
    live_session_id: int, request: RecordingDetachRequest, service: ClippingDep
) -> SegmentMutationResponse:
    """撤销一条人工覆盖（恢复自动匹配结果）。"""
    try:
        await service.restore_recording(live_session_id=live_session_id, source_path=request.path)
    except PreviewSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return SegmentMutationResponse(deleted=False)


@router.post("/sessions/{live_session_id}/select", response_model=SelectResponse)
async def select_segments(live_session_id: int, service: ClippingDep) -> SelectResponse:
    """触发/重跑 AI 选段（同步等待结果；耗时主要在 LLM 调用，进度看后台任务条）。"""
    try:
        count = await service.select_segments(live_session_id=live_session_id)
    except Exception as exc:  # noqa: BLE001 - 选段失败（含模型输出解析失败）转 502 可读错误
        logger.error(f"场次 {live_session_id} AI 选段失败：{exc}", exc=exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"AI 选段失败：{exc}") from exc
    return SelectResponse(suggestion_count=count)


@router.get("/sessions/{live_session_id}/segments", response_model=SegmentListResponse)
async def list_segments(live_session_id: int, service: ClippingDep) -> SegmentListResponse:
    """单场全部切片段（AI 提议 + 运营手建，按起点排序）。"""
    segments = await service.clips_repo.list_session_segments(live_session_id=live_session_id)
    return SegmentListResponse(live_session_id=live_session_id, segments=segments)


@router.post("/sessions/{live_session_id}/segments", response_model=SegmentMutationResponse)
async def create_segment(
    live_session_id: int, request: SegmentCreateRequest, service: ClippingDep
) -> SegmentMutationResponse:
    """运营手建一段。"""
    if request.end_ms <= request.start_ms:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="段终点必须晚于段起点")
    segment = await service.clips_repo.add_segment(
        segment=NewClipSegment(
            live_session_id=live_session_id,
            origin="manual",
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            title=request.title,
        )
    )
    return SegmentMutationResponse(segment=segment)


@router.put("/segments/{segment_id}", response_model=SegmentMutationResponse)
async def update_segment(
    segment_id: int, request: SegmentUpdateRequest, service: ClippingDep
) -> SegmentMutationResponse:
    """运营手调一段（起止/标题/状态；None 字段不动）。"""
    if request.start_ms is not None and request.end_ms is not None and request.end_ms <= request.start_ms:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="段终点必须晚于段起点")
    existing = await service.clips_repo.get_segment(segment_id=segment_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"切片段 {segment_id} 不存在")
    # 单边改动也要按"改后的完整区间"校验（只改 start 或只改 end 可能撞库里的
    # end>=start CHECK，那里只会给 500）
    effective_start = request.start_ms if request.start_ms is not None else existing.start_ms
    effective_end = request.end_ms if request.end_ms is not None else existing.end_ms
    if effective_end <= effective_start:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="段终点必须晚于段起点")
    segment = await service.clips_repo.update_segment(
        segment_id=segment_id, update=ClipSegmentUpdate(**request.model_dump())
    )
    if segment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"切片段 {segment_id} 不存在")
    return SegmentMutationResponse(segment=segment)


@router.delete("/segments/{segment_id}", response_model=SegmentMutationResponse)
async def delete_segment(segment_id: int, service: ClippingDep) -> SegmentMutationResponse:
    """删除一段。"""
    deleted = await service.clips_repo.delete_segment(segment_id=segment_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"切片段 {segment_id} 不存在")
    return SegmentMutationResponse(deleted=True)


@router.post("/sessions/{live_session_id}/cut", response_model=CutResponse)
async def cut_segments(live_session_id: int, request: CutRequest, service: ClippingDep) -> CutResponse:
    """发起切割（后台任务执行，逐段状态回填到段列表）。"""
    if not service.recording_dir:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="未配置录播目录，无法切割")
    try:
        accepted = await service.cut_segments(live_session_id=live_session_id, segment_ids=request.segment_ids)
    except Exception as exc:  # noqa: BLE001 - 受理前的失败（无文件/无段）转可读错误
        logger.error(f"场次 {live_session_id} 切割受理失败：{exc}", exc=exc)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return CutResponse(accepted=accepted)


@router.get("/preview/status")
async def preview_status(path: str, service: ClippingDep) -> dict:
    """预览就绪状态（轻量 JSON，供前端轮询；首次查询顺带触发转换）。"""
    try:
        source = service.resolve_preview_source(path)
    except PreviewSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    ready = service.preview_file_or_none(source) is not None
    if not ready:
        service.prepare_previews([str(source)])
    return {"ready": ready, "converting": not ready}


@router.get("/preview/range")
async def preview_range_file(path: str, start_s: float, duration_s: float, service: ClippingDep) -> Response:
    """段级预览：只重封装请求的那几分钟（流复制秒级），整场未转换完也能立刻看。

    整场缓存就绪时直接回整场文件（可自由拖动）；否则现切现转并缓存复用。
    """
    try:
        source = service.resolve_preview_source(path)
    except PreviewSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if start_s < 0 or duration_s <= 0 or duration_s > 3600:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="预览范围不合法")
    ready_full = service.preview_file_or_none(source)
    if ready_full is not None:
        return FileResponse(ready_full, media_type="video/mp4")
    try:
        ranged = await service.range_preview_file(source, start_s=start_s, duration_s=duration_s)
    except Exception as exc:  # noqa: BLE001 - 边界：重封装失败转可读错误
        logger.error(f"段级预览重封装失败 {source.name}：{exc}", exc=exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"预览准备失败：{exc}") from exc
    return FileResponse(ranged, media_type="video/mp4")


@router.get("/preview")
async def preview_file(path: str, service: ClippingDep) -> Response:
    """预览视频文件：mp4 直服（支持 Range 拖动）；flv/mkv 等容器 202 转换中。

    缓存就绪前返回 202 JSON（前端轮询本端点直到 200）；首次请求顺带触发后台
    重封装，也可经 ``POST /preview/prepare`` 批量预热。
    """
    try:
        source = service.resolve_preview_source(path)
    except PreviewSourceError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    ready = service.preview_file_or_none(source)
    if ready is not None:
        return FileResponse(ready, media_type="video/mp4")
    service.prepare_previews([str(source)])
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"converting": True, "ready": False})


@router.post("/preview/prepare", response_model=PreviewPrepareResponse)
async def prepare_previews(request: PreviewPrepareRequest, service: ClippingDep) -> PreviewPrepareResponse:
    """批量预热预览缓存（非 mp4 源后台重封装；幂等去重）。"""
    started = service.prepare_previews(request.paths)
    return PreviewPrepareResponse(started=started)


__all__ = ["router"]
