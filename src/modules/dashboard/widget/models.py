"""
弹幕小部件数据模型

定义用于弹幕小部件显示的消息类型。
用于 Warudo 等虚拟形象软件的网页道具场景。
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SubtitleWidgetMessage(BaseModel):
    text: str = Field(description="字幕文本")
    timestamp: datetime = Field(default_factory=datetime.now, description="消息时间")
    duration_ms: int = Field(default=5000, ge=1000, le=30000, description="显示持续时间（毫秒）")


class SubtitleWidgetConfig(BaseModel):
    enabled: bool = Field(default=True, description="是否启用字幕小部件")
    enable_html_page: bool = Field(default=False, description="是否启用后端 HTML 页面")
    max_messages: int = Field(default=10, ge=1, le=50, description="最大字幕显示数量")
    auto_hide_after_ms: int = Field(default=5000, ge=1000, le=30000, description="自动隐藏时间（毫秒）")
    font_size: int = Field(default=32, ge=12, le=72, description="字体大小")
    font_color: str = Field(default="#ffffff", description="字体颜色")
    background_color: str = Field(default="rgba(0,0,0,0.45)", description="背景颜色")
    border_color: str = Field(default="#ff8800", description="左边边框颜色（橙色）")
    position: str = Field(default="bottom", description="位置: top, bottom, center")


class MessageType(str, Enum):
    """消息类型枚举"""

    DANMAKU = "danmaku"  # 普通弹幕
    GIFT = "gift"  # 礼物
    SUPER_CHAT = "superchat"  # SuperChat
    GUARD = "guard"  # 大航海
    ENTER = "enter"  # 进入直播间
    REPLY = "reply"  # AI 回复


class DanmakuWidgetMessage(BaseModel):
    """
    弹幕小部件消息

    用于在前端显示的统一消息格式。
    """

    user_name: str = Field(description="用户昵称")
    user_id: str = Field(default="", description="用户 ID")
    content: str = Field(description="消息内容")
    message_type: MessageType = Field(description="消息类型")
    timestamp: datetime = Field(default_factory=datetime.now, description="消息时间")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="重要性评分")

    # 礼物专属字段
    gift_name: Optional[str] = Field(default=None, description="礼物名称")
    gift_count: Optional[int] = Field(default=None, description="礼物数量")
    gift_price: Optional[float] = Field(default=None, description="礼物单价")

    # SuperChat 专属字段
    sc_price: Optional[float] = Field(default=None, description="SC 金额")
    sc_message: Optional[str] = Field(default=None, description="SC 消息内容")

    # 大航海专属字段
    guard_level: Optional[int] = Field(default=None, description="大航海等级")

    # 来源信息
    platform: str = Field(default="bilibili", description="平台来源")
    room_id: Optional[str] = Field(default=None, description="直播间 ID")

    class Config:
        use_enum_values = True

    def to_display_text(self) -> str:
        """生成显示文本"""
        if self.message_type == MessageType.GIFT:
            return f"送出 {self.gift_name or '礼物'} x{self.gift_count or 1}"
        elif self.message_type == MessageType.SUPER_CHAT:
            return f"¥{self.sc_price or 0} {self.sc_message or self.content}"
        elif self.message_type == MessageType.GUARD:
            guard_names = {1: "总督", 2: "提督", 3: "舰长"}
            guard_name = guard_names.get(self.guard_level or 3, "大航海")
            return f"开通了 {guard_name}"
        elif self.message_type == MessageType.ENTER:
            return "进入了直播间"
        else:
            return self.content


class DanmakuWidgetConfig(BaseModel):
    """弹幕小部件配置"""

    enabled: bool = Field(default=True, description="是否启用弹幕小部件")
    enable_html_page: bool = Field(default=False, description="是否启用后端 HTML 页面（若为 false，则使用 Vue 页面）")
    max_messages: int = Field(default=30, ge=5, le=100, description="最大消息数量")
    show_danmaku: bool = Field(default=True, description="显示普通弹幕")
    show_gift: bool = Field(default=True, description="显示礼物")
    show_super_chat: bool = Field(default=True, description="显示 SuperChat")
    show_guard: bool = Field(default=True, description="显示大航海")
    show_enter: bool = Field(default=False, description="显示进入直播间")
    show_reply: bool = Field(default=True, description="显示 AI 回复")
    show_subtitle: bool = Field(default=True, description="显示字幕（AI 回复文本）")
    min_importance: float = Field(default=0.0, ge=0.0, le=1.0, description="最小重要性过滤")
