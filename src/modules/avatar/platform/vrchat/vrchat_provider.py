"""
VRChatProvider - VRChat 虚拟形象适配器（OSC 协议）

形象的逐帧驱动由面捕合成器负责：本适配器把一个 VRChat 面捕驱动挂到合成器上——眼睛走
VRChat 官方的 OSC 眼动输入（任何形象都认），脸走 VRCFaceTracking 的通行参数名（做了面捕
参数的形象才动），风格化标记写形象作者另做的参数。头部转动 VRChat 不开放写入。

- 动作片段：动作表里的动作（默认对应 VRChat SDK 默认模板的 ``VRCEmote``）登记给面捕合成器，
  主播经 ``avatar_act`` 按名字触发；触发 = 把动作值写进动作参数，过一会儿写回 0。
- 头顶聊天框：登记为字幕服务的一个后端，跟随字幕源。
- 不向 LLM 暴露工具；``set_expression`` 等契约方法只给调试台用（设表情交给面捕合成器）。

VRChat 的内置参数（口型、手势等）全部只读；口型由 VRChat 按麦克风声音自己算。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from src.modules.avatar.platform.vrchat.chatbox import VRChatChatboxBackend
from src.modules.avatar.tool_common import dispatch_tool, unknown_action_fail
from src.modules.avatar.tracking.channels import MARKERS
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
    tool_result_fail,
    tool_result_ok,
)
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

from .tracking_driver import VrchatTrackingDriver

# python-osc 软降级
try:
    from pythonosc.udp_client import SimpleUDPClient

    PYTHON_OSC_AVAILABLE = True
except ImportError:
    PYTHON_OSC_AVAILABLE = False
    SimpleUDPClient = None  # type: ignore

_EMOTIONS = frozenset(e.value for e in Emotion)


class VRChatProvider(BaseToolProvider):
    """VRChat 虚拟形象适配器：面捕驱动 + 动作片段 + 头顶聊天框。"""

    PROVIDER_NAME = "vrchat"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "avatar"

    class ConfigSchema(BaseConfig):
        """VRChat OSC 配置（地址端口 + 面捕参数 + 动作表 + 风格化标记 + 聊天框）"""

        # 常量型字段：值恒等于段名，仅作段位标识，WebUI 只读呈现（改了也会被忽略）
        type: str = Field(title="段位标识", default="vrchat", json_schema_extra={"readonly": True})
        vrc_host: str = Field(title="OSC 主机地址", default="127.0.0.1", description="VRChat OSC 主机地址")
        vrc_out_port: int = Field(title="OSC 输出端口", default=9000, ge=1, le=65535, description="VRChat OSC 输出端口")
        face_tracking_enabled: bool = Field(
            title="写面捕参数",
            default=True,
            description=(
                "按 VRCFaceTracking 的通行参数名写脸（下巴、嘴角、眉、眯眼等）；只有做了面捕参数的形象会动，"
                "其余形象 VRChat 直接忽略。眼睛走官方眼动输入，任何形象都会动，不受这一项影响"
            ),
        )
        face_tracking_prefix: str = Field(
            title="面捕参数前缀",
            default="FT/",
            description="形象上面捕参数名的前缀（/avatar/parameters/<前缀>v2/JawOpen）；常见模板用 FT/，没有前缀填空串",
            json_schema_extra={"x-ui-advanced": True},
        )
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
        markers: Dict[str, str] = Field(
            title="风格化标记",
            default_factory=dict,
            description=(
                "标记名 → 形象上的参数名（形象作者另做的 0~1 浮点参数）。"
                "标记名：" + "、".join(f"{name}（{label}）" for name, label in MARKERS.items())
            ),
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

        @field_validator("markers")
        @classmethod
        def _known_markers(cls, value: Dict[str, str]) -> Dict[str, str]:
            unknown = sorted(set(value) - set(MARKERS))
            if unknown:
                raise ValueError(f"未知的风格化标记 {unknown}（合法名：{', '.join(MARKERS)}）")
            empty = sorted(name for name, target in value.items() if not str(target).strip())
            if empty:
                raise ValueError(f"风格化标记 {empty} 没写对应的形象参数名")
            return {name: str(target).strip() for name, target in value.items()}

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        tracking: Optional[Any] = None,
    ) -> None:
        """``tracking``：面捕合成器运行时（组合根共享一个）；None = 只剩动作与聊天框，不驱动形象。"""
        self.config = config
        self.event_bus = event_bus
        self._tracking = tracking
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
        self._driver = VrchatTrackingDriver(
            self._send_osc,
            lambda: self._is_connected and self.osc_client is not None,
            face_prefix=self.typed_config.face_tracking_prefix if self.typed_config.face_tracking_enabled else None,
            markers=self.typed_config.markers,
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

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> list[ToolSpec]:
        """不向 LLM 暴露工具：形象动作统一走 ``avatar_act``，动作表作为片段登记给面捕合成器。"""
        return []

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """本适配器没有 LLM 工具：任何调用都按"工具不属于本 Provider"失败返回。"""
        return await dispatch_tool(invocation, self.PROVIDER_NAME, {}, self.logger)

    # ===== 契约方法（调试台直接调用）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪：交给面捕合成器，浮现后保持配置的时长再回落。"""
        if emotion not in _EMOTIONS:
            return tool_result_fail("vrchat_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        if self._tracking is None:
            return tool_result_fail("vrchat_set_expression", "面捕合成器未启用（avatar.toml [tracking].enabled）")
        factor = min(1.0, max(0.0, float(intensity)))
        outcome = self._tracking.express(emotion, factor)
        return tool_result_ok(
            "vrchat_set_expression", True, {"emotion": emotion, "intensity": factor, "outcome": outcome}
        )

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（配置的动作表）。"""
        actions = [{"name": name, "type": "emote"} for name in self.emotes]
        return tool_result_ok("vrchat_list_preset_actions", True, {"actions": actions})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作；未知名把目录随失败结果返回（失败即发现）。"""
        action = action.strip()
        if action in self.emotes:
            fired = self._trigger_emote(action)
            return tool_result_ok("vrchat_trigger_preset_action", fired, {"action": action})
        return unknown_action_fail("vrchat_trigger_preset_action", action, list(self.emotes))

    def _clip_names(self) -> List[str]:
        """登记给面捕合成器的片段目录：动作表里的动作。"""
        return list(self.emotes)

    async def _trigger_clip(self, name: str) -> bool:
        """面捕合成器按片段名转回来的触发请求。"""
        return bool((await self.trigger_preset_action(name)).success)

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            return
        await self._connect()
        # 面捕驱动挂到面捕合成器上：发言、播放都由合成器订阅，这里不再订阅事件
        if self._tracking is not None:
            await self._tracking.attach(self._driver)
            self._tracking.register_clips(self.PROVIDER_NAME, self._clip_names, self._trigger_clip)
        else:
            self.logger.warning("面捕合成器未装配：VRChat 只剩动作与聊天框，形象不会被逐帧驱动")
        # 重新启动时按配置恢复聊天框（上次清理时已停用）
        self.chatbox.enabled = self.typed_config.chatbox_enabled
        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self) -> None:
        if not self._has_started:
            return
        # 先摘下面捕驱动（摘下前发一帧静止姿态），再停聊天框与动作归零，最后断 OSC
        if self._tracking is not None:
            self._tracking.unregister_clips(self.PROVIDER_NAME)
            await self._tracking.detach(self._driver)
        await self.chatbox.close()
        await self._cancel_emote_reset()
        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法 =====

    def _trigger_emote(self, emote_name: str) -> bool:
        """写动作参数 → 延迟归零；新动作会取消上一个尚未执行的归零。返回是否写出去了。"""
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接，无法触发动作")
            self.error_count += 1
            return False
        address = f"/avatar/parameters/{self.emote_parameter}"
        value = self.emotes[emote_name]
        try:
            self.osc_client.send_message(address, value)
            self.render_count += 1
            self.logger.debug(f"触发 VRChat 动作: {emote_name} ({address} = {value})")
        except Exception as e:
            self.error_count += 1
            self.logger.error(f"触发 VRChat 动作失败: {emote_name}: {e}", exc=True)
            return False
        if self.emote_reset_ms > 0:
            if self._emote_reset_task is not None and not self._emote_reset_task.done():
                self._emote_reset_task.cancel()
            self._emote_reset_task = asyncio.create_task(self._reset_emote(address), name="VRChat_EmoteReset")
        return True

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

    def _send_osc(self, address: str, value: Any) -> None:
        """面捕驱动发一条 OSC 消息（未连接时跳过；UDP 发完就走）。"""
        if not self._is_connected or not self.osc_client:
            return
        try:
            self.osc_client.send_message(address, value)
        except Exception as e:
            self.error_count += 1
            if self.error_count == 1 or self.error_count % 300 == 0:
                self.logger.warning(f"VRChat OSC 发送失败（累计 {self.error_count} 次）: {address}: {e}")

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
            "tracking": self._tracking is not None,
            "emotion": self._tracking.current_emotion() if self._tracking is not None else "",
        }

    def connection_summary(self) -> Dict[str, Any]:
        """连接态观测摘要：OSC 无断线回调，连接态以自身 ``_is_connected`` 标志为准。"""
        return {"is_connected": self._is_connected, "stats": self.get_stats()}

    # ===== 内部辅助 =====

    async def _connect(self) -> None:
        if not self._osc_enabled:
            self.logger.warning("python-osc 不可用，无法连接到 VRChat")
            return
        try:
            self.logger.info(f"正在连接到 VRChat OSC... (Host: {self.vrc_host}, Port: {self.vrc_out_port})")
            self.osc_client = SimpleUDPClient(self.vrc_host, self.vrc_out_port)  # type: ignore[misc]
            self._is_connected = True
            self._driver.reset()
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


def create_vrchat_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    tracking: Optional[Any] = None,
) -> VRChatProvider:
    # lipsync_analyzer：统一装配签名；VRChat 的口型由它按麦克风声音自己算，不接口型分析器
    return VRChatProvider(config=config, event_bus=event_bus, tracking=tracking)


def register_vrchat_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    subtitle_service: Optional[Any] = None,
    tracking: Optional[Any] = None,
) -> VRChatProvider:
    """构造 VRChatProvider 并注册到 registry；给了字幕服务且开了聊天框时，
    把聊天框登记为字幕后端（跟随字幕服务已选定的字幕源）。"""
    provider = create_vrchat_provider(config=config, event_bus=event_bus, tracking=tracking)
    registry.register_provider(provider)
    if subtitle_service is not None and provider.chatbox.enabled:
        subtitle_service.register_backend(provider.chatbox)
    return provider
