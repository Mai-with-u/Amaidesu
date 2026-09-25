"""模拟器 API Schema

定义世界模拟器控制面端点的请求/响应模型；字段形状对照
``SimulatorService`` / ``PersonaPool`` / ``GiftGenerator`` 的实际产出。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SimulatorStartRequest(BaseModel):
    """启动请求体（replay 模式的录制日期覆盖，其余模式忽略）。"""

    replay_date: Optional[str] = Field(default=None, description="录制日期 YYYY-MM-DD（replay 模式用）")


class PersonaCreateRequest(BaseModel):
    """新增常驻人设请求体（user_id 由服务端生成）。"""

    user_nickname: str = Field(min_length=1, max_length=50)
    role: str = Field(pattern="^(fan|teaser|newcomer|hater|veteran|passerby)$")
    personality: str = Field(min_length=1)
    speaking_style: str = Field(min_length=1)
    fans_medal_level: int = Field(default=0, ge=0, le=40)
    guard_level: int = Field(default=0, ge=0, le=3)


class PersonaUpdateRequest(BaseModel):
    """按字段更新常驻人设请求体（None 字段不更新）。"""

    user_nickname: Optional[str] = Field(default=None, min_length=1, max_length=50)
    role: Optional[str] = Field(default=None, pattern="^(fan|teaser|newcomer|hater|veteran|passerby)$")
    personality: Optional[str] = None
    speaking_style: Optional[str] = None
    fans_medal_level: Optional[int] = Field(default=None, ge=0, le=40)
    guard_level: Optional[int] = Field(default=None, ge=0, le=3)
    is_active: Optional[bool] = None


class GiftCreateRequest(BaseModel):
    """新增礼物请求体。"""

    gift_id: str = Field(min_length=1, max_length=64, pattern="^[a-zA-Z0-9_]+$")
    gift_name: str = Field(min_length=1, max_length=50)
    category: str = Field(pattern="^(normal|medium|premium|sc)$")
    weight: int = Field(default=1, ge=1)
    data_type: str = Field(default="gift", pattern="^(gift|super_chat)$")
    sc_amount_rmb: Optional[int] = Field(default=None, ge=1)
    unit_price: int = Field(default=0, ge=0)


class GiftUpdateRequest(BaseModel):
    """按字段更新礼物请求体（None 字段不更新）。"""

    gift_name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    category: Optional[str] = Field(default=None, pattern="^(normal|medium|premium|sc)$")
    weight: Optional[int] = Field(default=None, ge=1)
    data_type: Optional[str] = Field(default=None, pattern="^(gift|super_chat)$")
    sc_amount_rmb: Optional[int] = Field(default=None, ge=1)
    unit_price: Optional[int] = Field(default=None, ge=0)


class SimulatorReplayProgress(BaseModel):
    """replay 模式回放进度（非 replay 模式为 null）。"""

    date: Optional[str] = Field(default=None, description="回放录制日期 YYYY-MM-DD")
    total: int = Field(default=0, description="本场录制消息总数")
    remaining: int = Field(default=0, description="尚未回放的消息数")


class SimulatorStatusResponse(BaseModel):
    """模拟器状态响应（enabled=false 时 is_available=false，不抛 404）。"""

    enabled: bool = Field(description="[simulator].enabled 配置开关")
    is_available: bool = Field(description="SimulatorService 实例是否存在（setup 后即 True）")
    is_running: bool = Field(description="当前是否在世界循环里")
    mode: str = Field(description="当前运行模式（off/generate/replay）")
    replay_progress: Optional[SimulatorReplayProgress] = Field(default=None, description="replay 模式进度")
    message: str = Field(default="", description="给前端的状态说明")
    config: Dict[str, Any] = Field(default_factory=dict, description="只读配置摘要")


class SimulatorOperationResponse(BaseModel):
    """写操作通用响应包络（不携带附加数据时使用）。"""

    success: bool
    message: str


class SimulatorRunStateResponse(BaseModel):
    """启停控制响应包络（携带运行态快照；未触发的附加字段以 None 占位）。"""

    success: bool
    message: str
    is_running: bool = Field(default=False, description="动作后是否处于运行态")
    mode: Optional[str] = Field(default=None, description="动作后运行模式（仅启动成功时携带）")
    replay_progress: Optional[SimulatorReplayProgress] = Field(default=None, description="回放进度（仅启动成功时携带）")


class ReplayDatesResponse(BaseModel):
    """可回放录制日期列表响应。"""

    dates: List[str] = Field(default_factory=list, description="有弹幕记录的日期（YYYY-MM-DD，时间正序）")


class PersonaOut(BaseModel):
    """常驻人设条目（对照 ``simulator.types.Persona`` 的落库形状）。"""

    user_id: str
    user_nickname: str
    role: str
    personality: str
    speaking_style: str
    fans_medal_level: int
    guard_level: int
    is_temporary: bool = False
    is_active: bool = True
    messages_generated: int = 0


class PersonaListResponse(BaseModel):
    """常驻人设列表响应（pool 未装配时为空列表 + is_available=false）。"""

    personas: List[PersonaOut] = Field(default_factory=list)
    is_available: bool


class PersonaCreateResponse(BaseModel):
    """新增常驻人设响应（成功时携带新增条目）。"""

    success: bool
    message: str
    persona: Optional[PersonaOut] = Field(default=None, description="新增的人设（失败时为 null）")


class GiftOut(BaseModel):
    """礼物目录条目（对照 ``simulator.types.GiftItem`` 的落库形状）。"""

    gift_id: str
    gift_name: str
    category: str
    weight: int
    data_type: str
    sc_amount_rmb: Optional[int] = None
    unit_price: int = 0


class GiftListResponse(BaseModel):
    """礼物目录列表响应（生成器未装配时为空列表 + is_available=false）。"""

    gifts: List[GiftOut] = Field(default_factory=list)
    is_available: bool


class GiftCreateResponse(BaseModel):
    """新增礼物响应（成功时携带新增条目）。"""

    success: bool
    message: str
    gift: Optional[GiftOut] = Field(default=None, description="新增的礼物（失败时为 null）")
