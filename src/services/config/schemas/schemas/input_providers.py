"""输入Provider配置Schema定义

定义所有输入Provider的Pydantic配置模型。
"""

from typing import Optional, List, Literal
from pydantic import Field, field_validator

from .base import BaseProviderConfig


class ConsoleInputProviderConfig(BaseProviderConfig):
    """控制台输入Provider配置"""

    type: Literal["console_input"] = "console_input"
    user_id: str = Field(default="console_user", description="用户ID")
    user_nickname: str = Field(default="控制台", description="用户昵称")


class BiliDanmakuProviderConfig(BaseProviderConfig):
    """Bilibili弹幕输入Provider配置"""

    type: Literal["bili_danmaku"] = "bili_danmaku"
    room_id: int = Field(..., description="直播间ID", gt=0)
    poll_interval: int = Field(default=3, description="轮询间隔（秒）", ge=1)
    message_config: dict = Field(default_factory=dict, description="消息配置")


class BiliDanmakuOfficialProviderConfig(BaseProviderConfig):
    """Bilibili官方弹幕输入Provider配置"""

    type: Literal["bili_danmaku_official"] = "bili_danmaku_official"
    id_code: str = Field(..., description="直播间ID代码")
    app_id: str = Field(..., description="应用ID")
    access_key: str = Field(..., description="访问密钥")
    access_key_secret: str = Field(..., description="访问密钥Secret")
    api_host: str = Field(
        default="https://live-open.biliapi.com",
        description="API主机地址"
    )
    message_cache_size: int = Field(
        default=1000,
        description="消息缓存大小",
        ge=1
    )
    context_tags: Optional[List[str]] = Field(
        default=None,
        description="Prompt上下文标签"
    )
    enable_template_info: bool = Field(
        default=False,
        description="启用模板信息"
    )
    template_items: dict = Field(
        default_factory=dict,
        description="模板项"
    )


class BiliDanmakuOfficialMaiCraftProviderConfig(BaseProviderConfig):
    """Bilibili官方弹幕+Minecraft转发输入Provider配置"""

    type: Literal["bili_danmaku_official_maicraft"] = "bili_danmaku_official_maicraft"
    id_code: str = Field(..., description="直播间ID代码")
    app_id: str = Field(..., description="应用ID")
    access_key: str = Field(..., description="访问密钥")
    access_key_secret: str = Field(..., description="访问密钥Secret")
    api_host: str = Field(
        default="https://live-open.biliapi.com",
        description="API主机地址"
    )
    message_cache_size: int = Field(
        default=1000,
        description="消息缓存大小",
        ge=1
    )
    context_tags: Optional[List[str]] = Field(
        default=None,
        description="Prompt上下文标签"
    )
    forward_ws_url: Optional[str] = Field(
        default=None,
        description="转发目标WebSocket URL"
    )
    forward_enabled: bool = Field(
        default=True,
        description="启用WebSocket转发"
    )

    @field_validator("forward_ws_url")
    @classmethod
    def validate_forward_ws_url(cls, v: Optional[str], info) -> Optional[str]:
        """验证WebSocket URL格式"""
        if v is not None and not v.startswith(("ws://", "wss://")):
            raise ValueError("forward_ws_url必须以ws://或wss://开头")
        return v


class MockDanmakuProviderConfig(BaseProviderConfig):
    """模拟弹幕输入Provider配置"""

    type: Literal["mock_danmaku"] = "mock_danmaku"
    log_file_path: str = Field(
        default="msg_default.jsonl",
        description="日志文件路径"
    )
    send_interval: float = Field(
        default=1.0,
        description="发送间隔（秒）",
        ge=0.1
    )
    loop_playback: bool = Field(
        default=True,
        description="循环播放"
    )
    start_immediately: bool = Field(
        default=True,
        description="立即开始"
    )


class ReadPingmuProviderConfig(BaseProviderConfig):
    """屏幕读评输入Provider配置"""

    type: Literal["read_pingmu"] = "read_pingmu"
    api_key: str = Field(default="", description="API密钥")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="API基础URL"
    )
    model_name: str = Field(
        default="qwen2.5-vl-72b-instruct",
        description="模型名称"
    )
    screenshot_interval: float = Field(
        default=0.3,
        description="截图间隔（秒）",
        ge=0.1
    )
    diff_threshold: float = Field(
        default=25.0,
        description="差异阈值",
        ge=0.0
    )
    check_window: int = Field(
        default=3,
        description="检查窗口",
        ge=1
    )
    max_cache_size: int = Field(
        default=5,
        description="最大缓存大小",
        ge=1
    )
    max_cached_images: int = Field(
        default=5,
        description="最大缓存图像数",
        ge=1
    )


class MainosabaProviderConfig(BaseProviderConfig):
    """Mainosaba游戏画面采集输入Provider配置"""

    type: Literal["mainosaba"] = "mainosaba"
    full_screen: bool = Field(
        default=True,
        description="全屏截图"
    )
    game_region: Optional[List[int]] = Field(
        default=None,
        description="游戏区域 [x1, y1, x2, y2]"
    )
    check_interval: int = Field(
        default=1,
        description="检查间隔（秒）",
        ge=1
    )
    screenshot_min_interval: float = Field(
        default=0.5,
        description="最小截图间隔（秒）",
        ge=0.1
    )
    response_timeout: int = Field(
        default=10,
        description="响应超时（秒）",
        ge=1
    )
    control_method: Literal["mouse_click", "enter_key", "space_key"] = Field(
        default="mouse_click",
        description="游戏控制方式"
    )
    click_position: List[int] = Field(
        default_factory=lambda: [1920 // 2, 1080 // 2],
        description="点击位置 [x, y]"
    )

    @field_validator("game_region")
    @classmethod
    def validate_game_region(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        """验证游戏区域格式"""
        if v is not None:
            if len(v) != 4:
                raise ValueError("game_region必须包含4个值 [x1, y1, x2, y2]")
            if any(x < 0 for x in v):
                raise ValueError("game_region坐标不能为负数")
        return v

    @field_validator("click_position")
    @classmethod
    def validate_click_position(cls, v: List[int]) -> List[int]:
        """验证点击位置格式"""
        if len(v) != 2:
            raise ValueError("click_position必须包含2个值 [x, y]")
        if any(x < 0 for x in v):
            raise ValueError("click_position坐标不能为负数")
        return v


# 注意：RemoteStreamProvider 已移动到 output 域
# 请使用 src.services.config.schemas.schemas.output_providers.RemoteStreamOutputProviderConfig


# 类型别名，用于导入
InputProviderConfig = (
    ConsoleInputProviderConfig |
    BiliDanmakuProviderConfig |
    BiliDanmakuOfficialProviderConfig |
    BiliDanmakuOfficialMaiCraftProviderConfig |
    MockDanmakuProviderConfig |
    ReadPingmuProviderConfig |
    MainosabaProviderConfig
)
