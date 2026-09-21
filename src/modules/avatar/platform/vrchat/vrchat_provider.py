"""
VRChatProvider - VRChat OSC 虚拟形象工具集

- 引擎：OSC 客户端初始化、参数写入、手势触发
- ToolProvider 协议由本类自身实现
- 暴露的工具（同语义跨平台同名同参数形状，契约见 ``avatar.protocol``）：
  - ``vrchat_set_expression``        - 设置情绪（VRChat 无标准表情参数体系，返回未应用结果）
  - ``vrchat_list_preset_actions``   - 列出可演预设（手势 enum）
  - ``vrchat_trigger_preset_action`` - 触发预设手势（未知名随结果返回目录）
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import Field

from src.modules.avatar.speech_binding import bind_speech_emotion
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

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
        "emotion": {
            "type": "string",
            "enum": [e.value for e in Emotion],
            "description": "情绪（17 枚举值之一，小写）",
        },
        "intensity": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "default": 0.5,
            "description": "情绪强度（0.0–1.0）",
        },
    },
    "required": ["emotion"],
}

_VRCHAT_TRIGGER_PRESET_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "预设动作名（取自 vrchat_list_preset_actions 返回的手势目录）",
        },
    },
    "required": ["action"],
}


class VRChatProvider(BaseToolProvider):
    """VRChat 虚拟形象 ToolProvider（OSC 协议）"""

    PROVIDER_NAME = "vrchat"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "avatar"

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

    class ConfigSchema(BaseConfig):
        """VRChat OSC 配置（host + out port）"""

        type: str = "vrchat"
        vrc_host: str = Field(default="127.0.0.1", description="VRChat OSC 主机地址")
        vrc_out_port: int = Field(default=9000, ge=1, le=65535, description="VRChat OSC 输出端口")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        # 配置（typed；空 dict = 全默认；失败 log+raise）
        try:
            self.typed_config = self.ConfigSchema.from_dict(config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise
        self.vrc_host: str = self.typed_config.vrc_host
        self.vrc_out_port: int = self.typed_config.vrc_out_port

        # OSC 客户端
        self.osc_client: Any = None
        self._osc_enabled = PYTHON_OSC_AVAILABLE
        if not self._osc_enabled:
            self.logger.warning("python-osc 库不可用，VRChatProvider 将在禁用状态下运行")

        self._is_connected = False
        self._has_started = False
        self.render_count = 0
        self.error_count = 0
        # 事件订阅句柄（setup 时绑定，cleanup 时退订）
        self._speech_emotion_handler: Optional[Any] = None

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="set_expression",
                description=(
                    "VRChat 设置主播当前情绪（17 枚举值 + 强度）。"
                    "VRChat OSC 参数每 avatar 自定义、无标准表情通道，当前不渲染情绪面，"
                    "手势类表达走 vrchat_trigger_preset_action"
                ),
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VRCHAT_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="list_preset_actions",
                description="列出 VRChat 可演的预设动作目录（手势清单）",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
            ToolSpec(
                name="trigger_preset_action",
                description="触发一个预设动作（动作名取自 vrchat_list_preset_actions 返回的目录）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VRCHAT_TRIGGER_PRESET_ACTION_SCHEMA,
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "vrchat_set_expression":
                return await self.set_expression(str(args.get("emotion", "")), float(args.get("intensity", 0.5)))
            if n == "vrchat_list_preset_actions":
                return await self.list_preset_actions()
            if n == "vrchat_trigger_preset_action":
                return await self.trigger_preset_action(str(args.get("action", "")))
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001
            self.logger.exception(f"VRChat 工具 {invocation.tool_name} 调用异常: {exc}")
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 契约方法（LLM 工具与自动情绪路径共用的渲染入口）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪（能力差异的优雅降级面）。

        VRChat OSC 参数体系每 avatar 自定义、无标准表情通道，本平台不渲染
        情绪面：合法情绪名返回成功但 ``applied=False``（诚实告知未应用），
        映射表外的情绪名按失败结果返回。手势类表达走 trigger_preset_action。
        """
        if emotion not in {e.value for e in Emotion}:
            return _fail("vrchat_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        factor = min(1.0, max(0.0, float(intensity)))
        return _ok(
            "vrchat_set_expression",
            True,
            {
                "emotion": emotion,
                "intensity": factor,
                "applied": False,
                "reason": "VRChat OSC 无标准表情参数通道，情绪面不渲染",
            },
        )

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（VRChat 内置手势 enum）。"""
        actions = [{"name": name, "type": "gesture"} for name in self.GESTURE_MAP if name != "Neutral"]
        return _ok("vrchat_list_preset_actions", True, {"actions": actions})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设手势；未知名把目录随失败结果返回（失败即发现）。"""
        action = action.strip()
        if action in self.GESTURE_MAP and action != "Neutral":
            self._trigger_gesture(action)
            return _ok("vrchat_trigger_preset_action", True, {"action": action})
        catalog = [entry["name"] for entry in (await self.list_preset_actions()).structured_content["actions"]]
        return ToolExecutionResult(
            tool_name="vrchat_trigger_preset_action",
            success=False,
            error_message=f"未知预设动作 '{action}'",
            structured_content={"available_actions": catalog},
            content=str(catalog),
        )

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        await self._connect()
        # 被动半事件订阅：情绪反射（streamer.speech；set_expression 当前不渲染
        # 情绪面，订阅保持契约一致性，未来接入表情通道无需改装配）
        self._speech_emotion_handler = bind_speech_emotion(self.event_bus, self, self.logger)
        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        if self.event_bus is not None and self._speech_emotion_handler is not None:
            try:
                self.event_bus.off(CoreEvents.STREAMER_SPEECH, self._speech_emotion_handler)
            except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
                self.logger.debug(f"streamer.speech 退订失败（已忽略）: {exc}")
            self._speech_emotion_handler = None
        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法 =====

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
            self.logger.exception(f"创建 VRChat OSC 客户端失败: {e}")
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
    lipsync_analyzer: Optional[Any] = None,
) -> VRChatProvider:
    # lipsync_analyzer：统一装配签名；VRChat 无标准 viseme 通道，不接口型渲染
    return VRChatProvider(
        config=config,
        event_bus=event_bus,
    )


def register_vrchat_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
) -> VRChatProvider:
    provider = create_vrchat_provider(
        config=config,
        event_bus=event_bus,
    )
    registry.register_provider(provider)
    return provider
