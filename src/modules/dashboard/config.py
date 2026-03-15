"""
Dashboard 配置模型

定义 Dashboard 服务器的配置结构。
"""

from typing import List

from pydantic import BaseModel, Field


class SubtitleWidgetConfigModel(BaseModel):
    enabled: bool = Field(default=True, description="是否启用字幕小部件")
    enable_html_page: bool = Field(default=False, description="是否启用后端 HTML 页面")
    max_messages: int = Field(default=10, ge=1, le=50, description="最大字幕显示数量（用于历史记录）")
    auto_hide_after_ms: int = Field(default=5000, ge=1000, le=30000, description="自动隐藏时间（毫秒）")
    font_size: int = Field(default=32, ge=12, le=72, description="字体大小")
    font_color: str = Field(default="#ffffff", description="字体颜色")
    background_color: str = Field(default="rgba(0,0,0,0.45)", description="背景颜色")
    border_color: str = Field(default="#ff8800", description="左边边框颜色（橙色）")
    position: str = Field(default="bottom", description="位置: top, bottom, center")


class DanmakuWidgetConfigModel(BaseModel):
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
    min_importance: float = Field(default=0.0, ge=0.0, le=1.0, description="最小重要性过滤")


class DashboardConfig(BaseModel):
    """Dashboard 配置"""

    enabled: bool = Field(default=True, description="是否启用 Dashboard")
    host: str = Field(default="127.0.0.1", description="监听地址")
    port: int = Field(default=60214, description="监听端口")
    cors_origins: List[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="CORS 允许的源",
    )
    max_history_messages: int = Field(default=1000, description="内存中保留的最大历史消息数")
    websocket_heartbeat: int = Field(default=30, description="WebSocket 心跳间隔（秒）")
    auto_open_browser: bool = Field(default=True, description="启动时自动打开浏览器")

    danmaku_widget: DanmakuWidgetConfigModel = Field(
        default_factory=DanmakuWidgetConfigModel,
        description="弹幕小部件配置",
    )

    subtitle_widget: SubtitleWidgetConfigModel = Field(
        default_factory=SubtitleWidgetConfigModel,
        description="字幕小部件配置",
    )
