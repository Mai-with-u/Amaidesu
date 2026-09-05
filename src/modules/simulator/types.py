"""模拟直播流收集器的内部数据类型。"""

# pyright: reportDeprecated=false

from enum import Enum
from typing import ClassVar, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PersonaRole(str, Enum):
    """模拟观众的人设角色。"""

    FAN = "fan"
    TEASER = "teaser"
    NEWCOMER = "newcomer"
    HATER = "hater"
    VETERAN = "veteran"
    PASSERBY = "passerby"


class Persona(BaseModel):
    """模拟观众的人设配置及运行时状态。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    user_id: str
    user_nickname: str
    role: PersonaRole
    personality: str
    speaking_style: str
    fans_medal_level: int = Field(default=0, ge=0, le=40)
    guard_level: int = Field(default=0, ge=0, le=3)
    # 上下文窗口大小覆盖（None=按角色默认；表达"该角色对直播间的关注度"）
    context_window_size: Optional[int] = Field(default=None, ge=1, le=50)
    is_temporary: bool = False
    is_active: bool = True
    messages_generated: int = 0


class GiftItem(BaseModel):
    """模拟礼物配置。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    gift_id: str
    gift_name: str
    category: str
    weight: int = Field(default=1, ge=1)
    data_type: str
    sc_amount_rmb: Optional[int] = None


class StreamerContextSnapshot(BaseModel):
    """生成消息时使用的主播上下文快照。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    recent_messages: List[str] = Field(default_factory=list)
    recent_emotion: Optional[str] = None
    last_activity_at_ms: int = 0
    is_online: bool = False
    has_new_activity_since_last_check: bool = False


class GeneratedMessage(BaseModel):
    """模拟器生成的单条消息。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    text: str
    persona: Persona
    data_type: str
    gift: Optional[GiftItem] = None
    sc_amount_rmb: Optional[int] = None
    tokens_used: int = 0


class BurstState(BaseModel):
    """消息爆发模式的运行时状态。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    is_active: bool = False
    started_at_ms: int = 0
    last_triggered_at_ms: int = 0
