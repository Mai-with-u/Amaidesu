"""
VRChatProvider - VRChat OSC 虚拟形象工具集

- 引擎：OSC 客户端初始化、动作参数写入、头顶聊天框发送
- ToolProvider 协议由本类自身实现
- 暴露的工具（同语义跨平台同名同参数形状，契约见 ``avatar.protocol``）：
  - ``vrchat_set_expression``        - 设置情绪（VRChat 无标准表情参数体系，返回未应用结果）
  - ``vrchat_list_preset_actions``   - 列出可演预设（动作表）
  - ``vrchat_trigger_preset_action`` - 触发预设动作（未知名随结果返回目录）

预设动作写形象参数 ``/avatar/parameters/<动作参数>``。VRChat 的内置参数（口型、
手势等）全部只读，能写的只有形象作者自定义的参数；默认动作表对应 VRChat SDK
默认模板里的 ``VRCEmote``，自定义形象可在配置里换参数名和动作表。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from pydantic import Field

from src.modules.avatar.platform.vrchat.chatbox import VRChatChatboxBackend
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
            "description": "预设动作名（取自 vrchat_list_preset_actions 返回的动作目录）",
        },
    },
    "required": ["action"],
}


class VRChatProvider(BaseToolProvider):
    """VRChat 虚拟形象 ToolProvider（OSC 协议）"""

    PROVIDER_NAME = "vrchat"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "avatar"

    class ConfigSchema(BaseConfig):
        """VRChat OSC 配置（地址端口 + 动作表 + 聊天框）"""

        # 常量型字段：值恒等于段名，仅作段位标识，WebUI 只读呈现（改了也会被忽略）
        type: str = Field(title="段位标识", default="vrchat", json_schema_extra={"readonly": True})
        vrc_host: str = Field(title="OSC 主机地址", default="127.0.0.1", description="VRChat OSC 主机地址")
        vrc_out_port: int = Field(title="OSC 输出端口", default=9000, ge=1, le=65535, description="VRChat OSC 输出端口")
        emote_parameter: str = Field(
            title="动作参数名",
            default="VRCEmote",
            description="预设动作写入的形象参数名（SDK 默认模板为 VRCEmote；自定义形象按其参数填写）",
        )
        # 默认值取 VRChat SDK 默认动作菜单：1~6 已核实，7、8 按菜单顺序推定
        emotes: Dict[str, int] = Field(
            title="动作表",
            default_factory=lambda: {
                "Wave": 1,
                "Clap": 2,
                "Point": 3,
                "Cheer": 4,
                "Dance": 5,
                "Backflip": 6,
                "Sadness": 7,
                "Die": 8,
            },
            description="{动作名: 参数值}；触发时把该值写入动作参数",
        )
        emote_reset_ms: int = Field(
            title="动作归零延迟",
            default=3000,
            ge=0,
            description="触发后多久把动作参数写回 0（鼓掌、跳舞这类循环动作靠归零停下）；0 = 不归零",
        )
        chatbox_enabled: bool = Field(
            title="头顶聊天框",
            default=True,
            description="把主播说的话同步显示到 VRChat 头顶聊天框（跟随字幕源，超过 144 字截断）",
        )
        chatbox_min_interval_ms: int = Field(
            title="聊天框最短间隔",
            default=1600,
            ge=0,
            description="两条聊天框之间的最短间隔；发得太快 VRChat 会冷却，间隔内只发最新一条",
            json_schema_extra={"x-ui-advanced": True},
        )

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
        self.emote_parameter: str = self.typed_config.emote_parameter
        self.emotes: Dict[str, int] = dict(self.typed_config.emotes)
        self.emote_reset_ms: int = self.typed_config.emote_reset_ms
        # 动作归零任务：新动作触发时取消上一个，避免旧的归零打断新动作
        self._emote_reset_task: Optional[asyncio.Task] = None
        # 头顶聊天框字幕后端（装配时注册到字幕服务；未连接时发送自行跳过）
        self.chatbox = VRChatChatboxBackend(
            self._send_chatbox,
            min_interval_ms=self.typed_config.chatbox_min_interval_ms,
            enabled=self.typed_config.chatbox_enabled,
        )

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
                    "动作类表达走 vrchat_trigger_preset_action"
                ),
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VRCHAT_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="list_preset_actions",
                description="列出 VRChat 可演的预设动作目录（挥手、鼓掌、跳舞等）",
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
        映射表外的情绪名按失败结果返回。动作类表达走 trigger_preset_action。
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
        """列出可演预设目录（配置的动作表）。"""
        actions = [{"name": name, "type": "emote"} for name in self.emotes]
        return _ok("vrchat_list_preset_actions", True, {"actions": actions})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作；未知名把目录随失败结果返回（失败即发现）。"""
        action = action.strip()
        if action in self.emotes:
            self._trigger_emote(action)
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
        # 重新启动时按配置恢复聊天框（上次清理时已停用）
        self.chatbox.enabled = self.typed_config.chatbox_enabled
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
        # 先停聊天框与动作归零，再断 OSC：断开后它们的发送都会落空
        await self.chatbox.close()
        await self._cancel_emote_reset()
        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法 =====

    def _trigger_emote(self, emote_name: str) -> None:
        """写动作参数 → 延迟归零；新动作会取消上一个尚未执行的归零"""
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接，无法触发动作")
            return
        address = f"/avatar/parameters/{self.emote_parameter}"
        value = self.emotes[emote_name]
        try:
            self.osc_client.send_message(address, value)
            self.logger.debug(f"触发 VRChat 动作: {emote_name} ({address} = {value})")
        except Exception as e:
            self.logger.error(f"触发 VRChat 动作失败: {emote_name}: {e}", exc=True)
            return
        if self.emote_reset_ms > 0:
            if self._emote_reset_task is not None and not self._emote_reset_task.done():
                self._emote_reset_task.cancel()
            self._emote_reset_task = asyncio.create_task(self._reset_emote(address), name="VRChat_EmoteReset")

    async def _reset_emote(self, address: str) -> None:
        # 鼓掌、跳舞这类循环动作只在参数回到 0 时停下；单次动作归零也无副作用
        await asyncio.sleep(self.emote_reset_ms / 1000)
        if not self._is_connected or not self.osc_client:
            return
        try:
            self.osc_client.send_message(address, 0)
        except Exception as e:
            self.logger.error(f"VRChat 动作归零失败: {address}: {e}", exc=True)

    async def _cancel_emote_reset(self) -> None:
        task, self._emote_reset_task = self._emote_reset_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                self.logger.debug("动作归零任务已取消")

    def _send_chatbox(self, text: str) -> None:
        """发一条头顶聊天框：立即显示、不弹键盘、不播提示音；空串 = 清空"""
        if not self._is_connected or not self.osc_client:
            return
        self.osc_client.send_message("/chatbox/input", [text, True, False])

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
    subtitle_service: Optional[Any] = None,
) -> VRChatProvider:
    """构造 VRChatProvider 并注册到 registry；给了字幕服务且开了聊天框时，
    把聊天框登记为字幕后端（跟随字幕服务已选定的字幕源）。"""
    provider = create_vrchat_provider(
        config=config,
        event_bus=event_bus,
    )
    registry.register_provider(provider)
    if subtitle_service is not None and provider.chatbox.enabled:
        subtitle_service.register_backend(provider.chatbox)
    return provider
