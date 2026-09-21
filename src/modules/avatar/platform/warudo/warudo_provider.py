"""
WarudoProvider - Warudo 虚拟形象工具集

ToolProvider 协议实现，编排各引擎子件（``WarudoStateManager`` / 后台任务 /
``WarudoSubtitleManager`` / ``ActionSender``）：

- 暴露的工具（同语义跨平台同名同参数形状，契约见 ``avatar.protocol``）：
  - ``warudo_set_expression``        - 设置情绪（17 枚举值 + 强度）
  - ``warudo_list_preset_actions``   - 列出可演预设（动作目录 + 内置条目）
  - ``warudo_trigger_preset_action`` - 触发预设动作（未知名随结果返回目录）
  - ``warudo_set_sight``             - 设置视线（看镜头/看弹幕/看手机）

眉毛/眼睛/瞳孔/嘴部等 blendshape 状态件是情绪映射与氛围任务的内部通道
（方法保留供机件调用），不再对 LLM 暴露。被收敛的历史工具的方法保留，
仅撤 LLM 工具注册。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import Field

from src.modules.avatar.speech_binding import bind_speech_emotion, bind_speaking_state
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

from .lip_sync_renderer import WarudoLipSyncRenderer
from .state.warudo_state_manager import WarudoStateManager
from .tasks.blink_task import BlinkTask
from .tasks.shift_task import ShiftTask
from .tasks.talking_head_task import TalkingHeadTask
from .tasks.throw_fish_task import ThrowFishTask
from .tasks.typing_action_task import TypingActionTask
from .warudo_sender import ActionSender

if TYPE_CHECKING:
    pass


# 软降级:websockets 库可能未安装
try:
    import websockets  # type: ignore

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None  # type: ignore
    WEBSOCKETS_AVAILABLE = False


# =============================================================================
# 工具参数 Schema
# =============================================================================

_WARUDO_SET_EXPRESSION_SCHEMA: Dict[str, Any] = {
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
            "description": "情绪强度（0.0–1.0；1.0 为该情绪的完整幅度）",
        },
    },
    "required": ["emotion"],
}

_WARUDO_TRIGGER_PRESET_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "预设动作名（取自 warudo_list_preset_actions 返回的目录）",
        },
    },
    "required": ["action"],
}

_WARUDO_SET_SIGHT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "enum": ["camera", "danmu", "phone"],
            "description": "视线目标：camera=看镜头 / danmu=看弹幕 / phone=看手机",
        },
    },
    "required": ["target"],
}


# 情绪 → Warudo blendshape 状态映射（词表 17 值全覆盖）。
# 值 = {通道: (状态键, 满幅权重)}；状态键是 Warudo 项目的 blendshape 预设名
# （与 warudo_state_manager 的 ALL_*_STATE 常量对齐），强度在 set_expression
# 中按线性缩放施加。解析细节是适配器实现自由，架构不约束映射结果。
_WARUDO_EMOTION_STATES: Dict[str, Dict[str, tuple]] = {
    "neutral": {},
    "happy": {"mouth": ("mouth_happy_strong", 1.0), "eyebrow": ("eyebrow_happy_weak", 0.8)},
    "sad": {"mouth": ("mouth_sad_weak", 1.0), "eyebrow": ("eyebrow_sad_weak", 0.9)},
    "angry": {"mouth": ("mouth_angry_weak", 1.0), "eyebrow": ("eyebrow_angry_strong", 0.9)},
    "surprised": {"mouth": ("mouth_smlie_teeth", 0.6), "eyebrow": ("eyebrow_happy_strong", 1.0)},
    "scared": {"eyebrow": ("eyebrow_sad_strong", 1.0), "eye": ("eye_happy_strong", 0.5)},
    "disgusted": {"mouth": ("mouth_angry_weak", 0.6), "eyebrow": ("eyebrow_angry_weak", 0.7)},
    "shy": {"mouth": ("mouth_smlie_2", 0.8), "eyebrow": ("eyebrow_happy_weak", 0.4)},
    "embarrassed": {"mouth": ("mouth_smlie_3", 0.7), "eyebrow": ("eyebrow_sad_weak", 0.3)},
    "confused": {"eyebrow": ("eyebrow_sad_weak", 0.6), "mouth": ("mouth_smlie_2", 0.2)},
    "love": {"mouth": ("mouth_happy_strong", 0.9), "eye": ("eye_happy_strong", 1.0)},
    "excited": {"mouth": ("mouth_smlie_teeth", 1.0), "eyebrow": ("eyebrow_happy_strong", 0.9)},
    "smug": {"mouth": ("mouth_smlie_3", 0.9), "eyebrow": ("eyebrow_happy_weak", 0.5)},
    "serious": {"eyebrow": ("eyebrow_angry_weak", 0.5), "mouth": ("mouth_angry_weak", 0.3)},
    "tired": {"eye": ("eye_close", 0.6), "mouth": ("mouth_smlie_2", 0.1)},
    "crying": {"eye": ("eye_close", 0.8), "mouth": ("mouth_sad_weak", 1.0), "eyebrow": ("eyebrow_sad_strong", 0.8)},
    "speechless": {"eyebrow": ("eyebrow_angry_weak", 0.2)},
}

# 内置预设动作（除配置 action_catalog 外固定可演的条目）
_THROW_FISH_ACTION = "throw_fish"


# =============================================================================
# WarudoProvider
# =============================================================================


class WarudoProvider(BaseToolProvider):
    """Warudo 虚拟形象 ToolProvider

    实现 ToolProvider 协议，编排各引擎子件。
    """

    PROVIDER_NAME = "warudo"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "avatar"

    class ConfigSchema(BaseConfig):
        """Warudo 配置（WebSocket + 后台任务 + 动作目录）

        TOML 段位：[avatar.platform.warudo]（成员段直接铺键）
        （历史 subtitle_enabled/subtitle_port/subtitle_show_status 三键随
        8766 字幕面删除而移除，字幕收敛为 Dashboard /subtitle 一面。）
        """

        type: str = "warudo"
        ws_host: str = Field(default="localhost", description="Warudo WebSocket 主机地址")
        ws_port: int = Field(default=19190, ge=1, le=65535, description="Warudo WebSocket 端口")
        reconnect_delay_seconds: float = Field(default=5.0, ge=0.0, description="断线重连间隔秒数")
        talking_head_enabled: bool = Field(default=True, description="是否启用 TalkingHead 后台任务")
        talking_head_interval: float = Field(default=0.1, ge=0.01, description="TalkingHead 最小间隔秒数")
        throw_fish_cooldown: float = Field(default=5.0, ge=0.0, description="抛鱼动画冷却秒数")
        # 动作目录（人类登记的可用动作名+说明；类似 MCP servers 的动态键例外，
        # 键=动作名、值=说明；typed 形态 Dict[str, str] 为有界键值映射，不算自由 dict）
        action_catalog: Dict[str, str] = Field(
            default_factory=dict,
            description="可用蓝图动作目录 {动作名: 说明}，人类配置预声明；用于拼入工具描述供 LLM 选择",
        )

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        lipsync_analyzer: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        # 共享口型分析器（装配注入；setup 时挂 Warudo 渲染器，None = 不渲染口型）
        self.lipsync_analyzer = lipsync_analyzer
        self.logger = get_logger(self.__class__.__name__)

        # 配置（typed；空 dict = 全默认；失败 log+raise）
        try:
            self.typed_config = self.ConfigSchema.from_dict(config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise
        self.ws_host: str = self.typed_config.ws_host
        self.ws_port: int = self.typed_config.ws_port
        self.reconnect_delay_seconds: float = self.typed_config.reconnect_delay_seconds
        self.talking_head_enabled: bool = self.typed_config.talking_head_enabled
        self.talking_head_interval: float = self.typed_config.talking_head_interval
        self.throw_fish_cooldown: float = self.typed_config.throw_fish_cooldown
        self.action_catalog: Dict[str, str] = dict(self.typed_config.action_catalog)

        # WebSocket 状态
        self.websocket: Any = None
        self._connection_task: Optional[asyncio.Task] = None
        self._should_stop: bool = False
        self._first_connection: bool = True

        # 单实例 ActionSender
        self._action_sender = ActionSender()

        async def send_action_callback(action: str, data: Any) -> bool:
            await self._send_action_internal(action, data)
            return True

        self.state_manager = WarudoStateManager(self.logger, send_action_callback)
        self.blink_task = BlinkTask(self.state_manager, self.logger)
        self.shift_task = ShiftTask(self.state_manager, self.logger)

        self.talking_head_task: Optional[TalkingHeadTask] = None
        if self.talking_head_enabled:
            self.talking_head_task = TalkingHeadTask(
                send_action_callback=send_action_callback,
                logger=self.logger,
                min_interval=self.talking_head_interval,
            )

        self.throw_fish_task = ThrowFishTask(
            send_action_callback=send_action_callback,
            logger=self.logger,
            cooldown_seconds=self.throw_fish_cooldown,
        )

        self.typing_action_task = TypingActionTask(
            send_action_callback=send_action_callback,
            logger=self.logger,
        )

        self._is_connected = False
        self._has_started = False
        self.render_count = 0
        self.error_count = 0
        # 事件订阅句柄（setup 时绑定，cleanup 时退订）
        self._speech_emotion_handler: Optional[Any] = None
        self._speaking_state_handles: Optional[Any] = None
        # 口型渲染器（setup 时挂到共享分析器，cleanup 时摘除）
        self._lip_renderer: Optional[WarudoLipSyncRenderer] = None

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def _preset_action_catalog(self) -> list:
        """预设动作目录：配置 ``action_catalog`` 条目 + 内置条目（抛鱼，带冷却）。"""
        catalog = [{"name": name, "description": desc} for name, desc in self.action_catalog.items()]
        catalog.append({"name": _THROW_FISH_ACTION, "description": "抛鱼动画（内置，带冷却）"})
        return catalog

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="set_expression",
                description="Warudo 设置主播当前情绪（17 枚举值 + 强度，持续生效直至下次设置）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_WARUDO_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="list_preset_actions",
                description="列出 Warudo 可演的预设动作目录（配置登记的蓝图动作 + 内置条目）",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
            ToolSpec(
                name="trigger_preset_action",
                description="触发一个预设动作（动作名取自 warudo_list_preset_actions 返回的目录）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_WARUDO_TRIGGER_PRESET_ACTION_SCHEMA,
            ),
            ToolSpec(
                name="set_sight",
                description="Warudo 设置视线目标（看镜头/看弹幕/看手机）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_WARUDO_SET_SIGHT_SCHEMA,
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "warudo_set_expression":
                return await self.set_expression(str(args.get("emotion", "")), float(args.get("intensity", 0.5)))
            if n == "warudo_list_preset_actions":
                return await self.list_preset_actions()
            if n == "warudo_trigger_preset_action":
                return await self.trigger_preset_action(str(args.get("action", "")))
            if n == "warudo_set_sight":
                return await self.set_sight(str(args.get("target", "")))
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001 — Provider 边界兜底
            self.logger.exception(f"Warudo 工具 {invocation.tool_name} 调用异常: {exc}")
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 契约方法（LLM 工具与自动情绪路径共用的渲染入口）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪：查映射表 → 按强度缩放 → 写 blendshape 状态件。

        状态件的写入经监控循环推送到 Warudo（``changed`` 标志驱动）；
        换情绪时各通道 ``set_first_layer`` 自带清零，旧状态不残留。
        """
        states = _WARUDO_EMOTION_STATES.get(emotion)
        if states is None:
            return _fail("warudo_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        factor = min(1.0, max(0.0, float(intensity)))
        applied: Dict[str, Any] = {}
        state_manager = self.state_manager
        channels = {
            "eyebrow": state_manager.eyebrow_state,
            "eye": state_manager.eye_state,
            "mouth": state_manager.mouth_state,
        }
        for channel, (key, weight) in states.items():
            component = channels.get(channel)
            if component is None:
                continue
            component.set_first_layer(key, weight * factor)
            applied[channel] = {"key": key, "weight": round(weight * factor, 4)}
        return _ok("warudo_set_expression", True, {"emotion": emotion, "intensity": factor, "applied": applied})

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（配置 ``action_catalog`` + 内置条目）。"""
        return _ok("warudo_list_preset_actions", True, {"actions": self._preset_action_catalog()})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作；未知名把目录随失败结果返回（失败即发现）。

        内置条目 ``throw_fish`` 走冷却任务（冷却中经结果载荷告知）；其余
        条目按配置登记的动作名直发蓝图。
        """
        action = action.strip()
        if action == _THROW_FISH_ACTION:
            fired = await self.throw_fish_task.throw_fish()
            return _ok(
                "warudo_trigger_preset_action",
                True,
                {"action": action, "fired": bool(fired), "cooldown_seconds": self.throw_fish_cooldown},
            )
        if action in self.action_catalog:
            await self._send_action_internal(action, 1)
            return _ok("warudo_trigger_preset_action", True, {"action": action})
        catalog = [entry["name"] for entry in self._preset_action_catalog()]
        return ToolExecutionResult(
            tool_name="warudo_trigger_preset_action",
            success=False,
            error_message=f"未知预设动作 '{action}'",
            structured_content={"available_actions": catalog},
            content=str(catalog),
        )

    async def set_sight(self, target: str) -> ToolExecutionResult:
        """设置视线目标（camera/danmu/phone）；程度由适配器定（满幅）。"""
        if target not in ("camera", "danmu", "phone"):
            return _fail("warudo_set_sight", f"未知视线目标 '{target}'（应为 camera/danmu/phone）")
        self.state_manager.sight_state.set_state(target, 1.0)
        return _ok("warudo_set_sight", True, {"target": target})

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            self.logger.warning("WarudoProvider 已启动，跳过重复 setup")
            return

        await self._connect()

        # 被动半事件订阅：情绪反射（streamer.speech）+ 说话状态（tts.utterance.*，
        # 驱动 talking-head 点头任务）
        self._speech_emotion_handler = bind_speech_emotion(self.event_bus, self, self.logger)
        self._speaking_state_handles = bind_speaking_state(self.event_bus, self.logger, on_change=self._set_speaking)

        # 口型渲染：挂到共享分析器（元音成分 → VowelA~O 通道；启用时应关闭 Warudo 原生口型）
        if self.lipsync_analyzer is not None:
            self._lip_renderer = WarudoLipSyncRenderer(set_mouth_channel=self._set_mouth_channel)
            self.lipsync_analyzer.add_renderer(self._lip_renderer)

        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    def _set_mouth_channel(self, key: str, weight: float) -> None:
        """嘴部单键写入（渲染器回调）：空键清空全部元音通道，否则 set_first_layer。"""
        mouth_state = self.state_manager.mouth_state
        if not key:
            for existing in list(mouth_state.first_layer):
                mouth_state.first_layer[existing] = 0.0
            mouth_state.changed = True
            return
        mouth_state.set_first_layer(key, weight)

    def _set_speaking(self, speaking: bool) -> None:
        """说话状态回调：说话时点头（talking-head 任务），停说即恢复。"""
        if self.talking_head_task is not None:
            self.talking_head_task.is_talking = speaking

    async def cleanup(self) -> None:
        if not self._has_started:
            return

        if self.event_bus is not None:
            if self._speech_emotion_handler is not None:
                try:
                    self.event_bus.off(CoreEvents.STREAMER_SPEECH, self._speech_emotion_handler)
                except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
                    self.logger.debug(f"streamer.speech 退订失败（已忽略）: {exc}")
                self._speech_emotion_handler = None
            if self._speaking_state_handles is not None:
                started_handler, finished_handler = self._speaking_state_handles
                try:
                    self.event_bus.off(CoreEvents.TTS_UTTERANCE_STARTED, started_handler)
                    self.event_bus.off(CoreEvents.TTS_UTTERANCE_FINISHED, finished_handler)
                except Exception as exc:  # noqa: BLE001 - 退订失败不阻断清理
                    self.logger.debug(f"tts.utterance.* 退订失败（已忽略）: {exc}")
                self._speaking_state_handles = None

        if self.lipsync_analyzer is not None and self._lip_renderer is not None:
            self.lipsync_analyzer.remove_renderer(self._lip_renderer)
            self._lip_renderer = None

        # 停止后台任务
        try:
            await self.blink_task.stop()
        except Exception as e:
            self.logger.error(f"停止眨眼任务失败: {e}")

        try:
            await self.shift_task.stop()
        except Exception as e:
            self.logger.error(f"停止眼球移动任务失败: {e}")

        if self.talking_head_task is not None:
            try:
                await self.talking_head_task.stop()
            except Exception as e:
                self.logger.error(f"停止 talking_head 任务失败: {e}")

        try:
            await self.typing_action_task.stop()
        except Exception as e:
            self.logger.error(f"停止 typing_action 任务失败: {e}")

        try:
            self.state_manager.stop_monitoring()
        except Exception as e:
            self.logger.error(f"停止状态监控失败: {e}")

        # 取消 WebSocket 重连循环
        self._should_stop = True
        if self._connection_task and not self._connection_task.done():
            self._connection_task.cancel()
            try:
                await asyncio.wait_for(self._connection_task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                self.logger.debug("WebSocket 重连任务已取消")
            finally:
                self._connection_task = None

        # 关闭当前 WebSocket
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
            except (asyncio.TimeoutError, Exception) as e:
                self.logger.warning(f"WebSocket 关闭异常: {e}")
            finally:
                self.websocket = None
                self._is_connected = False

        self._action_sender.set_websocket(None)
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法 =====

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "talking_head_running": self.talking_head_task.running if self.talking_head_task else False,
        }

    # ===== 内部辅助 =====

    @property
    def _ws_closed(self) -> bool:
        if self.websocket is None:
            return True
        code = getattr(self.websocket, "close_code", None)
        if code is not None:
            return True
        return getattr(self.websocket, "closed", False)

    async def _send_action_internal(self, action: str, data: Any) -> None:
        if not self._is_connected or self.websocket is None or self._ws_closed:
            self.logger.warning(f"Warudo 未连接，无法发送动作: {action}")
            return
        try:
            self._action_sender.set_websocket(self.websocket)
            await self._action_sender.send_action(action, data)
        except Exception as e:
            self.logger.error(f"发送动作失败: {action}: {e}")

    def _is_ready_to_send(self) -> bool:
        if not self._is_connected or self.websocket is None:
            return False
        return not self._ws_closed

    async def _connect(self) -> None:
        if not WEBSOCKETS_AVAILABLE:
            self.logger.error("websockets 库未安装，无法连接 Warudo")
            return

        self._should_stop = False
        uri = f"ws://{self.ws_host}:{self.ws_port}"

        try:
            self.websocket = await websockets.connect(uri)
            self._is_connected = True
            self.logger.info(f"已连接到 Warudo: {uri}")
        except Exception as e:
            self.logger.warning(f"首次连接 Warudo 失败({e})，将由后台重连循环处理")
            self._is_connected = False

        if not self._connection_task or self._connection_task.done():
            self._connection_task = asyncio.create_task(self._connection_loop(uri), name="Warudo_Reconnect")
            self.logger.info("Warudo WebSocket 后台重连任务已启动")

    async def connect(self) -> bool:
        """手动建立 Warudo WebSocket 连接（手动重连的"建立"半步）。

        直接委托 ``_connect``：内部会尝试一次 ``websockets.connect`` 并启
        动后台 ``_connection_loop``（若尚未运行）。返回 ``_is_connected`` 真
        值——即使首次 connect 失败，后台循环仍会持续重试；上层判定重连
        成败看本次是否建立成功。手动 connect 与后台循环属低频可接受并发。
        """
        self.logger.info("手动触发 Warudo 连接")
        await self._connect()
        return self._is_connected

    async def disconnect(self) -> bool:
        """手动断开 Warudo WebSocket 连接（手动重连的"断开"半步）。

        与 ``cleanup`` 不同：仅关当前 websocket 与 ``_action_sender`` 绑定的
        websocket 引用、置 ``_is_connected=False``，**不动** ``_should_stop``
        标志与后台 ``_connection_loop`` 任务——后台循环负责后续自动重连，
        手动断开不破坏 setup 语义（``_has_started`` 保持 True）。websocket
        关闭做 try/except 兜底（超时或已关闭均不阻断）。返回 True 表达
        "断开动作已发出"。
        """
        self.logger.info("手动断开 Warudo 连接")
        if self.websocket is not None:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
            except (asyncio.TimeoutError, Exception) as e:
                self.logger.warning(f"手动关闭 Warudo WebSocket 异常（忽略）: {e}")
            finally:
                self.websocket = None
                self._is_connected = False
                self._action_sender.set_websocket(None)
        return True

    async def _connection_loop(self, uri: str) -> None:
        self.logger.info("Warudo WebSocket 重连循环已启动")
        while not self._should_stop:
            try:
                if not self._is_connected or self.websocket is None or self._ws_closed:
                    self.logger.info(f"尝试连接 Warudo: {uri}")
                    self.websocket = await websockets.connect(uri)
                    self._is_connected = True
                    self._action_sender.set_websocket(self.websocket)
                    self.logger.info(f"已连接到 Warudo: {uri}")

                    if self._first_connection:
                        self._first_connection = False
                        await self._on_first_connection_setup()

                if self.websocket and not self._ws_closed:
                    await self.websocket.wait_closed()

            except asyncio.CancelledError:
                self.logger.debug("WebSocket 重连循环被取消")
                break
            except Exception as e:
                self.logger.error(f"WebSocket 连接异常: {e}")
            finally:
                if not self._should_stop:
                    self._is_connected = False
                    self.websocket = None
                    self._action_sender.set_websocket(None)
                    self.logger.debug(f"WebSocket 断开，{self.reconnect_delay_seconds}秒后重连...")
                    try:
                        await asyncio.sleep(self.reconnect_delay_seconds)
                    except asyncio.CancelledError:
                        break

        self.logger.info("Warudo WebSocket 重连循环已退出")

    async def _on_first_connection_setup(self) -> None:
        try:
            self.state_manager.start_monitoring()
            self.logger.info("状态管理器监控已启动")
        except Exception as e:
            self.logger.error(f"启动状态监控失败: {e}")

        try:
            await self.blink_task.start()
            self.logger.info("眨眼任务已启动")
        except Exception as e:
            self.logger.error(f"启动眨眼任务失败: {e}")

        try:
            await self.shift_task.start()
            self.logger.info("眼球移动任务已启动")
        except Exception as e:
            self.logger.error(f"启动眼球移动任务失败: {e}")

        if self.talking_head_task is not None:
            try:
                await self.talking_head_task.start()
                self.logger.info("TalkingHead 任务已启动")
            except Exception as e:
                self.logger.error(f"启动 TalkingHead 任务失败: {e}")

        try:
            await self.typing_action_task.start()
            self.logger.info("TypingAction 任务已启动")
        except Exception as e:
            self.logger.error(f"启动 TypingAction 任务失败: {e}")

        if self.subtitle_manager is not None:
            try:
                await self.subtitle_manager.start_server()
                self.logger.info(f"字幕服务器已启动: http://localhost:{self.subtitle_port}")
            except Exception as e:
                self.logger.error(f"启动字幕服务器失败: {e}")


# =============================================================================
# 工厂 / 注册辅助
# =============================================================================


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


def create_warudo_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
) -> WarudoProvider:
    return WarudoProvider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
    )


def register_warudo_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
) -> WarudoProvider:
    provider = create_warudo_provider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
    )
    registry.register_provider(provider)
    return provider
