"""
VRChatProvider - Output Domain: VRChat OSC 虚拟形象渲染实现

职责:
- 通过 OSC 协议连接到 VRChat
- 接收 Intent 并适配为 VRChat 参数
- 渲染表情参数和手势到 VRChat
- 软降级：python-osc 不可用时 Provider 仍能正常实例化
"""

from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import Field

from src.domains.output.providers.avatar.base import AvatarProviderBase
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.domains.decision.intent import Intent

# python-osc 软降级
try:
    from pythonosc.udp_client import SimpleUDPClient

    PYTHON_OSC_AVAILABLE = True
except ImportError:
    PYTHON_OSC_AVAILABLE = False
    SimpleUDPClient = None


class VRChatProvider(AvatarProviderBase):
    """
    VRChat Provider 实现

    核心功能:
    - OSC 客户端：发送参数控制命令到 VRChat
    - 表情控制：通过 OSC 参数控制 VRChat 虚拟形象
    - 手势触发：通过 OSC 触发 VRChat 手势（VRCEmote 参数）
    - 软降级：python-osc 不可用时不会导致实例化失败
    """

    # ==================== 情感映射配置 ====================

    # 情感到 VRChat OSC 参数的映射
    EMOTION_MAP: Dict[str, Dict[str, float]] = {
        "neutral": {},  # 默认状态，不设置参数
        "happy": {"MouthSmile": 1.0},
        "sad": {"MouthSmile": -0.3, "EyeOpen": 0.7},
        "angry": {"EyeOpen": 0.6, "MouthSmile": -0.5},
        "surprised": {"EyeOpen": 1.0, "MouthOpen": 0.5},
        "confused": {"EyeOpen": 0.7, "MouthOpen": 0.2},
        "scared": {"EyeOpen": 0.5, "MouthOpen": 0.3},
        "love": {"MouthSmile": 0.8, "EyeOpen": 0.9},
        "shy": {"MouthSmile": 0.3, "EyeOpen": 0.8},
        "excited": {"MouthSmile": 1.0, "EyeOpen": 1.0},
    }

    # 手势名称到 VRChat OSC 整数值的映射
    # 参考: https://docs.vrchat.com/docs/osc-as-a-parameter#emotes
    GESTURE_MAP: Dict[str, int] = {
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

    class ConfigSchema(BaseProviderConfig):
        """VRChat 输出 Provider 配置"""

        type: str = "vrchat"

        # VRChat OSC 连接配置
        vrc_host: str = Field(default="127.0.0.1", description="VRChat OSC 主机地址")
        vrc_out_port: int = Field(default=9000, ge=1, le=65535, description="VRChat OSC 端口")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 VRChat Provider

        Args:
            config: Provider 配置（来自 [providers.output.vrchat]）
        """
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # VRChat OSC 连接配置
        self.vrc_host = self.typed_config.vrc_host
        self.vrc_out_port = self.typed_config.vrc_out_port

        # OSC 客户端
        self.osc_client: Optional["SimpleUDPClient"] = None

        # python-osc 可用性检查（软降级）
        self._osc_enabled = PYTHON_OSC_AVAILABLE
        if not self._osc_enabled:
            self.logger.warning(
                "python-osc 库不可用，VRChatProvider 将在禁用状态下运行。请安装 python-osc: `uv add python-osc`"
            )

        # 统计信息
        self.render_count = 0
        self.error_count = 0

        self.logger.info(f"VRChatProvider 初始化 - VRChat OSC: {self.vrc_host}:{self.vrc_out_port}")

    # ==================== AvatarProviderBase 抽象方法实现 ====================

    def _adapt_intent(self, intent: "Intent") -> Dict[str, Any]:
        """
        适配 Intent 为 VRChat 特定参数

        Args:
            intent: 平台无关的 Intent

        Returns:
            VRChat 参数字典，包含:
            - expressions: Dict[str, float] - VRChat OSC 参数值
            - gestures: List[str] - 手势名称列表
        """
        from src.modules.types import EmotionType

        result = {
            "expressions": {},
            "gestures": [],
        }

        # 1. 适配情感为 VRChat OSC 参数
        emotion_str = intent.emotion.value if isinstance(intent.emotion, EmotionType) else str(intent.emotion)
        if emotion_str in self.EMOTION_MAP:
            result["expressions"].update(self.EMOTION_MAP[emotion_str])
            self.logger.debug(f"情感映射: {emotion_str} -> {self.EMOTION_MAP[emotion_str]}")

        # 2. 适配动作为手势
        for action in intent.actions:
            action_type_str = action.type.value if hasattr(action.type, "value") else str(action.type)
            gesture_name = action.params.get("gesture_name", "")

            # 检查是否是手势动作（HOTKEY 或 CUSTOM 类型）
            if action_type_str in ("hotkey", "custom") and gesture_name:
                result["gestures"].append(gesture_name)
                self.logger.debug(f"手势映射: {gesture_name}")

        self.logger.debug(f"Intent 适配结果: expressions={result['expressions']}, gestures={result['gestures']}")
        return result

    async def _render_internal(self, params: Dict[str, Any]) -> None:
        """
        渲染到 VRChat 平台

        Args:
            params: _adapt_intent() 返回的 VRChat 参数字典
        """
        # 检查 OSC 可用性和连接状态
        if not self._osc_enabled:
            self.logger.warning("python-osc 不可用，跳过渲染")
            return

        if not self._is_connected:
            self.logger.warning("未连接到 VRChat，跳过渲染")
            return

        try:
            expressions = params.get("expressions", {})
            gestures = params.get("gestures", [])

            # 1. 发送表情参数
            for param_name, param_value in expressions.items():
                self._send_parameter(param_name, param_value)
                self.logger.debug(f"设置 VRChat 参数: {param_name} = {param_value}")

            # 2. 触发手势
            for gesture_name in gestures:
                self._trigger_gesture(gesture_name)

            self.render_count += 1

        except Exception as e:
            self.logger.error(f"VRChat 渲染失败: {e}", exc_info=True)
            self.error_count += 1
            raise RuntimeError(f"VRChat 渲染失败: {e}") from e

    async def _connect(self) -> None:
        """连接到 VRChat OSC"""
        if not self._osc_enabled:
            self.logger.warning("python-osc 不可用，无法连接到 VRChat")
            return

        try:
            self.logger.info(f"正在连接到 VRChat OSC... (Host: {self.vrc_host}, Port: {self.vrc_out_port})")

            # 创建 OSC 客户端
            self.osc_client = SimpleUDPClient(self.vrc_host, self.vrc_out_port)
            self._is_connected = True

            self.logger.info(f"VRChat OSC 客户端已创建: {self.vrc_host}:{self.vrc_out_port}")

        except Exception as e:
            self.logger.error(f"创建 VRChat OSC 客户端失败: {e}", exc_info=True)
            self._is_connected = False
            raise

    async def _disconnect(self) -> None:
        """断开 VRChat OSC 连接"""
        self.logger.info("正在断开 VRChat OSC 连接...")

        # 清理 OSC 客户端
        self.osc_client = None
        self._is_connected = False

        self.logger.info("VRChat OSC 连接已断开")

    # ==================== 私有辅助方法 ====================

    def _send_parameter(self, param_name: str, value: float) -> None:
        """
        发送 OSC 参数消息到 VRChat

        Args:
            param_name: 参数名称（如 "EyeOpen"）
            value: 参数值（0.0 到 1.0）
        """
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接，无法发送参数")
            return

        try:
            # VRChat OSC 地址格式: /avatar/parameters/{param_name}
            address = f"/avatar/parameters/{param_name}"
            self.osc_client.send_message(address, value)
            self.logger.debug(f"发送 OSC: {address} = {value}")
        except Exception as e:
            self.logger.error(f"发送 OSC 参数失败: {param_name} = {value}: {e}")

    def _trigger_gesture(self, gesture_name: str) -> None:
        """
        触发 VRChat 手势

        Args:
            gesture_name: 手势名称（如 "Wave", "Peace"）
        """
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接，无法触发手势")
            return

        try:
            # 映射手势名称到整数值
            if gesture_name not in self.GESTURE_MAP:
                self.logger.warning(f"未知的手势名称: {gesture_name}")
                return

            gesture_value = self.GESTURE_MAP[gesture_name]

            # VRChat OSC 地址: /avatar/parameters/VRCEmote
            address = "/avatar/parameters/VRCEmote"
            self.osc_client.send_message(address, gesture_value)
            self.logger.info(f"触发 VRChat 手势: {gesture_name} (value: {gesture_value})")
        except Exception as e:
            self.logger.error(f"触发 VRChat 手势失败: {gesture_name}: {e}")

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取 Provider 统计信息"""
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "osc_enabled": self._osc_enabled,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "vrc_host": self.vrc_host,
            "vrc_out_port": self.vrc_out_port,
        }
