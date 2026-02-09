"""
VRChat Adapter - VRChat 平台适配器

通过 OSC 协议与 VRChat 通信，控制虚拟形象参数。
"""

from typing import Dict, Any, Optional
import threading

from ..base import PlatformAdapter

try:
    from pythonosc.udp_client import SimpleUDPClient
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer

    PYTHON_OSC_AVAILABLE = True
except ImportError:
    PYTHON_OSC_AVAILABLE = False
    SimpleUDPClient = None
    Dispatcher = None
    ThreadingOSCUDPServer = None

from src.core.utils.logger import get_logger
from pydantic import BaseModel, Field


class VRChatAdapterConfig(BaseModel):
    """VRChat 适配器配置"""

    vrc_host: str = Field(default="127.0.0.1", description="VRChat OSC 主机地址")
    vrc_out_port: int = Field(default=9000, description="VRChat OSC 发送端口（接收客户端发送的数据）")
    vrc_in_port: int = Field(default=9001, description="VRChat OSC 接收端口（客户端发送数据到 VRChat）")
    enable_server: bool = Field(default=False, description="是否启动 OSC 服务器接收 VRChat 数据")
    auto_send_params: bool = Field(default=True, description="是否自动发送参数更新")
    local_host: str = Field(default="127.0.0.1", description="本地监听地址")


