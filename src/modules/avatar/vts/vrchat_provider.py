"""
VRChatProvider - VRChat OSC 虚拟形象工具集

- 引擎：OSC 客户端初始化、参数写入、手势触发
- ToolProvider 协议由本类自身实现
- 暴露的工具：
  - ``vrchat_set_expression``  - 设置 VRChat OSC 表情参数
  - ``vrchat_trigger_gesture`` - 触发 VRChat 手势
  - ``vrchat_get_stats``       - 读取统计信息
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec

# python-osc 软降级
try:
    from pythonosc.udp_client import SimpleUDPClient

    PYTHON_OSC_AVAILABLE = True
except ImportError:
    PYTHON_OSC_AVAILABLE = False
    SimpleUDPClient = None  # type: ignore


_VRCHAT_SET_EXPRESSION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "VRChat OSC 参数名"},
        "value": {"type": "number", "description": "目标值"},
    },
    "required": ["name", "value"],
}

_VRCHAT_TRIGGER_GESTURE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "gesture": {
            "type": "string",
            "description": "VRChat 手势名（Neutral/Wave/Peace/...）",
            "enum": [
                "Neutral",
                "Wave",
                "Peace",
                "ThumbsUp",
                "RocknRoll",
                "HandGun",
                "Point",
                "Victory",
                "Cross",
            ],
        },
    },
    "required": ["gesture"],
}


class VRChatProvider:
    """VRChat 虚拟形象 ToolProvider（OSC 协议）"""

    PROVIDER_NAME = "vrchat"

    # 情感到 VRChat OSC 参数的映射
    EMOTION_MAP: Dict[str, Dict[str, float]] = {
        "neutral": {},
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

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        # 配置
        self.vrc_host: str = str(config.get("vrc_host", "127.0.0.1"))
        self.vrc_out_port: int = int(config.get("vrc_out_port", 9000))

        # OSC 客户端
        self.osc_client: Any = None
        self._osc_enabled = PYTHON_OSC_AVAILABLE
        if not self._osc_enabled:
            self.logger.warning("python-osc 库不可用，VRChatProvider 将在禁用状态下运行")

        self._is_connected = False
        self._has_started = False
        self.render_count = 0
        self.error_count = 0

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="vrchat_set_expression",
                description="VRChat 设置 OSC 表情参数",
                kind="sync",
                provider="builtin",
                parameters_schema=_VRCHAT_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="vrchat_trigger_gesture",
                description="VRChat 触发手势",
                kind="sync",
                provider="builtin",
                parameters_schema=_VRCHAT_TRIGGER_GESTURE_SCHEMA,
            ),
            ToolSpec(
                name="vrchat_get_stats",
                description="读取 VRChat 状态统计",
                kind="sync",
                provider="builtin",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "vrchat_set_expression":
                self._send_parameter(str(args["name"]), float(args["value"]))
                return _ok(n, True)
            if n == "vrchat_trigger_gesture":
                self._trigger_gesture(str(args["gesture"]))
                return _ok(n, True)
            if n == "vrchat_get_stats":
                return _ok(n, True, self.get_stats())
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"VRChat 工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        await self._connect()
        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法 =====

    def _send_parameter(self, param_name: str, value: float) -> None:
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接，无法发送参数")
            return
        try:
            address = f"/avatar/parameters/{param_name}"
            self.osc_client.send_message(address, value)
            self.logger.debug(f"发送 OSC: {address} = {value}")
        except Exception as e:
            self.logger.error(f"发送 OSC 参数失败: {param_name} = {value}: {e}")

    def _trigger_gesture(self, gesture_name: str) -> None:
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接，无法触发手势")
            return
        try:
            if gesture_name not in self.GESTURE_MAP:
                self.logger.warning(f"未知的手势名称: {gesture_name}")
                return
            gesture_value = self.GESTURE_MAP[gesture_name]
            address = "/avatar/parameters/VRCEmote"
            self.osc_client.send_message(address, gesture_value)
            self.logger.debug(f"触发 VRChat 手势: {gesture_name} (value: {gesture_value})")
        except Exception as e:
            self.logger.error(f"触发 VRChat 手势失败: {gesture_name}: {e}")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "osc_enabled": self._osc_enabled,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "vrc_host": self.vrc_host,
            "vrc_out_port": self.vrc_out_port,
        }

    # ===== 内部辅助 =====

    async def _connect(self) -> None:
        if not self._osc_enabled:
            self.logger.warning("python-osc 不可用，无法连接到 VRChat")
            return
        try:
            self.logger.info(f"正在连接到 VRChat OSC... (Host: {self.vrc_host}, Port: {self.vrc_out_port})")
            self.osc_client = SimpleUDPClient(self.vrc_host, self.vrc_out_port)  # type: ignore[misc]
            self._is_connected = True
            self.logger.info(f"VRChat OSC 客户端已创建: {self.vrc_host}:{self.vrc_out_port}")
        except Exception as e:
            self.logger.error(f"创建 VRChat OSC 客户端失败: {e}", exc_info=True)
            self._is_connected = False
            raise

    async def _disconnect(self) -> None:
        self.logger.info("正在断开 VRChat OSC 连接...")
        self.osc_client = None
        self._is_connected = False
        self.logger.info("VRChat OSC 连接已断开")


def _ok(tool_name: str, success: bool, structured: Any = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=bool(success),
        structured_content=structured,
        content="" if structured is None else str(structured),
    )


def _fail(tool_name: str, error_message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=error_message,
    )


def create_vrchat_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> VRChatProvider:
    return VRChatProvider(
        config=config,
        event_bus=event_bus,
    )


def register_vrchat_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> VRChatProvider:
    provider = create_vrchat_provider(
        config=config,
        event_bus=event_bus,
    )
    if hasattr(registry, "register_provider"):
        registry.register_provider(provider)
    else:
        for spec in provider.list_tools():
            registry.register(spec, provider.invoke)
    return provider
