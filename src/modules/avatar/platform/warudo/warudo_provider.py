"""
WarudoProvider - Warudo 虚拟形象工具集

ToolProvider 协议实现，编排各引擎子件（``WarudoStateManager`` / 后台任务 /
``WarudoSubtitleManager`` / ``ActionSender``）：

- 暴露的工具（同语义跨平台同名同参数形状，契约见 ``avatar.protocol``）：
  - ``warudo_set_expression``        - 设置情绪（17 枚举值 + 强度）
  - ``warudo_list_preset_actions``   - 列出可演预设（动作目录 + 内置条目）
  - ``warudo_trigger_preset_action`` - 触发预设动作（未知名随结果返回目录）
  - ``warudo_set_sight``             - 设置视线（看镜头/看弹幕/看手机）

表情 / 眼 / 瞳孔 / 口型等状态件是情绪映射与氛围任务的内部通道，不对
LLM 暴露。情绪用哪些表情键由表情档案配置（``expression_profile``），
代码不认任何具体模型的形状名。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from pydantic import Field

from src.modules.avatar.speech_binding import AvatarBindings
from src.modules.avatar.tool_common import (
    contract_tool_specs,
    dispatch_tool,
    scaled_emotion_weights,
    unknown_action_fail,
)
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec, tool_result_fail, tool_result_ok
from src.modules.tools.provider import ConnectionToolProvider, wire_connection_tools
from src.modules.utils.backoff import ExponentialBackoff

from .expression_profile import BLINK_KEY, WarudoExpressionConfig
from .lip_sync_renderer import WarudoLipSyncRenderer
from .state.warudo_state_manager import WarudoStateManager
from .tasks.blink_task import BlinkTask
from .tasks.shift_task import ShiftTask
from .tasks.talking_head_task import TalkingHeadTask
from .tasks.throw_fish_task import ThrowFishTask
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
# 工具参数 Schema（契约工具三件套的 schema 走 avatar.tool_common 单一定义源；
# 此处只留 Warudo 特有工具）
# =============================================================================

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


# 内置预设动作（除配置 action_catalog 外固定可演的条目）
_THROW_FISH_ACTION = "throw_fish"

# 内置头部动作与站姿：动作名直发蓝图（数据 1），蓝图播放 Warudo 自带动画，
# 与具体模型无关；仓库的模板蓝图按这些名字接收
BUILTIN_MOTIONS: Dict[str, str] = {
    "nod_once": "点头一次",
    "nod_twice": "点头两次",
    "shake_head": "摇头",
    "tilt_head": "歪头",
    "look_away": "低头看向一边",
    "pose_calm": "双手背到身后的放松站姿",
    "pose_think": "思考站姿",
    "pose_default": "恢复默认站姿",
}


# =============================================================================
# WarudoProvider
# =============================================================================


class WarudoProvider(ConnectionToolProvider):
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

        type: str = Field(default="warudo", title="段位标识")
        ws_host: str = Field(title="Warudo 主机地址", default="localhost", description="Warudo WebSocket 主机地址")
        ws_port: int = Field(title="Warudo 端口", default=19190, ge=1, le=65535, description="Warudo WebSocket 端口")
        reconnect_delay_seconds: float = Field(
            title="重连间隔",
            default=5.0,
            ge=0.0,
            description="断线重连间隔秒数",
            json_schema_extra={"x-ui-advanced": True},
        )
        talking_head_enabled: bool = Field(
            title="是否启用 TalkingHead", default=True, description="是否启用 TalkingHead 后台任务"
        )
        talking_head_interval: float = Field(
            title="TalkingHead 间隔",
            default=0.1,
            ge=0.01,
            description="TalkingHead 最小间隔秒数",
            json_schema_extra={"x-ui-advanced": True},
        )
        throw_fish_cooldown: float = Field(title="抛鱼冷却时间", default=5.0, ge=0.0, description="抛鱼动画冷却秒数")
        # 动作目录（人类登记的可用动作名+说明；类似 MCP servers 的动态键例外，
        # 键=动作名、值=说明；typed 形态 Dict[str, str] 为有界键值映射，不算自由 dict）
        action_catalog: Dict[str, str] = Field(
            title="动作目录",
            default_factory=dict,
            description="可用蓝图动作目录 {动作名: 说明}，人类配置预声明；用于拼入工具描述供 LLM 选择",
        )
        # 表情档案：情绪 → 表情键映射（默认 VRM 标准表情名；自定义形状名的模型换档案）
        expression: WarudoExpressionConfig = Field(
            default_factory=WarudoExpressionConfig,
            title="表情档案",
            description="情绪用哪些表情键、各占多少；换模型时改这里，蓝图按同一份档案生成",
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
        # 重连等待退避：以配置的重连间隔为起点，连续失败时指数拉长（60s 封顶），
        # 连上即复位——Warudo 长关时不再按固定间隔无限打日志
        self._reconnect_backoff = ExponentialBackoff(
            base_delay_ms=max(1, int(self.reconnect_delay_seconds * 1000)), cap_delay_ms=60_000
        )
        self.talking_head_enabled: bool = self.typed_config.talking_head_enabled
        self.talking_head_interval: float = self.typed_config.talking_head_interval
        self.throw_fish_cooldown: float = self.typed_config.throw_fish_cooldown
        self.action_catalog: Dict[str, str] = dict(self.typed_config.action_catalog)
        self.expression: WarudoExpressionConfig = self.typed_config.expression

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

        # 状态件的表情键全集取自档案：换情绪时这些键整表重发，旧表情不残留
        self.state_manager = WarudoStateManager(
            self.logger,
            send_action_callback,
            expression_keys=self.expression.expression_keys(),
            mouth_keys=self.expression.mouth_keys,
        )
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

        self._is_connected = False
        self._has_started = False
        self.render_count = 0
        self.error_count = 0
        # 被动半挂件句柄（setup 时逐项 attach，cleanup 时一次全量退订摘除）
        self._bindings: Optional[AvatarBindings] = None

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def _preset_action_catalog(self) -> list:
        """预设动作目录：配置 ``action_catalog`` 条目 + 内置头部动作与站姿 + 抛鱼（带冷却）。

        配置条目与内置条目同名时以配置的说明为准（用户自己的蓝图可能另有含义）。
        """
        catalog = [{"name": name, "description": desc} for name, desc in self.action_catalog.items()]
        catalog.extend(
            {"name": name, "description": desc}
            for name, desc in BUILTIN_MOTIONS.items()
            if name not in self.action_catalog
        )
        catalog.append({"name": _THROW_FISH_ACTION, "description": "抛鱼动画（内置，带冷却）"})
        return catalog

    def _connected_tools(self) -> list[ToolSpec]:
        """连接建立后暴露的工具列表（预设目录走 list_preset_actions 结果，不进描述）。

        未连接时清单为空（基类 ``list_tools`` 降级返回空，Agent 工具表不含
        warudo_*）；此处只负责连接后的全量声明，清单变化经连接态回调驱动
        注册表刷新补注册。
        """
        specs = contract_tool_specs(
            self.PROVIDER_NAME,
            set_expression="Warudo 设置主播当前情绪（17 枚举值 + 强度，持续生效直至下次设置）",
            list_preset_actions="列出 Warudo 可演的预设动作目录（配置登记的蓝图动作 + 内置条目）",
            trigger_preset_action="触发一个预设动作（动作名取自 warudo_list_preset_actions 返回的目录）",
        )
        # set_sight 是 Warudo 平台特有工具（非三平台契约面），声明就地追加
        specs.append(
            ToolSpec(
                name="set_sight",
                description="Warudo 设置视线目标（看镜头/看弹幕/看手机）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_WARUDO_SET_SIGHT_SCHEMA,
            )
        )
        return specs

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """分发到对应的 handler 方法（handlers 每次现构造，捕获本次参数）。"""
        args = invocation.arguments or {}
        return await dispatch_tool(
            invocation,
            self.PROVIDER_NAME,
            {
                "warudo_set_expression": lambda: self.set_expression(
                    str(args.get("emotion", "")), float(args.get("intensity", 0.5))
                ),
                "warudo_list_preset_actions": self.list_preset_actions,
                "warudo_trigger_preset_action": lambda: self.trigger_preset_action(str(args.get("action", ""))),
                "warudo_set_sight": lambda: self.set_sight(str(args.get("target", ""))),
            },
            self.logger,
        )

    # ===== 契约方法（LLM 工具与自动情绪路径共用的渲染入口）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪：查表情档案 → 按强度缩放 → 写表情与眼部状态件。

        状态件的写入经监控循环推送到 Warudo（``changed`` 标志驱动）；
        换情绪时表情键整表替换，旧表情不残留。档案未列出的合法情绪按中性处理。
        """
        resolved = scaled_emotion_weights(emotion, intensity, self.expression.emotions)
        if resolved is None:
            self.error_count += 1
            return tool_result_fail("warudo_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        factor, applied = resolved
        # 收到情绪 → 表情键整表写入；眨眼键交给眼部状态合并，笑眼等暂停眨眼的键生效时眨眼任务跳过
        self.state_manager.expression_state.set_weights(applied)
        blink_blocked = any(applied.get(key, 0.0) > 0 for key in self.expression.blink_blocking_keys)
        self.state_manager.eye_state.set_expression(applied.get(BLINK_KEY, 0.0), blink_blocked)
        self.render_count += 1
        return tool_result_ok(
            "warudo_set_expression", True, {"emotion": emotion, "intensity": factor, "applied": applied}
        )

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（配置 ``action_catalog`` + 内置条目）。"""
        return tool_result_ok("warudo_list_preset_actions", True, {"actions": self._preset_action_catalog()})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作；未知名把目录随失败结果返回（失败即发现）。

        内置条目 ``throw_fish`` 走冷却任务（冷却中经结果载荷告知）；头部动作、
        站姿与配置登记的条目都按动作名直发蓝图。
        """
        action = action.strip()
        if action == _THROW_FISH_ACTION:
            fired = await self.throw_fish_task.throw_fish()
            return tool_result_ok(
                "warudo_trigger_preset_action",
                True,
                {"action": action, "fired": bool(fired), "cooldown_seconds": self.throw_fish_cooldown},
            )
        if action in self.action_catalog or action in BUILTIN_MOTIONS:
            await self._send_action_internal(action, 1)
            return tool_result_ok("warudo_trigger_preset_action", True, {"action": action})
        catalog = [entry["name"] for entry in self._preset_action_catalog()]
        return unknown_action_fail("warudo_trigger_preset_action", action, catalog)

    async def set_sight(self, target: str) -> ToolExecutionResult:
        """设置视线目标（camera/danmu/phone）；程度由适配器定（满幅）。"""
        if target not in ("camera", "danmu", "phone"):
            return tool_result_fail("warudo_set_sight", f"未知视线目标 '{target}'（应为 camera/danmu/phone）")
        self.state_manager.sight_state.set_state(target, 1.0)
        return tool_result_ok("warudo_set_sight", True, {"target": target})

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            self.logger.warning("WarudoProvider 已启动，跳过重复 setup")
            return

        await self._connect()

        # 被动半事件订阅：情绪反射（streamer.speech）+ 说话状态（tts.utterance.*，
        # 驱动 talking-head 点头任务）；口型渲染挂到共享分析器（元音成分 →
        # VowelA~O 通道；启用时应关闭 Warudo 原生口型）
        self._bindings = AvatarBindings(self.event_bus, self.logger)
        self._bindings.attach_emotion_reflection(self)
        self._bindings.attach_speaking_state(self._set_speaking)
        if self.lipsync_analyzer is not None:
            self._bindings.attach_lip_renderer(
                self.lipsync_analyzer, WarudoLipSyncRenderer(set_mouth_channel=self._set_mouth_channel)
            )

        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    def _set_mouth_channel(self, key: str, weight: float) -> None:
        """元音单键写入（渲染器回调）：空键 = 停嘴，清空全部元音。

        口型只写元音状态件，不碰情绪表情；张嘴期间档案里的嘴部表情键暂时
        让位（按 0 下发），停嘴后恢复，说完一句话嘴形表情不会丢。
        """
        vowel_state = self.state_manager.vowel_state
        vowel_state.set_vowel(key, weight)
        self.state_manager.expression_state.set_speaking(vowel_state.active)

    def _set_speaking(self, speaking: bool) -> None:
        """说话状态回调：说话时点头（talking-head 任务），停说即恢复。"""
        if self.talking_head_task is not None:
            self.talking_head_task.is_talking = speaking

    async def cleanup(self) -> None:
        if not self._has_started:
            return

        if self._bindings is not None:
            self._bindings.close()
            self._bindings = None

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
            was_connected = self._is_connected
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
            except (asyncio.TimeoutError, Exception) as e:
                self.logger.warning(f"WebSocket 关闭异常: {e}")
            finally:
                self.websocket = None
                self._is_connected = False
                if was_connected:
                    # 连接态换血：cleanup 前先摘除工具（走到这里必是 True→False 迁移）
                    self.mark_disconnected()

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
            # 连接态换血：装配期降级登记的 0 工具在此补注册（回调由注册接线方提供）
            self.mark_connected()
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
            was_connected = self._is_connected
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
            except (asyncio.TimeoutError, Exception) as e:
                self.logger.warning(f"手动关闭 Warudo WebSocket 异常（忽略）: {e}")
            finally:
                self.websocket = None
                self._is_connected = False
                self._action_sender.set_websocket(None)
                if was_connected:
                    # 连接态换血：手动断开摘除工具（走到这里必是 True→False 迁移）
                    self.mark_disconnected()
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
                    # 连接态换血：断连期摘除的工具在此补注册（回调由注册接线方提供）
                    self.mark_connected()
                    self.logger.info(f"已连接到 Warudo: {uri}")
                    # 连上即复位：下一次断开的等待从配置的间隔重新起算
                    self._reconnect_backoff.reset()

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
                    was_connected = self._is_connected
                    self._is_connected = False
                    self.websocket = None
                    self._action_sender.set_websocket(None)
                    if was_connected:
                        # 连接态换血：摘除断连工具（Agent 工具表不再包含 warudo_*）
                        self.mark_disconnected()
                    delay_s = self._reconnect_backoff.next_delay_ms() / 1000
                    self.logger.debug(f"WebSocket 断开，{delay_s:.0f}秒后重连...")
                    try:
                        await asyncio.sleep(delay_s)
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


# =============================================================================
# 工厂 / 注册辅助
# =============================================================================


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
    subtitle_service: Optional[Any] = None,
) -> WarudoProvider:
    """构造 WarudoProvider 并注册到 registry。返回 Provider 实例供调用方管理生命周期。

    注册接线：装配期未连接 → 降级登记 0 工具（中间层标准语义）；连接态
    回调经 ``wire_connection_tools`` 接入，驱动 ``registry.refresh_provider_tools``
    补注册 / 摘除，工具表始终与真实连接态一致。
    """
    provider = create_warudo_provider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
    )
    registry.register_provider(provider)
    wire_connection_tools(provider, registry)
    return provider