class VRChatAdapter(PlatformAdapter):
    """VRChat 适配器

    通过 OSC 协议与 VRChat 通信，支持：
    - OSC 客户端：发送参数控制命令到 VRChat
    - OSC 服务器（可选）：接收来自 VRChat 的数据
    - 参数映射：将抽象参数翻译为 VRChat Avatar Parameters
    - 热键触发：通过 OSC 消息触发 VRChat 手势/动作
    """

    # 抽象参数 → VRChat Avatar Parameters 映射
    # 参考: VRChat Avatar Parameters 规范
    PARAM_TRANSLATION = {
        "smile": "MouthSmile",
        "eye_open": "EyeOpen",
        "mouth_open": "MouthOpen",
        "brow_down": "BrowDownLeft",
        "brow_up": "BrowUpLeft",
        "brow_angry": "BrowAngryLeft",
        "eye_x": "EyeX",
        "eye_y": "EyeY",
        # VRChat 通常只有单侧眼睛参数，但某些模型可能有左右分离
    }

    # VRChat 手势值映射（用于 VRCEmote 参数）
    GESTURE_MAP = {
        "Neutral": 0,
        "Wave": 1,
        "Peace": 2,
        "ThumbsUp": 3,
        "RocknRoll": 4,
        "HandGun": 5,
        "Point": 6,
        "Victory": 7,
        "Cross": 8,
    }

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 VRChat 适配器

        Args:
            config: 配置字典，需包含：
                - vrc_host: VRChat OSC 主机地址
                - vrc_out_port: VRChat OSC 发送端口
                - vrc_in_port: VRChat OSC 接收端口
                - enable_server: 是否启动 OSC 服务器
                - auto_send_params: 是否自动发送参数更新
        """
        super().__init__("vrchat", config)

        if not PYTHON_OSC_AVAILABLE:
            raise ImportError("python-osc is required for VRChatAdapter. Install with: pip install python-osc")

        # 使用 Pydantic 验证配置
        self.typed_config = VRChatAdapterConfig(**config)
        self.logger = get_logger("VRChatAdapter")

        # OSC 客户端（用于发送数据到 VRChat）
        self.osc_client: Optional[SimpleUDPClient] = None

        # OSC 服务器（用于接收来自 VRChat 的数据）
        self.osc_server: Optional[ThreadingOSCUDPServer] = None
        self._server_thread: Optional[threading.Thread] = None

        # 热键/手势名称列表
        self._registered_gestures: set[str] = set()

        self.logger.info(
            f"VRChatAdapter 初始化 - VRChat OSC: {self.typed_config.vrc_host}:{self.typed_config.vrc_in_port}"
        )

    def translate_params(self, abstract_params: Dict[str, float]) -> Dict[str, float]:
        """翻译抽象参数为 VRChat Avatar Parameters

        Args:
            abstract_params: 抽象参数字典

        Returns:
            VRChat Avatar Parameters 字典
        """
        vrc_params = {}
        for name, value in abstract_params.items():
            if name in self.PARAM_TRANSLATION:
                vrc_param = self.PARAM_TRANSLATION[name]
                if vrc_param:
                    vrc_params[vrc_param] = value
            else:
                # 未映射的参数直接使用原名
                vrc_params[name] = value

        return vrc_params

    async def connect(self) -> bool:
        """连接到 VRChat（初始化 OSC 客户端）"""
        try:
            # 创建 OSC 客户端
            self.osc_client = SimpleUDPClient(self.typed_config.vrc_host, self.typed_config.vrc_in_port)
            self._is_connected = True
            self.logger.info(f"OSC 客户端已创建: {self.typed_config.vrc_host}:{self.typed_config.vrc_in_port}")

            # 启动 OSC 服务器（可选）
            if self.typed_config.enable_server:
                self._start_osc_server()

            return True

        except Exception as e:
            self.logger.error(f"VRChatAdapter 连接失败: {e}", exc_info=True)
            return False

    async def disconnect(self) -> bool:
        """断开 VRChat 连接"""
        try:
            # 停止 OSC 服务器
            if self.osc_server:
                self.osc_server.shutdown()
                self.osc_server = None
                self._server_thread = None
                self.logger.info("OSC 服务器已停止")

            self._is_connected = False
            self.osc_client = None
            self.logger.info("VRChatAdapter 已断开")
            return True

        except Exception as e:
            self.logger.error(f"VRChatAdapter 断开失败: {e}", exc_info=True)
            return False

    async def set_parameters(self, params: Dict[str, float]) -> bool:
        """设置表情参数

        Args:
            params: 抽象参数字典（会被自动翻译为 VRChat 参数）

        Returns:
            是否设置成功
        """
        if not self._is_connected or not self.osc_client:
            self.logger.warning("VRChatAdapter 未连接")
            return False

        try:
            # 翻译抽象参数为 VRChat 参数
            vrc_params = self.translate_params(params)

            # 批量设置参数
            for param_name, value in vrc_params.items():
                await self._send_osc(f"/avatar/parameters/{param_name}", value)

            self.logger.debug(f"VRChat 参数已设置: {vrc_params}")
            return True

        except Exception as e:
            self.logger.error(f"设置 VRChat 参数失败: {e}", exc_info=True)
            return False

    async def trigger_hotkey(self, hotkey_name: str) -> bool:
        """触发 VRChat 热键/手势

        VRChat 支持的手势（通过 VRCEmote 参数）:
        - Neutral, Wave, Peace, ThumbsUp, RocknRoll, HandGun, Point, Victory, Cross

        Args:
            hotkey_name: 热键/手势名称（如 "Wave", "Peace"）

        Returns:
            是否触发成功
        """
        if not self._is_connected or not self.osc_client:
            self.logger.warning("VRChatAdapter 未连接")
            return False

        try:
            # 获取手势值
            gesture_value = self.GESTURE_MAP.get(hotkey_name)
            if gesture_value is None:
                self.logger.warning(f"未知的手势: {hotkey_name}，支持的手势: {list(self.GESTURE_MAP.keys())}")
                return False

            # 使用 VRCEmote 参数触发手势
            await self._send_osc("/avatar/parameters/VRCEmote", gesture_value)

            self.logger.info(f"VRChat 手势已触发: {hotkey_name} (值: {gesture_value})")
            return True

        except Exception as e:
            self.logger.error(f"触发 VRChat 手势失败: {e}", exc_info=True)
            return False

    def register_gesture(self, gesture_name: str) -> None:
        """注册手势名称（用于后续触发）

        Args:
            gesture_name: 手势名称
        """
        self._registered_gestures.add(gesture_name)
        self.logger.debug(f"已注册手势: {gesture_name}")

    def get_registered_gestures(self) -> set[str]:
        """获取已注册的手势列表

        Returns:
            手势名称集合
        """
        return self._registered_gestures.copy()

    def get_supported_gestures(self) -> list[str]:
        """获取 VRChat 支持的所有手势

        Returns:
            手势名称列表
        """
        return list(self.GESTURE_MAP.keys())

    async def _send_osc(self, address: str, value: float) -> bool:
        """发送 OSC 消息到 VRChat

        Args:
            address: OSC 地址（如 "/avatar/parameters/EyeOpen"）
            value: 参数值

        Returns:
            是否发送成功
        """
        if not self._is_connected or not self.osc_client:
            return False

        try:
            self.osc_client.send_message(address, value)
            self.logger.debug(f"发送 OSC: {address} = {value}")
            return True

        except Exception as e:
            self.logger.error(f"发送 OSC 消息失败: {e}", exc_info=True)
            return False

    def _start_osc_server(self) -> None:
        """启动 OSC 服务器（接收来自 VRChat 的数据）"""
        try:
            dispatcher = Dispatcher()

            # 注册默认处理器
            dispatcher.set_default_handler(self._osc_default_handler)

            # 创建服务器
            self.osc_server = ThreadingOSCUDPServer(
                (self.typed_config.local_host, self.typed_config.vrc_out_port), dispatcher
            )

            # 在后台线程中运行服务器
            self._server_thread = threading.Thread(target=self.osc_server.serve_forever)
            self._server_thread.daemon = True
            self._server_thread.start()

            self.logger.info(f"OSC 服务器已启动: {self.typed_config.local_host}:{self.typed_config.vrc_out_port}")

        except Exception as e:
            self.logger.error(f"启动 OSC 服务器失败: {e}", exc_info=True)

    def _osc_default_handler(self, address: str, *args) -> None:
        """OSC 消息默认处理器

        Args:
            address: OSC 地址
            *args: OSC 参数
        """
        self.logger.debug(f"收到 OSC 消息: {address} {args}")

        # 可以在这里处理来自 VRChat 的数据
        # 例如：触发事件、更新状态等
        # 未来可以集成到事件系统中
