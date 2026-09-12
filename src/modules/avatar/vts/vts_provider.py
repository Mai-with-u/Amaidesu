"""
VTSProvider - VTS 虚拟形象工具集

ToolProvider 协议实现，把 VTS 全家桶能力封装为工具：

- 引擎子件（``LipSyncProcessor`` / ``ExpressionController`` / ``HotkeyMatcher``
  / ``IdleMotionController``）经 callback 解耦，可独立复用。
- 暴露的工具：
  - ``vts_smile``             - 设置 MouthSmile 参数
  - ``vts_close_eyes``        - 闭眼
  - ``vts_open_eyes``         - 睁眼
  - ``vts_set_expression``    - 设置多个表情参数（multi-parameter）
  - ``vts_set_parameter_value`` - 设置单参数
  - ``vts_get_parameter_value``  - 读取参数
  - ``vts_trigger_hotkey``    - 触发热键
  - ``vts_load_item``         - 加载 VTS 道具/贴纸
  - ``vts_load_sticker``      - 直接调用加载贴纸文件（file_name 为 VTS 可访问路径）
  - ``vts_set_idle_enabled``  - 启停 idle 拟人动画
  - ``vts_reconnect``         - 手动触发重连
  - ``vts_get_stats``         - 读取状态统计
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider

from .expression_controller import ExpressionController
from .hotkey_matcher import HotkeyMatcher
from .idle_motion_controller import IdleMotionController
from .lip_sync_processor import LipSyncProcessor

if TYPE_CHECKING:
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
        "name": {"type": "string", "description": "VTS 热键名称（优先；连接后可从工具描述中的可用热键清单选取）"},
        "hotkey_id": {"type": "string", "description": "VTS 热键 ID（兜底；name 未匹配时使用）"},
    },
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


class VTSProvider(BaseToolProvider):
    """VTS 虚拟形象 ToolProvider

    实现 ToolProvider 协议，编排各引擎子件。
    推荐通过 ``create_vts_provider(config, event_bus)``
    构造与 setup/cleanup 流程管理。
    """

    PROVIDER_NAME = "vts"

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "avatar"

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
        "body_x": ("BodyAngleX", "BodyX", "BodyRotationX", "torsoAngleX", "BodyPositionX"),
        "body_y": ("BodyAngleY", "BodyY", "BodyRotationY", "torsoAngleY", "BodyPositionY"),
        "body_z": ("BodyAngleZ", "BodyZ", "BodyRotationZ", "torsoAngleZ", "BodyPositionZ"),
    }

    class ConfigSchema(BaseConfig):
        """VTS 配置（连接 + LipSync + Idle 三大段）

        TOML 段位：[tools.avatar.vts].config；本规范见 .omo/drafts/config-schema-spec.md

        说明：vts 字段多沿用历史命名（如 ``*_ms`` 实际单位是 float 秒）；
        本批保持行为保真（默认值 + 类型逐一等价），不顺手改单位/命名。
        """

        type: str = "vts"
        # 连接
        vts_host: str = Field(default="localhost", description="VTS WebSocket 主机地址")
        vts_port: int = Field(default=8001, ge=1, le=65535, description="VTS WebSocket 端口")
        lip_sync_enabled: bool = Field(default=True, description="是否启用 LipSync")
        sample_rate: int = Field(default=16000, ge=8000, le=48000, description="LipSync 采样率 Hz")
        # LipSync 详细（命名 *_ms 实际单位 = float 秒，行为保真）
        volume_threshold: float = Field(default=0.01, ge=0.0, description="LipSync 音量阈值")
        smoothing_factor: float = Field(default=0.3, ge=0.0, le=1.0, description="LipSync 平滑系数")
        vowel_detection_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0, description="LipSync 元音检测灵敏度")
        volume_gain: float = Field(default=1.0, ge=0.0, description="LipSync 音量增益")
        max_mouth_open: float = Field(default=0.6, ge=0.0, le=1.0, description="LipSync 最大张嘴度")
        silence_threshold: float = Field(default=0.02, ge=0.0, description="LipSync 静音阈值")
        close_mouth_threshold: float = Field(default=0.06, ge=0.0, description="LipSync 闭嘴阈值（低于此值触发闭嘴）")
        power_curve: float = Field(default=1.0, ge=0.0, description="LipSync 功率曲线指数")
        vowel_open_weight: float = Field(default=0.5, ge=0.0, description="LipSync 元音张嘴权重")
        update_interval_ms: float = Field(default=30.0, ge=0.0, description="LipSync 更新间隔（秒；命名沿用）")
        mouth_open_lerp_speed: float = Field(default=0.35, ge=0.0, description="LipSync 张嘴插值速度")
        vowel_decay: float = Field(default=0.4, ge=0.0, description="LipSync 元音衰减")
        min_mouth_delta: float = Field(default=0.005, ge=0.0, description="LipSync 最小张嘴变化阈值")
        base_smile: float = Field(default=0.3, ge=-1.0, le=1.0, description="MouthSmile 静止基线值")
        # Idle 运动
        idle_enabled: bool = Field(default=True, description="是否启用 Idle 拟人动画")
        idle_param_head_x: str = Field(default="HeadAngleX", description="Idle 头部 X 参数名")
        idle_param_head_y: str = Field(default="HeadAngleY", description="Idle 头部 Y 参数名")
        idle_param_head_z: str = Field(default="HeadAngleZ", description="Idle 头部 Z 参数名")
        idle_param_body_x: str = Field(default="BodyX", description="Idle 身体 X 参数名")
        idle_param_body_y: str = Field(default="BodyY", description="Idle 身体 Y 参数名")
        idle_param_body_z: str = Field(default="BodyZ", description="Idle 身体 Z 参数名")
        idle_head_amplitude: float = Field(default=0.05, ge=0.0, description="Idle 头部摆动幅度")
        idle_body_amplitude: float = Field(default=0.02, ge=0.0, description="Idle 身体摆动幅度")
        idle_speed: float = Field(default=1.0, ge=0.0, description="Idle 摆动速度系数")
        idle_update_interval_ms: float = Field(default=40.0, ge=0.0, description="Idle 更新间隔（秒；命名沿用）")
        idle_fade_speed: float = Field(default=0.15, ge=0.0, description="Idle 渐变速度")
        idle_head_enabled: bool = Field(default=True, description="Idle 头部摆动开关")
        idle_body_enabled: bool = Field(default=True, description="Idle 身体摆动开关")
        idle_pause_while_speaking: bool = Field(default=False, description="Idle 说话时是否暂停摆动")
        # 动态键：Idle 额外参数集（人类登记的额外参数名+速度；类似 MCP servers 动态键例外）
        idle_extra_params: Dict[str, float] = Field(
            default_factory=dict,
            description="Idle 额外参数 {参数名: 目标值}，人类配置预声明",
        )
        # Optional[float] 历史兼容 → 按 §1② 转空值不可表达，保留字段类型 Optional 但默认 None
        idle_extra_speed: Optional[float] = Field(default=None, description="Idle 额外参数速度（None=不额外调整）")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ):
        # 配置
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger(self.__class__.__name__)

        # 配置（typed；空 dict = 全默认；失败 log+raise）
        try:
            self.typed_config = self.ConfigSchema.from_dict(config)
        except Exception as e:
            self.logger.error(f"配置验证失败: {e}")
            raise

        self.vts_host: str = self.typed_config.vts_host
        self.vts_port: int = self.typed_config.vts_port
        self.lip_sync_enabled: bool = self.typed_config.lip_sync_enabled
        self.sample_rate: int = self.typed_config.sample_rate

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

        self._vts: Any = None
        self._vts_api_lock = asyncio.Lock()
        self._is_connecting = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._has_started = False

        self.render_count = 0
        self.error_count = 0

        # 子组件（消费侧 typed 化：self.typed_config.<field> 替代裸 config.get）
        self.lip_sync = LipSyncProcessor(
            logger_name=f"{self.__class__.__name__}.LipSync",
            sample_rate=self.sample_rate,
            volume_threshold=self.typed_config.volume_threshold,
            smoothing_factor=self.typed_config.smoothing_factor,
            vowel_detection_sensitivity=self.typed_config.vowel_detection_sensitivity,
            vts_set_parameter=self._expression_set_param_proxy,
            is_connected=lambda: self._is_connected,
            volume_gain=self.typed_config.volume_gain,
            max_mouth_open=self.typed_config.max_mouth_open,
            silence_threshold=self.typed_config.silence_threshold,
            close_mouth_threshold=self.typed_config.close_mouth_threshold,
            power_curve=self.typed_config.power_curve,
            vowel_open_weight=self.typed_config.vowel_open_weight,
            update_interval_ms=self.typed_config.update_interval_ms,
            mouth_open_lerp_speed=self.typed_config.mouth_open_lerp_speed,
            vowel_decay=self.typed_config.vowel_decay,
            min_mouth_delta=self.typed_config.min_mouth_delta,
            expression_rest_values={
                self.PARAM_MOUTH_SMILE: self.typed_config.base_smile,
                self.PARAM_EYE_OPEN_LEFT: 1.0,
                self.PARAM_EYE_OPEN_RIGHT: 1.0,
            },
        )
        self.hotkey_matcher = HotkeyMatcher(
            logger_name=f"{self.__class__.__name__}.Hotkey",
            is_connected=lambda: self._is_connected,
            vts_request=self._make_vts_request_proxy(),
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
            param_head_x=self.typed_config.idle_param_head_x,
            param_head_y=self.typed_config.idle_param_head_y,
            param_head_z=self.typed_config.idle_param_head_z,
            param_body_x=self.typed_config.idle_param_body_x,
            param_body_y=self.typed_config.idle_param_body_y,
            param_body_z=self.typed_config.idle_param_body_z,
            head_amplitude=self.typed_config.idle_head_amplitude,
            body_amplitude=self.typed_config.idle_body_amplitude,
            speed=self.typed_config.idle_speed,
            update_interval_ms=self.typed_config.idle_update_interval_ms,
            fade_speed=self.typed_config.idle_fade_speed,
            head_enabled=self.typed_config.idle_head_enabled,
            body_enabled=self.typed_config.idle_body_enabled,
            speech_pause_enabled=self.typed_config.idle_pause_while_speaking,
            extra_params=dict(self.typed_config.idle_extra_params),
            extra_speed=self.typed_config.idle_extra_speed,
        )
        self.idle_motion.set_baseline_params({self.PARAM_MOUTH_SMILE: self.typed_config.base_smile})

        self.idle_enabled_cfg = self.typed_config.idle_enabled

    # ===== ToolProvider 协议 =====

    @property
    def name(self) -> str:
        return self.PROVIDER_NAME

    def list_tools(self) -> List[ToolSpec]:
        """声明本 Provider 暴露的工具列表（热键描述按连接状态动态携带可用清单）"""
        return [
            ToolSpec(
                name="smile",
                description="设置 VTS MouthSmile 表情参数",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_SMILE_SCHEMA,
            ),
            ToolSpec(
                name="close_eyes",
                description="VTS 闭眼动作（EyeOpenLeft/Right=0）",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
            ToolSpec(
                name="open_eyes",
                description="VTS 睁眼动作（EyeOpenLeft/Right=1）",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
            ToolSpec(
                name="set_expression",
                description="VTS 批量设置表情参数（multi-parameter 写入）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="set_parameter_value",
                description="VTS 设置单个参数值",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_SET_PARAMETER_SCHEMA,
            ),
            ToolSpec(
                name="get_parameter_value",
                description="VTS 读取参数当前值",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_GET_PARAMETER_SCHEMA,
            ),
            ToolSpec(
                name="trigger_hotkey",
                description="VTS 触发热键（按热键名 name 优先，hotkey_id 兜底）" + self._hotkey_catalog_summary(),
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_TRIGGER_HOTKEY_SCHEMA,
            ),
            ToolSpec(
                name="load_item",
                description="VTS 加载道具（VTube Studio ItemLoadRequest）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_LOAD_ITEM_SCHEMA,
            ),
            ToolSpec(
                name="load_sticker",
                description="VTS 加载贴纸文件（file_name 为 VTS 可访问路径）",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
            ToolSpec(
                name="set_idle_enabled",
                description="VTS 启停 idle 拟人动画",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_SET_IDLE_SCHEMA,
            ),
            ToolSpec(
                name="reconnect",
                description="手动触发 VTS 重连循环",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
            ToolSpec(
                name="get_stats",
                description="读取 VTS 状态统计信息",
                kind="sync",
                provider=self.PROVIDER_NAME,
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
                    await self.trigger_hotkey(
                        name=str(args.get("name", "") or ""),
                        hotkey_id=str(args.get("hotkey_id", "") or ""),
                    ),
                )
            if invocation.tool_name == "vts_load_item":
                instance_id = await self.load_item(**{k: v for k, v in args.items() if k != ""})
                return _ok("vts_load_item", instance_id is not None, {"instance_id": instance_id})
            if invocation.tool_name == "vts_load_sticker":
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
        """Provider 生命周期入口"""
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

        self._has_started = True

    async def cleanup(self) -> None:
        """清理资源"""
        if not self._has_started:
            return

        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    # ===== 业务方法 =====

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

    def _hotkey_catalog_summary(self) -> str:
        """生成可用热键清单文本（拼入 vts_trigger_hotkey 描述，LLM 据此选名调用）。

        热键列表在 VTS 连接后由 ``HotkeyMatcher.load_hotkeys`` 加载；未连接 /
        未加载时返回空串（描述退化为不含清单的基础版）。
        """
        names = [str(hotkey.get("name", "")) for hotkey in self.hotkey_matcher.hotkey_list if hotkey.get("name")]
        if not names:
            return ""
        return f"。当前可用热键：{'、'.join(names)}"

    async def trigger_hotkey(self, name: str = "", hotkey_id: str = "") -> bool:
        """触发热键：按热键名解析（``find_by_name``）优先，``hotkey_id`` 兜底。

        LLM 只能从工具描述中拿到热键名（VTS 内部 hotkeyID 是不透明 UUID），
        因此调用侧以 name 为主入口；name 解析失败且有 id 时按 id 重试。
        """
        if name:
            resolved = self.hotkey_matcher.find_by_name(name)
            if resolved:
                return await self.hotkey_matcher.trigger_hotkey(resolved)
            if not hotkey_id:
                self.logger.warning(f"VTS 热键名未匹配且无 hotkey_id 兜底: {name}")
                return False
            self.logger.warning(f"VTS 热键名未匹配，回退 hotkey_id: name={name}, id={hotkey_id}")
        if hotkey_id:
            return await self.hotkey_matcher.trigger_hotkey(hotkey_id)
        self.logger.warning("VTS 触发热键失败：name 与 hotkey_id 均为空")
        return False

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
        }

    # ===== 内部辅助 =====

    async def _idle_set_param_proxy(self, parameter_name: str, value: float) -> bool:
        if not parameter_name:
            return False
        return await self.expression.set_parameter(parameter_name, value, weight=1, silent=True)

    async def _expression_set_param_proxy(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        return await self.expression.set_parameter(parameter_name, value, weight)

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

    async def connect(self) -> bool:
        """手动建立 VTS 连接（手动重连的"建立"半步）。

        直接委托 ``_connect``（内部已带 ``_is_connecting`` / ``_is_connected``
        短路，无需重复判重）。返回 bool 以 ``_is_connected`` 为准——即便
        ``_connect`` 异常被内部 try 吞掉，此处的真值与 VTS WebSocket 实际
        状态一致。手动 connect 与后台 ``_reconnect_loop`` 属低频可接受并发
        场景，不强制互斥。
        """
        self.logger.info("手动触发 VTS 连接")
        await self._connect()
        return self._is_connected

    async def disconnect(self) -> bool:
        """手动断开 VTS 连接（手动重连的"断开"半步）。

        复用 ``_disconnect`` 的关闭逻辑：停 idle → 取消后台重连循环 →
        关 VTS WebSocket → 置 ``_is_connected=False``。区别于 ``cleanup``
        的是**不重置** ``_has_started``——手动断开不破坏 setup 语义，后续
        仍可再 connect。后台 ``_reconnect_loop`` 被取消后手动重连场景下
        不自动恢复：调用方（``reconnect_provider``）在 connect 成功后下次
        ``_vts_health_check`` 仍能驱动恢复路径，不阻塞熔断器复位。返回
        True 表达"断开动作已完成"。
        """
        self.logger.info("手动断开 VTS 连接")
        await self._disconnect()
        return True


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
) -> VTSProvider:
    """构造 VTSProvider 实例（不启动，由调用方 setup）"""
    return VTSProvider(
        config=config,
        event_bus=event_bus,
    )


def register_vts_tools(
    registry: Any,
    config: Dict[str, Any],
    event_bus: Optional[EventBus] = None,
) -> VTSProvider:
    """构造 VTSProvider 并注册到 registry。返回 Provider 实例供调用方管理生命周期。"""
    provider = create_vts_provider(
        config=config,
        event_bus=event_bus,
    )
    registry.register_provider(provider)
    return provider
