"""
WarudoProvider - Warudo 虚拟形象适配器

形象的逐帧驱动由面捕合成器负责：本适配器把一个 Warudo 面捕驱动挂到合成器上，驱动按
iFacialMocap 协议把每一帧发给 Warudo 的面捕接收器（UDP，默认 127.0.0.1:49983），脸、眼、
头由 Warudo 自己的面捕流程落到模型上。另有一条 WebSocket 蓝图通道（默认 19190）：

- 动作片段：内置的头部动作与站姿（播放 Warudo 自带动画）、抛鱼（带冷却）、配置登记的蓝图
  动作，登记给面捕合成器，主播经 ``avatar_act`` 按名字触发；
- 风格化标记：脸红、眼泪这类面捕数据里没有的东西，按 ``markers`` 登记经蓝图写到模型形状上。

不向 LLM 暴露工具；``set_expression`` 等契约方法只给调试台用（设表情交给面捕合成器）。
蓝图由 ``scripts/warudo_blueprints.py`` 按同一份配置生成。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import Field, field_validator

from src.modules.avatar.tool_common import dispatch_tool, unknown_action_fail
from src.modules.avatar.tracking.channels import MARKERS
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec, tool_result_fail, tool_result_ok
from src.modules.tools.provider import ConnectionToolProvider, wire_connection_tools
from src.modules.types.emotion_vocab import Emotion
from src.modules.utils.backoff import ExponentialBackoff
from src.modules.utils.time_utils import now_ms

from .ifacialmocap import DEFAULT_PORT
from .tracking_driver import WarudoTrackingDriver
from .warudo_sender import ActionSender

# 软降级:websockets 库可能未安装
try:
    import websockets  # type: ignore

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None  # type: ignore
    WEBSOCKETS_AVAILABLE = False


# 内置预设动作（除配置 action_catalog 外固定可演的条目）
_THROW_FISH_ACTION = "throw_fish"

# 内置头部动作与站姿：动作名直发蓝图（数据 1），蓝图播放 Warudo 自带动画，
# 与具体模型无关；生成的蓝图按这些名字接收
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

_EMOTIONS = frozenset(e.value for e in Emotion)

# 打开面捕数据发送端：(主机, 端口) → 发送端（需有 sendto / is_closing / close）
DatagramFactory = Callable[[str, int], Awaitable[Any]]


class _QuietProtocol(asyncio.DatagramProtocol):
    """面捕数据只发不收：Warudo 没开时本机会回"端口不可达"，第一次记一条调试日志，之后不再记。"""

    def __init__(self, logger: Any) -> None:
        self._logger = logger
        self._reported = False

    def error_received(self, exc: Exception) -> None:
        if not self._reported:
            self._reported = True
            self._logger.debug(f"面捕数据暂时没人接（Warudo 未开或接收器未启用）: {exc}")


class WarudoProvider(ConnectionToolProvider):
    """Warudo 虚拟形象适配器：面捕驱动 + 蓝图通道上的动作片段与风格化标记。"""

    PROVIDER_NAME = "warudo"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "avatar"

    class ConfigSchema(BaseConfig):
        """Warudo 配置：面捕数据的接收地址 + 蓝图通道 + 动作目录 + 风格化标记

        TOML 段位：[avatar.platform.warudo]（成员段直接铺键）
        """

        type: str = Field(default="warudo", title="段位标识")
        tracking_host: str = Field(
            title="面捕接收器地址",
            default="127.0.0.1",
            description="Warudo 的 iFacialMocap 接收器所在主机（Warudo 和 Amaidesu 在同一台电脑时不用改）",
        )
        tracking_port: int = Field(
            title="面捕接收器端口",
            default=DEFAULT_PORT,
            ge=1,
            le=65535,
            description="Warudo 的 iFacialMocap 接收器端口（接收器配置页里的输入端口）",
        )
        ws_host: str = Field(title="Warudo 主机地址", default="localhost", description="Warudo WebSocket 主机地址")
        ws_port: int = Field(title="Warudo 端口", default=19190, ge=1, le=65535, description="Warudo WebSocket 端口")
        reconnect_delay_seconds: float = Field(
            title="重连间隔",
            default=5.0,
            ge=0.0,
            description="断线重连间隔秒数",
            json_schema_extra={"x-ui-advanced": True},
        )
        throw_fish_cooldown: float = Field(title="抛鱼冷却时间", default=5.0, ge=0.0, description="抛鱼动画冷却秒数")
        # 动作目录（人类登记的可用动作名+说明；类似 MCP servers 的动态键例外，
        # 键=动作名、值=说明；typed 形态 Dict[str, str] 为有界键值映射，不算自由 dict）
        action_catalog: Dict[str, str] = Field(
            title="动作目录",
            default_factory=dict,
            description="场景里自己做的蓝图动作 {动作名: 说明}；登记后主播可以经 avatar_act 触发",
        )
        markers: Dict[str, str] = Field(
            title="风格化标记",
            default_factory=dict,
            description=(
                "标记名 → 模型上的形状名（或 VRM 自定义表情名，取决于生成蓝图时选的写法）；"
                "蓝图按同一份生成。标记名：" + "、".join(f"{name}（{label}）" for name, label in MARKERS.items())
            ),
        )

        @field_validator("markers")
        @classmethod
        def _known_markers(cls, value: Dict[str, str]) -> Dict[str, str]:
            unknown = sorted(set(value) - set(MARKERS))
            if unknown:
                raise ValueError(f"未知的风格化标记 {unknown}（合法名：{', '.join(MARKERS)}）")
            empty = sorted(name for name, target in value.items() if not str(target).strip())
            if empty:
                raise ValueError(f"风格化标记 {empty} 没写对应的形状名")
            return {name: str(target).strip() for name, target in value.items()}

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        lipsync_analyzer: Optional[Any] = None,
        tracking: Optional[Any] = None,
        datagram_factory: Optional[DatagramFactory] = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        """Args:
        tracking: 面捕合成器运行时（组合根共享一个）；None = 只剩蓝图通道上的动作，不驱动形象。
        datagram_factory: 打开面捕数据发送端（测试注入；缺省开一个 UDP 端点）。
        clock: 毫秒时刻来源（抛鱼冷却、标记发送间隔；测试注入假时钟）。

        event_bus / lipsync_analyzer 是统一装配签名：发言、播放、口型都由面捕合成器订阅。
        """
        self.config = config
        self.event_bus = event_bus
        self.lipsync_analyzer = lipsync_analyzer
        self._tracking = tracking
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
        self.throw_fish_cooldown: float = self.typed_config.throw_fish_cooldown
        self.action_catalog: Dict[str, str] = dict(self.typed_config.action_catalog)
        self._datagram_factory = datagram_factory or self._open_datagram

        # WebSocket 状态
        self.websocket: Any = None
        self._connection_task: Optional[asyncio.Task] = None
        self._should_stop: bool = False

        # 单实例 ActionSender
        self._action_sender = ActionSender()
        self._clock = clock
        self._last_throw_ms: Optional[int] = None

        self._driver = WarudoTrackingDriver(
            markers=self.typed_config.markers, send_marker=self._send_marker, clock=clock
        )
        self._datagram: Any = None

        self._is_connected = False
        self._has_started = False
        self.render_count = 0
        self.error_count = 0

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def _preset_action_catalog(self) -> List[Dict[str, str]]:
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
        """连上以后也不向 LLM 暴露工具：形象动作统一走 ``avatar_act``，蓝图动作作为片段登记给面捕合成器。"""
        return []

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """本适配器没有 LLM 工具：任何调用都按"工具不属于本 Provider"失败返回。"""
        return await dispatch_tool(invocation, self.PROVIDER_NAME, {}, self.logger)

    # ===== 契约方法（调试台直接调用）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪：交给面捕合成器，浮现后保持配置的时长再回落。"""
        if emotion not in _EMOTIONS:
            self.error_count += 1
            return tool_result_fail("warudo_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        if self._tracking is None:
            return tool_result_fail("warudo_set_expression", "面捕合成器未启用（avatar.toml [tracking].enabled）")
        factor = min(1.0, max(0.0, float(intensity)))
        outcome = self._tracking.express(emotion, factor)
        self.render_count += 1
        return tool_result_ok(
            "warudo_set_expression", True, {"emotion": emotion, "intensity": factor, "outcome": outcome}
        )

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（配置 ``action_catalog`` + 内置条目）。"""
        return tool_result_ok("warudo_list_preset_actions", True, {"actions": self._preset_action_catalog()})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作；未知名把目录随失败结果返回（失败即发现）。

        内置条目 ``throw_fish`` 带冷却（冷却中经结果载荷告知）；头部动作、站姿与配置登记的
        条目都按动作名直发蓝图。
        """
        action = action.strip()
        if action == _THROW_FISH_ACTION:
            fired = await self._throw_fish()
            return tool_result_ok(
                "warudo_trigger_preset_action",
                True,
                {"action": action, "fired": fired, "cooldown_seconds": self.throw_fish_cooldown},
            )
        if action in self.action_catalog or action in BUILTIN_MOTIONS:
            sent = await self._send_action_internal(action, 1)
            return tool_result_ok("warudo_trigger_preset_action", sent, {"action": action})
        catalog = [entry["name"] for entry in self._preset_action_catalog()]
        return unknown_action_fail("warudo_trigger_preset_action", action, catalog)

    async def _throw_fish(self) -> bool:
        """抛鱼：距上次触发不到冷却时间就不发；发出去了才开始计冷却。"""
        now = self._clock()
        if self._last_throw_ms is not None and now - self._last_throw_ms < self.throw_fish_cooldown * 1000:
            return False
        if await self._send_action_internal(_THROW_FISH_ACTION, 1):
            self._last_throw_ms = now
            return True
        return False

    def _clip_names(self) -> List[str]:
        """登记给面捕合成器的片段目录：内置动作、抛鱼与配置登记的蓝图动作。"""
        return [entry["name"] for entry in self._preset_action_catalog()]

    async def _trigger_clip(self, name: str) -> bool:
        """面捕合成器按片段名转回来的触发请求。"""
        result = await self.trigger_preset_action(name)
        content = result.structured_content or {}
        return bool(result.success) and content.get("fired", True) is not False

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            self.logger.warning("WarudoProvider 已启动，跳过重复 setup")
            return

        await self._connect()

        # 面捕驱动挂到面捕合成器上：发言、播放、口型都由合成器订阅，这里不再订阅事件
        if self._tracking is not None:
            try:
                self._datagram = await self._datagram_factory(
                    self.typed_config.tracking_host, self.typed_config.tracking_port
                )
                self._driver.attach_transport(self._datagram)
                await self._tracking.attach(self._driver)
                self.logger.info(
                    f"面捕数据发往 {self.typed_config.tracking_host}:{self.typed_config.tracking_port}"
                    "（Warudo 里启用 iFacialMocap 接收器）"
                )
            except Exception as e:
                self.logger.error(f"打开面捕数据发送端失败，形象不会被逐帧驱动: {e}")
            # 蓝图动作作为片段登记：主播经 avatar_act 按名字触发
            self._tracking.register_clips(self.PROVIDER_NAME, self._clip_names, self._trigger_clip)
        else:
            self.logger.warning("面捕合成器未装配：Warudo 只剩蓝图通道上的动作，形象不会被逐帧驱动")

        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self) -> None:
        if not self._has_started:
            return

        # 先摘下面捕驱动（摘下前发一帧静止姿态），再关发送端与蓝图通道
        if self._tracking is not None:
            self._tracking.unregister_clips(self.PROVIDER_NAME)
            await self._tracking.detach(self._driver)
        self._driver.attach_transport(None)
        if self._datagram is not None:
            self._datagram.close()
            self._datagram = None

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
            "tracking": self._tracking is not None,
            "tracking_sending": self._driver.ready(),
            "emotion": self._tracking.current_emotion() if self._tracking is not None else "",
        }

    # ===== 内部辅助 =====

    async def _open_datagram(self, host: str, port: int) -> Any:
        """开一个只发不收的 UDP 端点，对准 Warudo 的面捕接收器。"""
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _QuietProtocol(self.logger), remote_addr=(host, port)
        )
        return transport

    @property
    def _ws_closed(self) -> bool:
        if self.websocket is None:
            return True
        code = getattr(self.websocket, "close_code", None)
        if code is not None:
            return True
        return getattr(self.websocket, "closed", False)

    def _is_ready_to_send(self) -> bool:
        if not self._is_connected or self.websocket is None:
            return False
        return not self._ws_closed

    async def _send_action_internal(self, action: str, data: Any) -> bool:
        """经蓝图通道发一个动作；没连上时记一条警告并返回 False。"""
        if not self._is_ready_to_send():
            self.logger.warning(f"Warudo 蓝图通道未连接，无法发送动作: {action}")
            return False
        try:
            self._action_sender.set_websocket(self.websocket)
            return await self._action_sender.send_action(action, data)
        except Exception as e:
            self.logger.error(f"发送动作失败: {action}: {e}")
            return False

    async def _send_marker(self, action: str, value: float) -> bool:
        """风格化标记逐帧变化：蓝图通道没连上时静默跳过（驱动下一帧再试），不刷警告。"""
        if not self._is_ready_to_send():
            return False
        self._action_sender.set_websocket(self.websocket)
        return await self._action_sender.send_action(action, value)

    async def _connect(self) -> None:
        if not WEBSOCKETS_AVAILABLE:
            self.logger.error("websockets 库未安装，无法连接 Warudo 蓝图通道")
            return

        self._should_stop = False
        uri = f"ws://{self.ws_host}:{self.ws_port}"

        try:
            self.websocket = await websockets.connect(uri)
            self._is_connected = True
            # 连接态换血：装配期降级登记的 0 工具在此补注册（回调由注册接线方提供）
            self.mark_connected()
            self.logger.info(f"已连接到 Warudo 蓝图通道: {uri}")
        except Exception as e:
            self.logger.warning(f"首次连接 Warudo 蓝图通道失败({e})，将由后台重连循环处理")
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
                        # 连接态换血：断连后工具页显示未连接
                        self.mark_disconnected()
                    delay_s = self._reconnect_backoff.next_delay_ms() / 1000
                    self.logger.debug(f"WebSocket 断开，{delay_s:.0f}秒后重连...")
                    try:
                        await asyncio.sleep(delay_s)
                    except asyncio.CancelledError:
                        break

        self.logger.info("Warudo WebSocket 重连循环已退出")


# =============================================================================
# 工厂 / 注册辅助
# =============================================================================


def create_warudo_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    tracking: Optional[Any] = None,
    datagram_factory: Optional[DatagramFactory] = None,
    clock: Callable[[], int] = now_ms,
) -> WarudoProvider:
    return WarudoProvider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
        tracking=tracking,
        datagram_factory=datagram_factory,
        clock=clock,
    )


def register_warudo_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    lipsync_analyzer: Optional[Any] = None,
    subtitle_service: Optional[Any] = None,
    tracking: Optional[Any] = None,
) -> WarudoProvider:
    """构造 WarudoProvider 并注册到 registry。返回 Provider 实例供调用方管理生命周期。

    不登记 LLM 工具；连接态回调经 ``wire_connection_tools`` 接入，工具页的连接态与蓝图通道一致。
    """
    provider = create_warudo_provider(
        config=config,
        event_bus=event_bus,
        lipsync_analyzer=lipsync_analyzer,
        tracking=tracking,
    )
    registry.register_provider(provider)
    wire_connection_tools(provider, registry)
    return provider


__all__ = ["BUILTIN_MOTIONS", "WarudoProvider", "create_warudo_provider", "register_warudo_tools"]
