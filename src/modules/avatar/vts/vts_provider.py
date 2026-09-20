"""
VTSProvider - VTS 虚拟形象工具集

ToolProvider 协议实现，把 VTS 能力封装为工具（LLM 主动半）：

- 引擎子件（``LipSyncProcessor`` / ``ExpressionController`` / ``HotkeyMatcher``
  / ``IdleMotionController``）经 callback 解耦，可独立复用。
- 暴露的工具（同语义跨后端同名同参数形状，契约见 ``avatar.protocol``）：
  - ``vts_set_expression``        - 设置情绪（17 枚举值 + 强度）
  - ``vts_list_preset_actions``   - 列出可演预设（VTS 热键目录）
  - ``vts_trigger_preset_action`` - 触发预设动作（未知名随结果返回目录）
  - ``vts_set_idle_enabled``      - 启停 idle 拟人动画

被收敛的历史工具（微旋钮 / 运维件 / 不可发现件）的 Python 方法保留供
内部机件调用，仅撤 LLM 工具注册。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import Field

from src.modules.avatar.speech_binding import bind_speech_emotion, bind_speaking_state
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider
from src.modules.types.emotion_vocab import Emotion

from .expression_controller import ExpressionController
from .hotkey_matcher import HotkeyMatcher
from .idle_motion_controller import IdleMotionController
from .lip_sync_processor import LipSyncProcessor

if TYPE_CHECKING:
    pass


# =============================================================================
# 工具的 JSON Schema 描述
# =============================================================================

_VTS_SET_EXPRESSION_SCHEMA: Dict[str, Any] = {
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

_VTS_TRIGGER_PRESET_ACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "预设动作名（取自 vts_list_preset_actions 返回的目录）",
        },
    },
    "required": ["action"],
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

        TOML 段位：[tools.avatar.vts].config

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
        # Optional[float] 历史兼容 →；空值不可用空串表达，保留字段类型 Optional 但默认 None
        idle_extra_speed: Optional[float] = Field(default=None, description="Idle 额外参数速度（None=不额外调整）")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: Optional[EventBus] = None,
    ) -> None:
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

        # 情绪 → VTS 参数映射（词表 17 值全覆盖；键取 Emotion.value 小写）。
        # VTS 可驱动的面部参数用足（嘴/眼/眉/脸颊/舌头/水平嘴/FaceAngry），
        # 各值落在不同的参数组合上；强度在 set_expression 中按线性缩放施加。
        self._emotion_map: Dict[str, Dict[str, float]] = {
            "neutral": {},
            "happy": {"MouthSmile": 0.8, "BrowLeftY": 0.6, "BrowRightY": 0.6},
            "sad": {"MouthSmile": -0.4, "BrowLeftY": 0.15, "BrowRightY": 0.15, "EyeOpenLeft": 0.6, "EyeOpenRight": 0.6},
            "angry": {"MouthSmile": -0.6, "FaceAngry": 0.9, "BrowLeftY": 0.2, "BrowRightY": 0.2, "MouthOpen": 0.1},
            "surprised": {
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
                "MouthOpen": 0.5,
                "BrowLeftY": 1.0,
                "BrowRightY": 1.0,
            },
            "scared": {
                "EyeOpenLeft": 0.7,
                "EyeOpenRight": 0.7,
                "MouthOpen": 0.4,
                "BrowLeftY": 0.9,
                "BrowRightY": 0.5,
                "FaceAngry": -0.4,
            },
            "disgusted": {
                "MouthX": -0.3,
                "TongueOut": 0.2,
                "FaceAngry": 0.4,
                "EyeOpenLeft": 0.5,
                "EyeOpenRight": 0.5,
                "CheekPuff": 0.2,
            },
            "shy": {"MouthSmile": 0.35, "EyeOpenLeft": 0.55, "EyeOpenRight": 0.55, "CheekPuff": 0.15},
            "embarrassed": {"MouthSmile": 0.2, "MouthX": 0.25, "EyeOpenLeft": 0.5, "EyeOpenRight": 0.65},
            "confused": {
                "EyeOpenLeft": 0.75,
                "EyeOpenRight": 0.95,
                "BrowLeftY": 0.95,
                "BrowRightY": 0.4,
                "MouthX": 0.15,
            },
            "love": {"MouthSmile": 0.9, "EyeOpenLeft": 0.35, "EyeOpenRight": 0.35, "CheekPuff": 0.2},
            "excited": {
                "MouthSmile": 1.0,
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
                "MouthOpen": 0.4,
                "BrowLeftY": 0.85,
                "BrowRightY": 0.85,
            },
            "smug": {"MouthSmile": 0.5, "EyeOpenLeft": 0.3, "EyeOpenRight": 0.45, "BrowLeftY": 0.7, "BrowRightY": 0.3},
            "serious": {
                "MouthSmile": -0.1,
                "BrowLeftY": 0.15,
                "BrowRightY": 0.15,
                "EyeOpenLeft": 0.85,
                "EyeOpenRight": 0.85,
                "FaceAngry": 0.25,
            },
            "tired": {"EyeOpenLeft": 0.3, "EyeOpenRight": 0.25, "MouthOpen": 0.12, "BrowLeftY": 0.1, "BrowRightY": 0.1},
            "crying": {
                "EyeOpenLeft": 0.2,
                "EyeOpenRight": 0.2,
                "MouthOpen": 0.35,
                "MouthSmile": -0.5,
                "BrowLeftY": 0.1,
                "BrowRightY": 0.1,
            },
            "speechless": {
                "MouthSmile": 0.0,
                "MouthOpen": 0.06,
                "EyeOpenLeft": 0.8,
                "EyeOpenRight": 0.8,
                "BrowLeftY": 0.3,
                "BrowRightY": 0.3,
            },
        }

        self._vts: Any = None
        self._vts_api_lock = asyncio.Lock()
        self._is_connecting = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._has_started = False
        # idle 归一化值 → 参数原生量纲的缩放表（连接后按 VTS 实际范围构建；未知 = 1.0）
        self._idle_param_scale: Dict[str, float] = {}
        # 说话状态（tts.utterance.started/finished 订阅驱动；idle 据此暂停摇摆）
        self._is_speaking: bool = False
        # 事件订阅句柄（setup 时绑定，cleanup 时退订）
        self._speech_emotion_handler: Optional[Any] = None
        self._speaking_state_handles: Optional[Any] = None

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
            # 说话状态由 tts.utterance.* 订阅驱动（setup 时绑定），不在口型件里兼任
            is_speaking=lambda: self._is_speaking,
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
        """声明本 Provider 暴露的工具列表（预设目录走 list_preset_actions 结果，不进描述）"""
        return [
            ToolSpec(
                name="set_expression",
                description="VTS 设置主播当前情绪（17 枚举值 + 强度，持续生效直至下次设置）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_SET_EXPRESSION_SCHEMA,
            ),
            ToolSpec(
                name="list_preset_actions",
                description="列出 VTS 可演的预设动作目录（连接后的热键清单；贴纸/道具已绑成热键的也在列）",
                kind="sync",
                provider=self.PROVIDER_NAME,
            ),
            ToolSpec(
                name="trigger_preset_action",
                description="触发一个预设动作（动作名取自 vts_list_preset_actions 返回的目录）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_TRIGGER_PRESET_ACTION_SCHEMA,
            ),
            ToolSpec(
                name="set_idle_enabled",
                description="VTS 启停 idle 拟人动画（呼吸/摆头/身体摇摆）",
                kind="sync",
                provider=self.PROVIDER_NAME,
                parameters_schema=_VTS_SET_IDLE_SCHEMA,
            ),
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """ToolProvider.invoke：分发到对应的 handler 方法。"""
        args = invocation.arguments or {}
        try:
            if invocation.tool_name == "vts_set_expression":
                return await self.set_expression(
                    str(args.get("emotion", "")),
                    float(args.get("intensity", 0.5)),
                )
            if invocation.tool_name == "vts_list_preset_actions":
                return await self.list_preset_actions()
            if invocation.tool_name == "vts_trigger_preset_action":
                return await self.trigger_preset_action(str(args.get("action", "")))
            if invocation.tool_name == "vts_set_idle_enabled":
                self._set_idle_enabled(bool(args["enabled"]))
                return _ok("vts_set_idle_enabled", True)
            return _fail(
                invocation.tool_name,
                f"工具 '{invocation.tool_name}' 不属于 Provider '{self.PROVIDER_NAME}'",
            )
        except Exception as exc:  # noqa: BLE001 — Provider 边界兜底
            self.logger.exception(f"VTS 工具 {invocation.tool_name} 调用异常: {exc}")
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
                # token 与其他运行时数据统一落 data/（该目录已整目录不入库）
                "authentication_token_path": "data/vts_token.txt",
                "vts_host": self.vts_host,
                "vts_port": self.vts_port,
            }
            vts_api_info = {
                "host": self.vts_host,
                "port": self.vts_port,
                "name": "VTubeStudioPublicAPI",
                "version": "1.0",
            }
            # 形参名必须是 plugin_info：pyvts 以 **kwargs 吞掉拼错的键并静默退回
            # 库默认（曾致插件身份显示为 pyvts/genteki、token 落盘根目录）
            self._vts = vts(plugin_info=plugin_info, vts_api_info=vts_api_info)
            self.logger.info("pyvts 实例创建成功")
        except ImportError:
            self.logger.error("pyvts 库不可用，VTSProvider 将被禁用")
            self._vts = None
            raise ImportError("pyvts library not available") from None

        await self._connect()

        # 启动断线自动重连循环
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._reconnect_task.set_name(f"{self.__class__.__name__}.reconnect_loop")

        # 被动半事件订阅：情绪反射（streamer.speech）+ 说话状态（tts.utterance.*，
        # 驱动 idle"说话时暂停摇摆"）
        self._speech_emotion_handler = bind_speech_emotion(self.event_bus, self, self.logger)
        self._speaking_state_handles = bind_speaking_state(self.event_bus, self.logger, on_change=self._set_speaking)

        self._has_started = True

    async def cleanup(self) -> None:
        """清理资源"""
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

        await self._disconnect()
        self._has_started = False
        self.logger.info(f"{self.__class__.__name__} 已停止")

    def _set_speaking(self, speaking: bool) -> None:
        """说话状态回调（bind_speaking_state 驱动；idle 据此暂停摇摆）。"""
        self._is_speaking = speaking

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

    # ===== 契约方法（LLM 工具与自动情绪路径共用的渲染入口）=====

    async def set_expression(self, emotion: str, intensity: float) -> ToolExecutionResult:
        """设置当前情绪：查映射表 → 按强度线性缩放 → 批量写入 VTS 参数。

        强度语义：0.0 收回全部表情参数，1.0 为映射表完整幅度，线性内插；
        ``neutral`` 映射为空参数表（表情交回 idle/基线渲染）。映射表外的
        情绪名按失败结果返回（词表编译期已知，出现未知名即调用方契约破坏）。
        """
        params = self._emotion_map.get(emotion)
        if params is None:
            return _fail("vts_set_expression", f"未知情绪 '{emotion}'（应为 17 枚举值之一）")
        factor = min(1.0, max(0.0, float(intensity)))
        scaled = {name: value * factor for name, value in params.items()}
        success = await self.expression.set_multi_parameter(scaled, 1.0)
        return _ok(
            "vts_set_expression",
            bool(success),
            {"emotion": emotion, "intensity": factor, "parameters": scaled},
        )

    async def list_preset_actions(self) -> ToolExecutionResult:
        """列出可演预设目录（VTS 热键清单；未连接/未加载时为空列表）。

        贴纸/道具在 VTS 内绑成热键后自然出现在目录中（``load_sticker``
        的承载方式），不再单列工具。
        """
        actions = [
            {"name": str(hotkey.get("name", "")), "type": str(hotkey.get("type", ""))}
            for hotkey in self.hotkey_matcher.hotkey_list
            if hotkey.get("name")
        ]
        return _ok("vts_list_preset_actions", True, {"actions": actions})

    async def trigger_preset_action(self, action: str) -> ToolExecutionResult:
        """触发一个预设动作（按名解析热键）；未知名把目录随失败结果返回。"""
        action = action.strip()
        resolved = self.hotkey_matcher.find_by_name(action) if action else None
        if not resolved:
            catalog = [a["name"] for a in (await self.list_preset_actions()).structured_content["actions"]]
            return ToolExecutionResult(
                tool_name="vts_trigger_preset_action",
                success=False,
                error_message=f"未知预设动作 '{action}'",
                structured_content={"available_actions": catalog},
                content=str(catalog),
            )
        success = await self.hotkey_matcher.trigger_hotkey(resolved)
        return _ok("vts_trigger_preset_action", bool(success), {"action": action})

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
            self.logger.exception(f"加载道具失败: {e}")
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
        scaled = value * self._idle_param_scale.get(parameter_name, 1.0)
        return await self.expression.set_parameter(parameter_name, scaled, weight=1, silent=True)

    @staticmethod
    def _idle_scale_for(ranges: Dict[str, tuple[float, float]], names: List[str]) -> Dict[str, float]:
        """按参数原生范围计算 idle 归一化值的缩放系数（范围未知 = 原值 1.0）。

        idle 控制器产出零中心归一化值（约 ±1 × 幅度配置），注入须落在参数
        原生量纲内：取 ``max(|min|, |max|)`` 为半幅放大（FaceAngleX ±30 →
        ±0.05 变 ±1.5°）；[0,1]/[-1,1] 类参数半幅 ≤ 1，行为不变。
        """
        scales: Dict[str, float] = {}
        for name in names:
            if not name:
                continue
            bounds = ranges.get(name)
            if bounds is None:
                continue
            half_span = max(abs(bounds[0]), abs(bounds[1]))
            scales[name] = half_span if half_span > 0 else 1.0
        return scales

    async def _refresh_idle_param_scale(self, resolved: Dict[str, str]) -> None:
        """拉取 VTS 参数原生范围并构建 idle 写入缩放表（失败降级为全原值）。"""
        try:
            ranges = await self.expression.list_parameter_ranges()
        except Exception as e:
            self.logger.warning(f"拉取 VTS 参数范围失败，idle 按原值写入: {e}")
            self._idle_param_scale = {}
            return
        if not ranges:
            self.logger.warning("VTS 未返回参数范围，idle 按原值写入")
            self._idle_param_scale = {}
            return
        names = [
            resolved.get("head_x"),
            resolved.get("head_y"),
            resolved.get("head_z"),
            resolved.get("body_x"),
            resolved.get("body_y"),
            resolved.get("body_z"),
            self.PARAM_MOUTH_SMILE,
            *self.typed_config.idle_extra_params.keys(),
        ]
        self._idle_param_scale = self._idle_scale_for(ranges, [n for n in names if n])
        scaled = {n: s for n, s in self._idle_param_scale.items() if s != 1.0}
        self.logger.info(f"idle 参数量纲缩放表: {scaled or '全部 1.0（无角度类参数）'}")

    async def _expression_set_param_proxy(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        return await self.expression.set_parameter(parameter_name, value, weight)

    def _make_vts_request_proxy(self) -> Any:
        """创建可调用代理，所有 VTS API 调用都经过同一把 asyncio.Lock 串行化"""
        import pyvts

        handler = self
        vts_request_builder = pyvts.VTSRequest()

        class VTSRequestProxy:
            async def __call__(self, request: Dict[str, Any]) -> Dict[str, Any]:
                async with handler._vts_api_lock:
                    return await handler._vts.request(request)

            @property
            def vts_request(self) -> Any:
                return vts_request_builder

            def requestHotKeyList(self) -> Dict[str, Any]:
                return vts_request_builder.requestHotKeyList()

            def requestTriggerHotKey(self, **kwargs: Any) -> Dict[str, Any]:
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
            await self._refresh_idle_param_scale(resolved)

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
            self.logger.exception(f"VTS 自动重连循环异常: {e}")

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
