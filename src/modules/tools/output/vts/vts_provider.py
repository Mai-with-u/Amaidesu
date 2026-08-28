"""
VTSProvider - VTS 虚拟形象工具集（Wave 4 / §1.5）

迁移自 ``src.stages.output.handlers.avatar.vts.VTSHandler``，将其从 3 阶段
OutputHandler 改写为 ToolProvider 协议实现：

- 引擎子件（``LipSyncProcessor`` / ``ExpressionController`` / ``HotkeyMatcher``
  / ``IdleMotionController``）经 callback 解耦，verbatim 复用。
- ``AvatarHandlerBase`` 继承被去除；ToolProvider 协议由本类自身实现。
- 暴露的工具：
  - ``vts_smile``             - 设置 MouthSmile 参数
  - ``vts_close_eyes``        - 闭眼
  - ``vts_open_eyes``         - 睁眼
  - ``vts_set_expression``    - 设置多个表情参数（multi-parameter）
  - ``vts_set_parameter_value`` - 设置单参数
  - ``vts_get_parameter_value``  - 读取参数
  - ``vts_trigger_hotkey``    - 触发热键
  - ``vts_load_item``         - 加载 VTS 道具/贴纸
  - ``vts_load_sticker``      - 由 ``OUTPUT_STICKER_COMMAND`` 触发
  - ``vts_set_idle_enabled``  - 启停 idle 拟人动画
  - ``vts_reconnect``         - 手动触发重连
  - ``vts_get_stats``         - 读取状态统计

迁移策略与权威参考：
- 引擎逻辑 verbatim（来自 VTSHandler + LipSyncProcessor + ExpressionController
  + HotkeyMatcher + IdleMotionController，零改动）
- 仅修改 import 路径与外层结构（去 base 继承，注入 ToolProvider 接口）
- ``OUTPUT_STICKER_COMMAND`` 事件订阅保留在 ``setup()`` 钩子内
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional


from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)

from .expression_controller import ExpressionController
from .hotkey_matcher import HotkeyMatcher
from .idle_motion_controller import IdleMotionController
from .lip_sync_processor import LipSyncProcessor

if TYPE_CHECKING:
    pass


LLM_AVAILABLE = False
try:
    import openai  # noqa: F401

    LLM_AVAILABLE = True
except ImportError:
    pass


# =============================================================================
# 工具的 JSON Schema 描述
# =============================================================================

_VTS_SMILE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "value": {
            "type": "number",
            "default": 1.0,
            "minimum": -1.0,
            "maximum": 1.0,
            "description": "MouthSmile 参数值",
        }
    },
}

_VTS_SET_EXPRESSION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "parameters": {
            "type": "object",
            "description": "参数名 -> 数值 映射",
            "additionalProperties": {"type": "number"},
        },
        "weight": {
            "type": "number",
            "default": 1.0,
            "description": "VTS 权重参数（与跟踪输入的混合权重）",
        },
    },
    "required": ["parameters"],
}

_VTS_SET_PARAMETER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "VTS 参数名"},
        "value": {"type": "number", "description": "目标值"},
        "weight": {"type": "number", "default": 1.0, "description": "权重"},
    },
    "required": ["name", "value"],
}

_VTS_GET_PARAMETER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "VTS 参数名"},
    },
    "required": ["name"],
}

_VTS_TRIGGER_HOTKEY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "hotkey_id": {"type": "string", "description": "VTS 热键 ID"},
    },
    "required": ["hotkey_id"],
}

_VTS_LOAD_ITEM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_name": {"type": "string", "default": "filename.png"},
        "position_x": {"type": "number", "default": 0},
        "position_y": {"type": "number", "default": 0.5},
        "size": {"type": "number", "default": 0.33},
        "rotation": {"type": "number", "default": 90},
        "fade_time": {"type": "number", "default": 0.5},
        "order": {"type": "integer", "default": 4},
        "fail_if_order_taken": {"type": "boolean", "default": False},
        "smoothing": {"type": "number", "default": 0},
        "censored": {"type": "boolean", "default": False},
        "flipped": {"type": "boolean", "default": False},
        "locked": {"type": "boolean", "default": False},
        "unload_when_plugin_disconnects": {"type": "boolean", "default": True},
        "custom_data_base64": {"type": "string", "default": ""},
    },
}

_VTS_SET_IDLE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean", "description": "是否启用 idle 拟人动画"},
    },
    "required": ["enabled"],
}


# =============================================================================
# VTSProvider
# =============================================================================


class VTSProvider:
    """VTS 虚拟形象 ToolProvider

    实现 ToolProvider 协议 + VTSHandler 编排器职责（verbatim）。
    推荐通过 ``create_vts_provider(config, event_bus)``
    构造与 setup/cleanup 流程管理。
    """

    PROVIDER_NAME = "vts"

    PARAM_MOUTH_SMILE = "MouthSmile"
    PARAM_MOUTH_OPEN = "MouthOpen"
    PARAM_EYE_OPEN_LEFT = "EyeOpenLeft"
    PARAM_EYE_OPEN_RIGHT = "EyeOpenRight"

    # VTS 断线自动重连间隔（秒）：覆盖 Amaidesu 先于 VTS 启动、VTS 中途重启两种场景
    _RECONNECT_INTERVAL_S = 5.0

    _IDLE_PARAM_FALLBACKS: Dict[str, tuple[str, ...]] = {
        "head_x": ("HeadAngleX", "HeadX", "FaceAngleX", "FaceX", "NeckAngleX"),
        "head_y": ("HeadAngleY", "HeadY", "FaceAngleY", "FaceY", "NeckAngleY"),
        "head_z": ("HeadAngleZ", "HeadZ", "FaceAngleZ", "FaceZ", "NeckAngleZ"),
        "body_x": ("BodyAngleX", "BodyX", "BodyRotationX", "TorsoAngleX", "BodyPositionX"),
        "body_y": ("BodyAngleY", "BodyY", "BodyRotationY", "TorsoAngleY", "BodyPositionY"),
        "body_z": ("BodyAngleZ", "BodyZ", "BodyRotationZ", "TorsoAngleZ", "BodyPositionZ"),
    }

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
        prompt_service: Any = None,
    ):
        # 配置
        self.config = config
        self.event_bus = event_bus
        self._prompt_service = prompt_service
        self.logger = get_logger(self.__class__.__name__)

        self.vts_host: str = config.get("vts_host", "localhost")
        self.vts_port: int = int(config.get("vts_port", 8001))
        self.lip_sync_enabled: bool = bool(config.get("lip_sync_enabled", True))
        self.sample_rate: int = int(config.get("sample_rate", 16000))

        self._emotion_map = {
            "happy": {"MouthSmile": 1.0},
            "surprised": {"EyeOpenLeft": 1.0, "EyeOpenRight": 1.0, "MouthOpen": 0.5},
            "sad": {"MouthSmile": -0.3, "EyeOpenLeft": 0.7, "EyeOpenRight": 0.7},
            "angry": {"EyeOpenLeft": 0.6, "EyeOpenRight": 0.6, "MouthSmile": -0.5},
            "shy": {"MouthSmile": 0.3, "EyeOpenLeft": 0.8, "EyeOpenRight": 0.8},
            "love": {"MouthSmile": 0.8, "EyeOpenLeft": 0.9, "EyeOpenRight": 0.9},
            "excited": {"MouthSmile": 1.0, "EyeOpenLeft": 1.0, "EyeOpenRight": 1.0},
            "confused": {"EyeOpenLeft": 0.7, "EyeOpenRight": 0.7, "MouthOpen": 0.2},
            "scared": {"EyeOpenLeft": 0.5, "EyeOpenRight": 0.5, "MouthOpen": 0.3},
            "neutral": {},
        }
        self._action_hotkey_map = {
            "blink": "Blink",
            "nod": "Nod",
            "shake": "Shake",
            "wave": "Wave",
            "clap": "Clap",
            "motion": "Motion",
        }
        self._sticker_subscribed = False

        self._vts: Any = None
        self._vts_api_lock = asyncio.Lock()
        self._is_connecting = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._has_started = False

        self.render_count = 0
        self.error_count = 0

        # 子组件 — verbatim 复用（构造签名不变）
        self.lip_sync = LipSyncProcessor(
            logger_name=f"{self.__class__.__name__}.LipSync",
            sample_rate=self.sample_rate,
            volume_threshold=float(config.get("volume_threshold", 0.01)),
            smoothing_factor=float(config.get("smoothing_factor", 0.3)),
            vowel_detection_sensitivity=float(config.get("vowel_detection_sensitivity", 0.5)),
            vts_set_parameter=self._expression_set_param_proxy,
            is_connected=lambda: self._is_connected,
            volume_gain=float(config.get("volume_gain", 1.0)),
            max_mouth_open=float(config.get("max_mouth_open", 0.6)),
            silence_threshold=float(config.get("silence_threshold", 0.02)),
            close_mouth_threshold=float(config.get("close_mouth_threshold", 0.06)),
            power_curve=float(config.get("power_curve", 1.0)),
            vowel_open_weight=float(config.get("vowel_open_weight", 0.5)),
            update_interval_ms=float(config.get("update_interval_ms", 30.0)),
            mouth_open_lerp_speed=float(config.get("mouth_open_lerp_speed", 0.35)),
            vowel_decay=float(config.get("vowel_decay", 0.4)),
            min_mouth_delta=float(config.get("min_mouth_delta", 0.005)),
            expression_rest_values={
                self.PARAM_MOUTH_SMILE: float(config.get("base_smile", 0.3)),
                self.PARAM_EYE_OPEN_LEFT: 1.0,
                self.PARAM_EYE_OPEN_RIGHT: 1.0,
            },
        )
        self.hotkey_matcher = HotkeyMatcher(
            logger_name=f"{self.__class__.__name__}.Hotkey",
            is_connected=lambda: self._is_connected,
            vts_request=self._make_vts_request_proxy(),
            prompt_service=self._prompt_service,
            openai_client=self._build_openai_client(config),
            llm_model=str(config.get("llm_model", "gpt-4o-mini")),
            llm_temperature=float(config.get("llm_temperature", 0.7)),
            llm_max_tokens=int(config.get("llm_max_tokens", 50)),
            llm_matching_enabled=bool(config.get("llm_matching_enabled", False)),
        )
        self.expression = ExpressionController(
            logger_name=f"{self.__class__.__name__}.Expression",
            is_connected=lambda: self._is_connected,
            vts_request=self._make_vts_request_proxy(),
        )
        self.idle_motion = IdleMotionController(
            logger_name=f"{self.__class__.__name__}.IdleMotion",
            is_connected=lambda: self._is_connected,
            is_speaking=lambda: self.lip_sync.is_speaking,
            set_parameter=self._idle_set_param_proxy,
            param_head_x=str(config.get("idle_param_head_x", "HeadAngleX")),
            param_head_y=str(config.get("idle_param_head_y", "HeadAngleY")),
            param_head_z=str(config.get("idle_param_head_z", "HeadAngleZ")),
            param_body_x=str(config.get("idle_param_body_x", "BodyX")),
            param_body_y=str(config.get("idle_param_body_y", "BodyY")),
            param_body_z=str(config.get("idle_param_body_z", "BodyZ")),
            head_amplitude=float(config.get("idle_head_amplitude", 0.05)),
            body_amplitude=float(config.get("idle_body_amplitude", 0.02)),
            speed=float(config.get("idle_speed", 1.0)),
            update_interval_ms=float(config.get("idle_update_interval_ms", 40.0)),
            fade_speed=float(config.get("idle_fade_speed", 0.15)),
            head_enabled=bool(config.get("idle_head_enabled", True)),
            body_enabled=bool(config.get("idle_body_enabled", True)),
            speech_pause_enabled=bool(config.get("idle_pause_while_speaking", False)),
            extra_params=config.get("idle_extra_params", {}) or {},
            extra_speed=config.get("idle_extra_speed", None),
        )
        self.idle_motion.set_baseline_params({self.PARAM_MOUTH_SMILE: float(config.get("base_smile", 0.3))})

        self.idle_enabled_cfg = bool(config.get("idle_enabled", True))

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> List[ToolSpec]:
        """声明本 Provider 暴露的工具列表"""
        return [
            ToolSpec(
                name="vts_smile",
                description="设置 VTS MouthSmile 表情参数",
                kind="sync",
                provider="builtin",
                parameters_schema=_VTS_SMILE_SCHEMA,
            ),
            ToolSpec(
                name="vts_close_eyes",
                description="VTS 闭眼动作（EyeOpenLeft/Right=0）",
                kind="sync",
                provider="builtin",
            ),
            ToolSpec(
                name="vts_open_eyes",
                description="VTS 睁眼动作（EyeOpenLeft/Right=1）",
                kind="sync",
                provider="builtin",
            ),
            ToolSpec(
                name="vts_set_expression",
                description="VTS 批量设置表情参数（multi-parameter 写入）",
                kind="sync",
                provider="builtin",
                parameters_schema=_VTS_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="vts_set_parameter_value",
                description="VTS 设置单个参数值",
                kind="sync",
                provider="builtin",
                parameters_schema=_VTS_SET_PARAMETER_SCHEMA,
            ),
            ToolSpec(
                name="vts_get_parameter_value",
                description="VTS 读取参数当前值",
                kind="sync",
                provider="builtin",
                parameters_schema=_VTS_GET_PARAMETER_SCHEMA,
            ),
            ToolSpec(
                name="vts_trigger_hotkey",
                description="VTS 触发热键（按 hotkey_id）",
                kind="sync",
                provider="builtin",
                parameters_schema=_VTS_TRIGGER_HOTKEY_SCHEMA,
            ),
            ToolSpec(
                name="vts_load_item",
                description="VTS 加载道具（VTube Studio ItemLoadRequest）",
                kind="sync",
                provider="builtin",
                parameters_schema=_VTS_LOAD_ITEM_SCHEMA,
            ),
            ToolSpec(
                name="vts_load_sticker",
                description="VTS 加载贴纸（由 OUTPUT_STICKER_COMMAND 事件触发）",
                kind="sync",
                provider="builtin",
            ),
            ToolSpec(
                name="vts_set_idle_enabled",
                description="VTS 启停 idle 拟人动画",
                kind="sync",
                provider="builtin",
                parameters_schema=_VTS_SET_IDLE_SCHEMA,
            ),
            ToolSpec(
                name="vts_reconnect",
                description="手动触发 VTS 重连循环",
                kind="sync",
                provider="builtin",
            ),
            ToolSpec(
                name="vts_get_stats",
                description="读取 VTS 状态统计信息",
                kind="sync",
                provider="builtin",
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """ToolProvider.invoke：分发到对应的 handler 方法。"""
        args = invocation.arguments or {}
        try:
            if invocation.tool_name == "vts_smile":
                return _ok("vts_smile", await self.smile(float(args.get("value", 1.0))))
            if invocation.tool_name == "vts_close_eyes":
                return _ok("vts_close_eyes", await self.close_eyes())
            if invocation.tool_name == "vts_open_eyes":
                return _ok("vts_open_eyes", await self.open_eyes())
            if invocation.tool_name == "vts_set_expression":
                return _ok(
                    "vts_set_expression",
                    await self.expression.set_multi_parameter(
                        dict(args.get("parameters", {})),
                        float(args.get("weight", 1.0)),
                    ),
                )
            if invocation.tool_name == "vts_set_parameter_value":
                return _ok(
                    "vts_set_parameter_value",
                    await self.set_parameter_value(
                        str(args["name"]), float(args["value"]), float(args.get("weight", 1.0))
                    ),
                )
            if invocation.tool_name == "vts_get_parameter_value":
                value = await self.get_parameter_value(str(args["name"]))
                return _ok("vts_get_parameter_value", value is not None, {"value": value})
            if invocation.tool_name == "vts_trigger_hotkey":
                return _ok(
                    "vts_trigger_hotkey",
                    await self.trigger_hotkey(str(args["hotkey_id"])),
                )
            if invocation.tool_name == "vts_load_item":
                instance_id = await self.load_item(**{k: v for k, v in args.items() if k != ""})
                return _ok("vts_load_item", instance_id is not None, {"instance_id": instance_id})
            if invocation.tool_name == "vts_load_sticker":
                # 由 OUTPUT_STICKER_COMMAND 触发；直接传 payload 模式
                instance_id = await self.load_item(
                    file_name=str(args.get("file_name", "sticker.png")),
                    custom_data_base64=str(args.get("image_base64", "")),
                    size=float(args.get("size", 0.33)),
                    rotation=int(args.get("rotation", 0)),
                    position_x=float(args.get("position_x", 0.0)),
                    position_y=float(args.get("position_y", 0.0)),
                )
                return _ok("vts_load_sticker", instance_id is not None, {"instance_id": instance_id})
            if invocation.tool_name == "vts_set_idle_enabled":
                self._set_idle_enabled(bool(args["enabled"]))
                return _ok("vts_set_idle_enabled", True)
            if invocation.tool_name == "vts_reconnect":
                if self._is_connected:
                    self._is_connected = False
                return _ok("vts_reconnect", True)
            if invocation.tool_name == "vts_get_stats":
                return _ok("vts_get_stats", True, self.get_stats())
            return _fail(
                invocation.tool_name,
                f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'",
            )
        except Exception as exc:  # noqa: BLE001 — Provider 边界兜底
            self.logger.error(f"VTS 工具 {invocation.tool_name} 调用异常: {exc}", exc_info=True)
            return _fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")

    # ===== 生命周期 =====

    async def setup(self) -> None:
        """Provider 生命周期入口（对应旧 VTSHandler.init()）"""
        if self._has_started:
            self.logger.warning("VTSProvider 已启动，跳过重复 setup")
            return

        try:
            import pyvts  # noqa: F401
            from pyvts import vts

            plugin_info = {
                "plugin_name": "Amaidesu_VTS_ToolProvider",
                "developer": "Wave 4 Implementation",
                "authentication_token_path": "./vts_token.txt",
                "vts_host": self.vts_host,
                "vts_port": self.vts_port,
            }
            vts_api_info = {
                "host": self.vts_host,
                "port": self.vts_port,
                "name": "VTubeStudioPublicAPI",
                "version": "1.0",
            }
            self._vts = vts(vts_plugin_info=plugin_info, vts_api_info=vts_api_info)
            self.logger.info("pyvts 实例创建成功")
        except ImportError:
            self.logger.error("pyvts 库不可用，VTSProvider 将被禁用")
            self._vts = None
            raise ImportError("pyvts library not available") from None

        await self._connect()

        # 启动断线自动重连循环
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._reconnect_task.set_name(f"{self.__class__.__name__}.reconnect_loop")

        # 订阅贴纸事件（保留 OUTPUT_STICKER_COMMAND 事件链路）
        if self.event_bus and not getattr(self, "_sticker_subscribed", False):
            from src.modules.events.payloads import StickerCommandPayload

            self.event_bus.on(
                CoreEvents.OUTPUT_STICKER_COMMAND,
                self._on_sticker_command,
                StickerCommandPayload,
            )
            self._sticker_subscribed = True

        self._has_started = True

    async def cleanup(self) -> None:
        """清理资源（对应旧 VTSHandler.cleanup()）"""
        if not self._has_started:
            return

        # 取消贴纸订阅
        sticker_handler = getattr(self, "_on_sticker_command", None)
        if self.event_bus and getattr(self, "_sticker_subscribed", False) and sticker_handler is not None:
            try:
                self.event_bus.off(
                    CoreEvents.OUTPUT_STICKER_COMMAND,
                    sticker_handler,
                )
            except Exception as e:
                self.logger.warning(f"取消订阅 {CoreEvents.OUTPUT_STICKER_COMMAND} 失败: {e}")
            finally:
                self._sticker_subscribed = False

        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法（保留旧 VTSHandler 接口） =====

    async def smile(self, value: float = 1) -> bool:
        return await self.expression.smile(value)

    async def close_eyes(self) -> bool:
        return await self.expression.close_eyes()

    async def open_eyes(self) -> bool:
        return await self.expression.open_eyes()

    async def set_parameter_value(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        return await self.expression.set_parameter(parameter_name, value, weight)

    async def get_parameter_value(self, parameter_name: str) -> Optional[float]:
        return await self.expression.get_parameter(parameter_name)

    async def trigger_hotkey(self, hotkey_id: str) -> bool:
        return await self.hotkey_matcher.trigger_hotkey(hotkey_id)

    async def load_item(
        self,
        file_name: str = "filename.png",
        position_x: float = 0,
        position_y: float = 0.5,
        size: float = 0.33,
        rotation: float = 90,
        fade_time: float = 0.5,
        order: int = 4,
        fail_if_order_taken: bool = False,
        smoothing: float = 0,
        censored: bool = False,
        flipped: bool = False,
        locked: bool = False,
        unload_when_plugin_disconnects: bool = True,
        custom_data_base64: str = "",
        custom_data_ask_user_first: bool = False,
        custom_data_skip_asking_user_if_whitelisted: bool = False,
        custom_data_ask_timer: int = -1,
    ) -> Optional[str]:
        if not self._is_connected:
            self.logger.warning("VTS 未连接，无法加载道具")
            return None
        try:
            data = {
                "fileName": file_name,
                "positionX": position_x,
                "positionY": position_y,
                "size": size,
                "rotation": rotation,
                "fadeTime": fade_time,
                "order": order,
                "failIfOrderTaken": fail_if_order_taken,
                "smoothing": smoothing,
                "censored": censored,
                "flipped": flipped,
                "locked": locked,
                "unloadWhenPluginDisconnects": unload_when_plugin_disconnects,
                "customDataBase64": custom_data_base64,
                "customDataAskUserFirst": custom_data_ask_user_first,
                "customDataSkipAskingUserIfWhitelisted": custom_data_skip_asking_user_if_whitelisted,
                "customDataAskTimer": custom_data_ask_timer,
            }
            response = await self._vts.request(
                self._vts.vts_request.BaseRequest(message_type="ItemLoadRequest", data=data)
            )
            if response and response.get("messageType") == "ItemLoadResponse":
                instance_id = response.get("data", {}).get("instanceID", None)
                if instance_id:
                    self.logger.debug(f"道具已加载: {instance_id}")
                    return instance_id
                self.logger.warning(f"道具加载失败: {response}")
                return None
            self.logger.warning(f"道具加载失败: {response}")
            return None
        except Exception as e:
            self.logger.error(f"加载道具失败: {e}", exc_info=True)
            return None

    async def unload_item(
        self,
        item_instance_id_list: Optional[List[str]] = None,
        file_name_list: Optional[List[str]] = None,
    ) -> bool:
        if not self._is_connected:
            self.logger.warning("VTS 未连接，无法卸载道具")
            return False
        try:
            if not item_instance_id_list and not file_name_list:
                return False
            data = {
                "instanceIDs": item_instance_id_list if item_instance_id_list else [],
                "fileNames": file_name_list if file_name_list else [],
            }
            response = await self._vts.request(
                self._vts.vts_request.BaseRequest(message_type="ItemUnloadRequest", data=data)
            )
            if response and response.get("messageType") == "ItemUnloadResponse":
                self.logger.debug(f"道具已卸载: {data}")
                return True
            self.logger.warning(f"道具卸载失败: {response}")
            return False
        except Exception as e:
            self.logger.error(f"卸载道具失败: {e}")
            return False

    def _set_idle_enabled(self, enabled: bool) -> None:
        """启停 idle 拟人动画（不抛异常，重复启停幂等）"""
        if enabled and not self.idle_motion._running:
            try:
                self.idle_motion.start()
            except Exception as e:
                self.logger.error(f"启动 idle 动画失败: {e}")
        elif not enabled and self.idle_motion._running:
            try:
                # 异步停止转后台任务，不阻塞调用方
                asyncio.create_task(self.idle_motion.stop())
            except Exception as e:
                self.logger.error(f"停止 idle 动画失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "hotkey_count": len(self.hotkey_matcher.hotkey_list),
            "lip_sync_enabled": self.lip_sync_enabled,
            "llm_matching_enabled": bool(self.config.get("llm_matching_enabled", False)),
        }

    # ===== 内部辅助（verbatim 复用 VTSHandler 写法） =====

    async def _idle_set_param_proxy(self, parameter_name: str, value: float) -> bool:
        if not parameter_name:
            return False
        return await self.expression.set_parameter(parameter_name, value, weight=1, silent=True)

    async def _expression_set_param_proxy(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        return await self.expression.set_parameter(parameter_name, value, weight)

    def _build_openai_client(self, config: Dict[str, Any]) -> Optional[Any]:
        llm_matching_enabled = bool(config.get("llm_matching_enabled", False))
        llm_api_key = config.get("llm_api_key")
        if not (llm_matching_enabled and LLM_AVAILABLE and llm_api_key):
            return None
        try:
            import openai as _openai

            client = _openai.AsyncOpenAI(
                api_key=llm_api_key,
                base_url=config.get("llm_base_url") if config.get("llm_base_url") else None,
            )
            self.logger.info("LLM 客户端初始化成功")
            return client
        except Exception as e:
            self.logger.warning(f"LLM 客户端初始化失败: {e}")
            return None

    def _make_vts_request_proxy(self) -> Any:
        """创建可调用代理，所有 VTS API 调用都经过同一把 asyncio.Lock 串行化"""
        import pyvts

        handler = self
        vts_request_builder = pyvts.VTSRequest()

        class VTSRequestProxy:
            async def __call__(self, request):
                async with handler._vts_api_lock:
                    return await handler._vts.request(request)

            @property
            def vts_request(self):
                return vts_request_builder

            def requestHotKeyList(self):
                return vts_request_builder.requestHotKeyList()

            def requestTriggerHotKey(self, **kwargs):
                return vts_request_builder.requestTriggerHotKey(**kwargs)

        return VTSRequestProxy()

    async def _resolve_idle_parameter_names(self) -> Dict[str, str]:
        available = set(await self.expression.list_tracking_parameters())
        config_names = {
            "head_x": str(self.config.get("idle_param_head_x", "HeadAngleX")),
            "head_y": str(self.config.get("idle_param_head_y", "HeadAngleY")),
            "head_z": str(self.config.get("idle_param_head_z", "HeadAngleZ")),
            "body_x": str(self.config.get("idle_param_body_x", "BodyX")),
            "body_y": str(self.config.get("idle_param_body_y", "BodyY")),
            "body_z": str(self.config.get("idle_param_body_z", "BodyZ")),
        }
        if not available:
            self.logger.warning("无法获取 VTS 参数列表，idle 动画将使用配置中的参数名")
            return config_names

        resolved: Dict[str, str] = {}
        for axis, user_name in config_names.items():
            candidates = (user_name,) + self._IDLE_PARAM_FALLBACKS.get(axis, ())
            chosen = next((name for name in candidates if name in available), None)
            if chosen:
                resolved[axis] = chosen
                if chosen != user_name:
                    self.logger.info(f"idle 参数回退：{axis} 配置名 '{user_name}' 在 VTS 中不可用，自动使用 '{chosen}'")
            else:
                resolved[axis] = user_name
                available_sample = sorted(available)[:30]
                self.logger.warning(
                    f"idle 参数 {axis} 在 VTS 中无可用候选（配置名 '{user_name}'，"
                    f"回退表 {candidates} 均不可用）。当前可用参数示例：{available_sample}。"
                )
        return resolved

    async def _connect(self) -> None:
        if self._is_connecting or self._is_connected:
            return
        self._is_connecting = True
        try:
            if not self._vts:
                self.logger.error("pyvts 未初始化")
                return

            self.logger.info(f"开始连接 VTS: {self.vts_host}:{self.vts_port}")
            await self._vts.connect()
            await self._vts.request_authenticate_token()
            await self._vts.request_authenticate()
            self._is_connected = True
            self.logger.info("VTS 连接成功")

            await self.hotkey_matcher.load_hotkeys()

            resolved = await self._resolve_idle_parameter_names()
            self.idle_motion.set_parameter_names(
                param_head_x=resolved.get("head_x"),
                param_head_y=resolved.get("head_y"),
                param_head_z=resolved.get("head_z"),
                param_body_x=resolved.get("body_x"),
                param_body_y=resolved.get("body_y"),
                param_body_z=resolved.get("body_z"),
            )

            if self.idle_enabled_cfg:
                try:
                    self.idle_motion.start()
                    self.logger.info("VTS idle 动画已启动")
                except Exception as e:
                    self.logger.error(f"启动 idle 动画失败: {e}")

            try:
                await self.expression.set_parameter(
                    self.PARAM_MOUTH_SMILE,
                    float(self.config.get("base_smile", 0.3)),
                    weight=1,
                    silent=True,
                )
            except Exception as e:
                self.logger.warning(f"应用常驻微笑基线失败: {e}")
        except Exception as e:
            self.logger.error(f"VTS 连接失败: {e}")
            self._is_connected = False
        finally:
            self._is_connecting = False

    async def _vts_health_check(self) -> bool:
        try:
            proxy = self._make_vts_request_proxy()
            response = await asyncio.wait_for(
                proxy(proxy.vts_request.requestParameterValue("FaceAngleX")),
                timeout=3.0,
            )
            return bool(response and response.get("messageType") == "ParameterValueResponse")
        except Exception:
            return False

    async def _reconnect_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._RECONNECT_INTERVAL_S)
                if self._is_connecting:
                    continue
                if not self._is_connected:
                    self.logger.info("VTS 未连接，尝试自动重连...")
                    await self._connect()
                    continue
                if not await self._vts_health_check():
                    self.logger.warning("VTS 连接已断开（VTS 可能已重启），准备自动重连")
                    self._is_connected = False
                    try:
                        await self._vts.close()
                    except Exception as e:
                        self.logger.debug(f"关闭旧 VTS 连接异常（忽略）: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"VTS 自动重连循环异常: {e}", exc_info=True)

    async def _disconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        try:
            await self.idle_motion.stop()
        except Exception as e:
            self.logger.warning(f"停止 idle 动画失败: {e}")

        if not self._is_connected or not self._vts:
            return
        try:
            await self._vts.close()
            self.logger.info("VTS 连接已关闭")
        except Exception as e:
            self.logger.warning(f"关闭 VTS 连接异常: {e}")
        finally:
            self._is_connected = False

    async def _on_sticker_command(self, event_name: str, payload: Any, source: str) -> None:
        if payload.target_handler != "vts":
            return
        self.logger.info(f"收到贴纸触发: sticker_id={payload.sticker_id}")
        if not payload.image_base64:
            self.logger.debug("贴纸事件未携带 image_base64，跳过渲染")
            return
        try:
            instance_id = await self.load_item(
                file_name=f"{payload.sticker_id}.png",
                position_x=payload.position_x or 0.0,
                position_y=payload.position_y or 0.0,
                size=payload.size or 0.33,
                rotation=payload.rotation or 0,
                fade_time=0.5,
                order=10,
                custom_data_base64=payload.image_base64,
            )
            if instance_id:
                self.logger.debug(f"贴纸已加载到 VTS: {instance_id}")
        except Exception as e:
            self.logger.error(f"加载贴纸到 VTS 失败: {e}", exc_info=True)


# =============================================================================
# 工厂与注册辅助
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


def create_vts_provider(
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    prompt_service: Any = None,
) -> VTSProvider:
    """构造 VTSProvider 实例（不启动，由调用方 setup）"""
    return VTSProvider(
        config=config,
        event_bus=event_bus,
        prompt_service=prompt_service,
    )


def register_vts_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
    prompt_service: Any = None,
) -> VTSProvider:
    """构造 VTSProvider 并注册到 registry。返回 Provider 实例供调用方管理生命周期。"""
    provider = create_vts_provider(
        config=config,
        event_bus=event_bus,
        prompt_service=prompt_service,
    )
    if hasattr(registry, "register_provider"):
        registry.register_provider(provider)
    else:
        # fallback：手动注册每个 spec
        for spec in provider.list_tools():
            registry.register(spec, provider.invoke)
    return provider
