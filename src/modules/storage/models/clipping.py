"""切片段的数据契约（clip_segments 表）。"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# 段来源：ai = AI 选段提议；manual = 运营手建（或对 AI 段的手调结果）
ClipSegmentOrigin = Literal["ai", "manual"]

# 段状态：suggested 提议待确认 → confirmed 运营确认待切 → cutting 切割中
#        → done 已出片 / failed 切割失败；dismissed 运营弃用
ClipSegmentStatus = Literal["suggested", "confirmed", "cutting", "done", "failed", "dismissed"]


class ClipSegment(BaseModel):
    """一行切片段（读面契约）。"""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="行主键")
    live_session_id: int = Field(..., description="所属场次主键")
    origin: ClipSegmentOrigin = Field(..., description="来源：ai=AI 提议 / manual=运营手建")
    start_ms: int = Field(..., description="段起点（墙钟 Unix epoch 毫秒，场时间线坐标）")
    end_ms: int = Field(..., description="段终点（墙钟 Unix epoch 毫秒，场时间线坐标）")
    title: str = Field(default="", description="片段标题（切片产物文件名素材）")
    reason: str = Field(default="", description="AI 提议这一段的理由（运营手建为空）")
    status: ClipSegmentStatus = Field(default="suggested", description="切割状态")
    output_path: str = Field(default="", description="切割产物路径（done 后回填；跨文件多产物用换行分隔）")
    error_message: str = Field(default="", description="切割失败原因（failed 后回填）")
    created_at_ms: int = Field(..., description="创建时刻（Unix epoch 毫秒）")
    updated_at_ms: int = Field(..., description="最后改动时刻（Unix epoch 毫秒）")


class NewClipSegment(BaseModel):
    """新写一行切片段的入参（AI 选段与运营手建共用；AI 段走 replace_ai_segments）。"""

    model_config = ConfigDict(extra="forbid")

    live_session_id: int = Field(..., description="所属场次主键")
    origin: ClipSegmentOrigin = Field(default="manual", description="来源：ai=AI 提议 / manual=运营手建")
    start_ms: int = Field(..., description="段起点（墙钟 Unix epoch 毫秒）")
    end_ms: int = Field(..., description="段终点（墙钟 Unix epoch 毫秒）")
    title: str = Field(default="", description="片段标题")
    reason: str = Field(default="", description="AI 提议理由")
    status: ClipSegmentStatus = Field(default="suggested", description="初始状态")


class ClipSegmentUpdate(BaseModel):
    """改一行切片段的入参（运营在 WebUI 手调；None 字段不动）。"""

    model_config = ConfigDict(extra="forbid")

    start_ms: Optional[int] = Field(default=None, description="段起点（墙钟 Unix epoch 毫秒）")
    end_ms: Optional[int] = Field(default=None, description="段终点（墙钟 Unix epoch 毫秒）")
    title: Optional[str] = Field(default=None, description="片段标题")
    status: Optional[ClipSegmentStatus] = Field(default=None, description="切割状态")


__all__ = [
    "ClipSegment",
    "ClipSegmentOrigin",
    "ClipSegmentStatus",
    "ClipSegmentUpdate",
    "NewClipSegment",
]
