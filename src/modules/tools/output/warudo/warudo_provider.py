"""
WarudoProvider - Warudo 虚拟形象工具集（Wave 4 / §1.5）

迁移自 ``src.stages.output.handlers.avatar.warudo.WarudoHandler``，将其
从 3 阶段 OutputHandler 改写为 ToolProvider 协议实现：

- 引擎子件（``WarudoStateManager`` + 5 state 类 / 5 后台任务 / ``WarudoSubtitleManager``
  / ``ActionSender``）verbatim 复用，仅 import 路径变更。
- ``AvatarHandlerBase`` 继承被去除；ToolProvider 协议由本类自身实现。
- 暴露的工具：
  - ``warudo_set_expression``   - 设置 blendshape 表情参数
  - ``warudo_trigger_hotkey``   - 触发热键
  - ``warudo_body_action``      - 触发身体动作
  - ``warudo_head_action``      - 触发头部动作
  - ``warudo_direct_action``    - 直接动作（蓝图节点名）
  - ``warudo_push_subtitle``    - 推送字幕文本
  - ``warudo_throw_fish``       - 抛鱼动画（带冷却）
  - ``warudo_set_sight``        - 设置视线状态
  - ``warudo_set_eyebrow``      - 设置眉毛状态
  - ``warudo_set_eye``          - 设置眼睛状态
  - ``warudo_set_pupil``        - 设置瞳孔方向
  - ``warudo_set_mouth``        - 设置嘴巴第一层状态
  - ``warudo_get_stats``        - 读取状态统计
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Dict, Optional


from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec

from .state.warudo_state_manager import WarudoStateManager
from .subtitle.subtitle_manager import WarudoSubtitleManager
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
        "name": {"type": "string", "description": "blendshape 参数名"},
        "value": {"type": "number", "description": "目标值"},
    },
    "required": ["name", "value"],
}

_WARUDO_TRIGGER_HOTKEY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "hotkey_id": {"type": "string", "description": "热键 ID（动作名）"},
    },
    "required": ["hotkey_id"],
}

_WARUDO_BODY_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "description": "身体动作（中文）"},
    },
    "required": ["action"],
}

_WARUDO_HEAD_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "description": "头部动作（中文）"},
    },
    "required": ["action"],
}

_WARUDO_DIRECT_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "description": "直接动作（蓝图节点名）"},
        "data": {"type": "number", "description": "Integer 数据"},
    },
    "required": ["action"],
}

_WARUDO_PUSH_SUBTITLE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "speech": {"type": "string", "description": "字幕文本"},
        "user_name": {"type": "string", "default": "MaiBot", "description": "用户名"},
    },
    "required": ["speech"],
}

_WARUDO_STATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "key": {"type": "string", "description": "状态键"},
        "intensity": {"type": "number", "description": "强度（0.0~1.0）"},
    },
    "required": ["key"],
}


# =============================================================================
# WarudoProvider
# =============================================================================


class WarudoProvider:
    """Warudo 虚拟形象 ToolProvider

    实现 ToolProvider 协议 + WarudoHandler 编排器职责（verbatim）。
    """

    PROVIDER_NAME = "warudo"

    _EMOTION_BLENDSHAPE_MAP: Dict[str, Dict[str, Dict[str, float]]] = {
        "happy": {
            "high": {"mouth_happy_strong": 1.0, "eyebrow_happy_strong": 0.8, "eye_happy_weak": 0.5},
            "low": {"mouth_smlie_3": 1.0, "eyebrow_happy_weak": 0.5, "eye_happy_weak": 0.3},
        },
        "sad": {
            "high": {"eyebrow_sad_strong": 1.0, "mouth_sad_weak": 0.8},
            "low": {"eyebrow_sad_weak": 0.8, "mouth_sad_weak": 0.5},
        },
        "angry": {
            "high": {"eyebrow_angry_strong": 1.0, "mouth_angry_weak": 0.6},
            "low": {"eyebrow_angry_weak": 0.8, "mouth_angry_weak": 0.4},
        },
        "surprised": {
            "high": {"mouth_happy_strong": 1.0, "eyebrow_happy_strong": 0.8},
            "low": {"mouth_smlie_3": 0.6, "eyebrow_happy_weak": 0.5},
        },
        "shy": {
            "high": {"mouth_smlie_3": 1.0, "eye_happy_weak": 0.5, "eyebrow_happy_weak": 0.3},
            "low": {"mouth_smlie_2": 0.8, "eye_happy_weak": 0.3},
        },
        "love": {
            "high": {"mouth_smlie_3": 1.0, "eye_happy_weak": 0.6},
            "low": {"mouth_smlie_2": 0.8, "eye_happy_weak": 0.3},
        },
        "excited": {
            "high": {
                "mouth_happy_strong": 1.0,
                "eyebrow_happy_strong": 1.0,
                "eye_happy_weak": 0.8,
            },
            "low": {"mouth_smlie_3": 1.0, "eyebrow_happy_weak": 0.6, "eye_happy_weak": 0.5},
        },
        "confused": {
            "high": {"eyebrow_sad_strong": 0.8, "mouth_sad_weak": 0.4},
            "low": {"eyebrow_sad_weak": 0.6},
        },
        "scared": {
            "high": {"eyebrow_sad_strong": 1.0, "mouth_happy_strong": 0.8},
            "low": {"eyebrow_sad_weak": 0.6, "mouth_happy_strong": 0.4},
        },
        "thinking": {
            "high": {"eyebrow_sad_weak": 0.8, "mouth_sad_weak": 0.4},
            "low": {"mouth_sad_weak": 0.3},
        },
        "relaxed": {
            "high": {"eye_happy_weak": 0.5, "mouth_smlie_2": 0.6},
            "low": {"eye_happy_weak": 0.3, "mouth_smlie_2": 0.3},
        },
        "neutral": {"high": {}, "low": {}},
    }

    _INTENSITY_HIGH_THRESHOLD: float = 0.7
    _INTENSITY_LOW_THRESHOLD: float = 0.3

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        # 配置
        self.ws_host: str = str(config.get("ws_host", "localhost"))
        self.ws_port: int = int(config.get("ws_port", 19190))
        self.reconnect_delay_seconds: float = float(config.get("reconnect_delay_seconds", 5.0))
        self.subtitle_enabled: bool = bool(config.get("subtitle_enabled", True))
        self.subtitle_port: int = int(config.get("subtitle_port", 8766))
        self.subtitle_show_status: bool = bool(config.get("subtitle_show_status", False))
        self.talking_head_enabled: bool = bool(config.get("talking_head_enabled", True))
        self.talking_head_interval: float = float(config.get("talking_head_interval", 0.1))
        self.throw_fish_cooldown: float = float(config.get("throw_fish_cooldown", 5.0))

        # Action 三字典分类
        self._action_hotkey_map: Dict[str, str] = {}
        self._action_body_map: Dict[str, str] = {
            "calm_pose": "平静，双手后放",
            "think": "思考",
        }
        self._action_head_map: Dict[str, str] = {
            "nod": "点头一次",
            "shake": "摇头",
        }
        self._action_direct_map: Dict[str, str] = {
            "throw_fish": "throw_fish",
        }
        self._action_map = self._action_head_map

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

        self.subtitle_manager: Optional[WarudoSubtitleManager] = None
        if self.subtitle_enabled:
            self.subtitle_manager = WarudoSubtitleManager(
                port=self.subtitle_port,
                show_status=self.subtitle_show_status,
                logger=self.logger,
            )

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
                name="warudo_set_expression",
                description="Warudo 设置 blendshape 表情参数",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="warudo_trigger_hotkey",
                description="Warudo 触发热键",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_TRIGGER_HOTKEY_SCHEMA,
            ),
            ToolSpec(
                name="warudo_body_action",
                description="Warudo 触发身体动作（姿势.json 蓝图）",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_BODY_ACTION_SCHEMA,
            ),
            ToolSpec(
                name="warudo_head_action",
                description="Warudo 触发头部动作（头部动态.json 蓝图）",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_HEAD_ACTION_SCHEMA,
            ),
            ToolSpec(
                name="warudo_direct_action",
                description="Warudo 直接动作（蓝图节点名）",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_DIRECT_ACTION_SCHEMA,
            ),
            ToolSpec(
                name="warudo_push_subtitle",
                description="Warudo 推送字幕文本（one-shot 模式）",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_PUSH_SUBTITLE_SCHEMA,
            ),
            ToolSpec(
                name="warudo_throw_fish",
                description="Warudo 抛鱼动画（带冷却）",
                kind="sync",
                provider="builtin",
            ),
            ToolSpec(
                name="warudo_set_sight",
                description="Warudo 设置视线状态（camera/danmu/phone）",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_STATE_SCHEMA,
            ),
            ToolSpec(
                name="warudo_set_eyebrow",
                description="Warudo 设置眉毛状态",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_STATE_SCHEMA,
            ),
            ToolSpec(
                name="warudo_set_eye",
                description="Warudo 设置眼睛状态",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_STATE_SCHEMA,
            ),
            ToolSpec(
                name="warudo_set_pupil",
                description="Warudo 设置瞳孔方向",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_STATE_SCHEMA,
            ),
            ToolSpec(
                name="warudo_set_mouth",
                description="Warudo 设置嘴巴第一层状态",
                kind="sync",
                provider="builtin",
                parameters_schema=_WARUDO_STATE_SCHEMA,
            ),
            ToolSpec(
                name="warudo_get_stats",
                description="读取 Warudo 状态统计",
                kind="sync",
                provider="builtin",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        args = invocation.arguments or {}
        try:
            n = invocation.tool_name
            if n == "warudo_set_expression":
                return _ok(n, True, await self._send_expression(str(args["name"]), float(args["value"])))
            if n == "warudo_trigger_hotkey":
                return _ok(n, True, await self._send_hotkey(str(args["hotkey_id"])))
            if n == "warudo_body_action":
                return _ok(n, True, await self._send_action_internal("body_action", str(args["action"])))
            if n == "warudo_head_action":
                return _ok(n, True, await self._send_action_internal("head_action", str(args["action"])))
            if n == "warudo_direct_action":
                return _ok(
                    n,
                    True,
                    await self._send_action_internal(str(args["action"]), int(args.get("data", 1))),
                )
            if n == "warudo_push_subtitle":
                await self.push_subtitle(str(args["speech"]), str(args.get("user_name", "MaiBot")))
                return _ok(n, True)
            if n == "warudo_throw_fish":
                await self.throw_fish_task.throw_fish()
                return _ok(n, True)
            if n == "warudo_set_sight":
                self.state_manager.sight_state.set_state(str(args["key"]), float(args.get("intensity", 1.0)))
                return _ok(n, True)
            if n == "warudo_set_eyebrow":
                self.state_manager.eyebrow_state.set_first_layer(str(args["key"]), float(args.get("intensity", 1.0)))
                return _ok(n, True)
            if n == "warudo_set_eye":
                self.state_manager.eye_state.set_first_layer(str(args["key"]), float(args.get("intensity", 1.0)))
                return _ok(n, True)
            if n == "warudo_set_pupil":
                self.state_manager.pupil_state.set_state(str(args["key"]), float(args.get("intensity", 1.0)))
                return _ok(n, True)
            if n == "warudo_set_mouth":
                self.state_manager.mouth_state.set_first_layer(str(args["key"]), float(args.get("intensity", 1.0)))
                return _ok(n, True)
            if n == "warudo_get_stats":
                return _ok(n, True, self.get_stats())
            return _fail(n, f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'")
        except Exception as exc:  # noqa: BLE001 — Provider 边界兜底
            self.logger.error(f"Warudo 工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 生命周期 =====

    async def setup(self) -> None:
        if self._has_started:
            self.logger.warning("WarudoProvider 已启动，跳过重复 setup")
            return

        await self._connect()

        self._has_started = True
        self.logger.info(f"{self.__class__.__name__} 已启动")

    async def cleanup(self) -> None:
        if not self._has_started:
            return

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

        if self.subtitle_manager is not None:
            try:
                await self.subtitle_manager.stop_server()
            except Exception as e:
                self.logger.error(f"停止字幕服务器失败: {e}")

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

    async def push_subtitle(self, speech: str, user_name: str = "MaiBot") -> None:
        if not self.subtitle_manager or not speech:
            return
        try:
            await self.subtitle_manager.start_generation(user_name)
            await self.subtitle_manager.add_chunk(speech)
            await self.subtitle_manager.complete_generation()
            self.logger.debug(f"字幕已推送: {speech[:50]}...")
        except Exception as e:
            self.logger.error(f"字幕推送失败: {e}")

    async def start_talking(self) -> None:
        if self.talking_head_task is not None:
            self.talking_head_task.is_talking = True
        self.state_manager.sight_state.set_state("camera", 1.0)
        await self._send_action_internal("loading", "")

    async def stop_talking(self) -> None:
        if self.talking_head_task is not None:
            self.talking_head_task.is_talking = False
        self.state_manager.sight_state.set_state("camera", 0.0)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "subtitle_enabled": self.subtitle_manager is not None,
            "talking_head_running": self.talking_head_task.running if self.talking_head_task else False,
        }

    # ===== 内部辅助（verbatim 复用 WarudoHandler 写法） =====

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

    async def _send_expression(self, param_name: str, param_value: float) -> bool:
        if not self._is_ready_to_send():
            self.logger.warning(f"Warudo 未连接，无法设置参数: {param_name} = {param_value}")
            return False
        try:
            message = {"action": param_name, "data": param_value}
            if hasattr(self.websocket, "send_json"):
                await self.websocket.send_json(message)
            else:
                await self.websocket.send(json.dumps(message))
            self.logger.debug(f"设置 Warudo 参数: {param_name} = {param_value}")
            return True
        except Exception as e:
            self.logger.error(f"设置 Warudo 参数失败: {param_name}: {e}")
            return False

    async def _send_hotkey(self, hotkey_id: str) -> bool:
        if not self._is_ready_to_send():
            self.logger.warning(f"Warudo 未连接，无法触发热键: {hotkey_id}")
            return False
        try:
            message = {"action": hotkey_id, "data": ""}
            if hasattr(self.websocket, "send_json"):
                await self.websocket.send_json(message)
            else:
                await self.websocket.send(json.dumps(message))
            self.logger.debug(f"触发热键: {hotkey_id}")
            return True
        except Exception as e:
            self.logger.error(f"触发热键失败: {hotkey_id}: {e}")
            return False

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
) -> WarudoProvider:
    return WarudoProvider(
        config=config,
        event_bus=event_bus,
    )


def register_warudo_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> WarudoProvider:
    provider = create_warudo_provider(
        config=config,
        event_bus=event_bus,
    )
    if hasattr(registry, "register_provider"):
        registry.register_provider(provider)
    else:
        for spec in provider.list_tools():
            registry.register(spec, provider.invoke)
    return provider
