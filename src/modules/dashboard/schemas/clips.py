"""切片 API 的请求/响应模型（``/api/v1/clips``）。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.storage.models.clipping import ClipSegment, ClipSegmentStatus


class RecordingFileInfo(BaseModel):
    """一个匹配到的录播文件（含文件名解析出的开始时刻）。"""

    path: str = Field(..., description="文件绝对路径")
    file_name: str = Field(..., description="文件名")
    started_at_ms: int = Field(..., description="录制开始时刻（墙钟 Unix epoch 毫秒，来自文件名）")
    title: str = Field(default="", description="录播姬文件名里的标题段")


class UnmatchedFileInfo(BaseModel):
    """一个解析不了录播姬命名的视频文件（可被运营手动指定）。"""

    path: str = Field(..., description="文件绝对路径")
    file_name: str = Field(..., description="文件名")


class ClippedSessionItem(BaseModel):
    """切片页场次列表的一行。"""

    live_session_id: int = Field(..., description="场次主键")
    started_at_ms: int = Field(..., description="开播时刻（Unix epoch 毫秒）")
    ended_at_ms: Optional[int] = Field(default=None, description="下播时刻；None 即进行中")
    title: str = Field(default="", description="场次标题")
    file_count: int = Field(..., description="匹配到的录播文件数")


class ClippedSessionListResponse(BaseModel):
    """有录播匹配的场次列表。"""

    sessions: List[ClippedSessionItem] = Field(default_factory=list, description="按开播时间倒序")


class RecordingAssignRequest(BaseModel):
    """人工指派一个录播文件到某场的请求体。"""

    path: str = Field(..., min_length=1, description="录播文件绝对路径（须在录播目录内）")
    start_ms: Optional[int] = Field(default=None, description="录制开始时刻（墙钟毫秒）；文件名解析不出时刻时必填")


class RecordingDetachRequest(BaseModel):
    """移出/恢复一个录播文件的请求体。"""

    path: str = Field(..., min_length=1, description="录播文件绝对路径（须在录播目录内）")


class RecordingRenameRequest(BaseModel):
    """WebUI 里给录播文件改名的请求体（同目录、扩展名不可改）。"""

    path: str = Field(..., min_length=1, description="录播文件绝对路径（须在录播目录内）")
    new_name: str = Field(..., min_length=1, description="新文件名（含扩展名）")


class RecordingTimeResponse(BaseModel):
    """容器元数据里的录制时刻（缺失为 None）。"""

    creation_time_ms: Optional[int] = Field(..., description="录制时刻（墙钟 Unix epoch 毫秒）；元数据缺失为 None")


class RecordingRenameResponse(BaseModel):
    """改名结果。"""

    path: str = Field(..., description="改名后的文件绝对路径")


class SessionRecordingsResponse(BaseModel):
    """单场录播文件三组：生效（自动+人工指派）/ 被移出（可恢复）/ 未识别（可指派）。"""

    live_session_id: int = Field(..., description="场次主键")
    files: List[RecordingFileInfo] = Field(default_factory=list, description="生效的录播文件（按开始时刻排序）")
    detached_files: List[RecordingFileInfo] = Field(
        default_factory=list, description="被人工移出本场的文件（可恢复自动匹配）"
    )
    unmatched_files: List[UnmatchedFileInfo] = Field(
        default_factory=list, description="文件名解析不出开始时刻的文件（可指派到本场）"
    )


class SegmentListResponse(BaseModel):
    """单场全部切片段。"""

    live_session_id: int = Field(..., description="场次主键")
    segments: List[ClipSegment] = Field(default_factory=list, description="按段起点排序")


class SegmentCreateRequest(BaseModel):
    """运营手建一段的请求体。"""

    start_ms: int = Field(..., ge=0, description="段起点（墙钟 Unix epoch 毫秒）")
    end_ms: int = Field(..., ge=0, description="段终点（墙钟 Unix epoch 毫秒）")
    title: str = Field(default="", description="片段标题")


class SegmentUpdateRequest(BaseModel):
    """运营手调一段的请求体（None 字段不动）。"""

    start_ms: Optional[int] = Field(default=None, ge=0, description="段起点（墙钟 Unix epoch 毫秒）")
    end_ms: Optional[int] = Field(default=None, ge=0, description="段终点（墙钟 Unix epoch 毫秒）")
    title: Optional[str] = Field(default=None, description="片段标题")
    status: Optional[ClipSegmentStatus] = Field(default=None, description="状态（dismissed = 弃用）")


class SegmentMutationResponse(BaseModel):
    """单段写操作的结果。"""

    segment: Optional[ClipSegment] = Field(default=None, description="写后的段（删除时为 None）")
    deleted: bool = Field(default=False, description="是否删除成功")


class SelectResponse(BaseModel):
    """AI 选段结果。"""

    suggestion_count: int = Field(..., description="提议段数（整批替换该场旧 AI 段）")


class CutRequest(BaseModel):
    """发起切割的请求体。"""

    segment_ids: List[int] = Field(..., min_length=1, description="要切的段 id 列表")


class CutResponse(BaseModel):
    """切割受理结果（进度看后台任务条，逐段状态看段列表）。"""

    accepted: int = Field(..., description="实际受理的段数（不属于该场的段被剔除）")


class ClipConfigResponse(BaseModel):
    """切片配置读面（页内配置卡用；服务未装配时也可读）。"""

    assembled: bool = Field(..., description="ClippingService 当前是否已装配")
    config: ClippingConfigSchema = Field(..., description="[clipping] 段当前值")
    recording_dir_exists: bool = Field(..., description="录播目录在文件系统上是否存在（未配置为 False）")


class ClipConfigApplyResponse(BaseModel):
    """切片配置保存结果（写盘 + 运行期重装配一起完成）。"""

    success: bool = Field(..., description="写盘与重装配是否都成功")
    message: str = Field(default="", description="给人看的结果说明")
    assembled: bool = Field(..., description="保存后服务是否处于装配态")
    recording_dir_exists: bool = Field(..., description="录播目录在文件系统上是否存在")


class PreviewPrepareRequest(BaseModel):
    """批量发起预览转换的请求体（非 mp4 源后台重封装）。"""

    paths: List[str] = Field(..., min_length=1, description="录播文件绝对路径列表（须在录播目录内）")


class PreviewPrepareResponse(BaseModel):
    """预览转换发起结果。"""

    started: int = Field(..., description="新发起的转换任务数（已就绪/已在转换/路径无效的不计）")


__all__ = [
    "ClipConfigApplyResponse",
    "ClipConfigResponse",
    "ClippedSessionItem",
    "ClippedSessionListResponse",
    "CutRequest",
    "CutResponse",
    "RecordingAssignRequest",
    "RecordingDetachRequest",
    "RecordingFileInfo",
    "RecordingRenameRequest",
    "RecordingRenameResponse",
    "RecordingTimeResponse",
    "SegmentCreateRequest",
    "SegmentListResponse",
    "SegmentMutationResponse",
    "PreviewPrepareRequest",
    "PreviewPrepareResponse",
    "SegmentUpdateRequest",
    "SelectResponse",
    "SessionRecordingsResponse",
    "UnmatchedFileInfo",
]
