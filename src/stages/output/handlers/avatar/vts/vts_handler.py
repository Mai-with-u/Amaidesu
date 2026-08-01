"""
VTS Handler - VTS虚拟形象渲染编排器

职责:
- 接收Intent并适配为VTS特定参数
- 通过组合的 3 个子组件执行业务逻辑：
  - LipSyncProcessor: 口型同步
  - HotkeyMatcher: 热键匹配（含 LLM 辅助）
  - ExpressionController: 表情控制
- VTS 连接生命周期管理
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

import asyncio

from pydantic import BaseModel, Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager
from src.modules.streaming.audio_stream_channel import AudioStreamChannel

from src.modules.avatar.idle_motion_controller import IdleMotionController
from src.stages.output.handlers.avatar.base import AvatarHandlerBase
from src.stages.output.handlers.avatar.vts.expression_controller import ExpressionController
from src.stages.output.handlers.avatar.vts.hotkey_matcher import HotkeyMatcher
from src.stages.output.handlers.avatar.vts.lip_sync_processor import LipSyncProcessor
from src.stages.output.registry import handler

if TYPE_CHECKING:
    from src.modules.streaming.audio_chunk import AudioChunk, AudioMetadata

    from src.modules.types import Intent

LLM_AVAILABLE = False
try:
    import openai

    LLM_AVAILABLE = True
except ImportError:
    pass


@handler("vts")
class VTSHandler(AvatarHandlerBase):
    """VTS Handler 编排器"""

    PARAM_MOUTH_SMILE = "MouthSmile"
    PARAM_MOUTH_OPEN = "MouthOpen"
    PARAM_EYE_OPEN_LEFT = "EyeOpenLeft"
    PARAM_EYE_OPEN_RIGHT = "EyeOpenRight"

    _EMOTION_KEYS = frozenset(
        {
            "happy",
            "surprised",
            "sad",
            "angry",
            "shy",
            "love",
            "excited",
            "confused",
            "scared",
            "neutral",
        }
    )
    _ACTION_KEYS = frozenset({"blink", "nod", "shake", "wave", "clap", "motion"})

    # VTS 断线自动重连间隔（秒）：覆盖 Amaidesu 先于 VTS 启动、VTS 中途重启两种场景
    _RECONNECT_INTERVAL_S = 5.0

    class _VTSActionParams(BaseModel):  # type: ignore[name-defined]  # noqa: F821
        duration_ms: int = Field(default=1500, ge=100, le=10000)

    _ACTION_PARAMS_SCHEMA: dict[str, type] = {
        "blink": _VTSActionParams,
        "nod": _VTSActionParams,
        "shake": _VTSActionParams,
        "wave": _VTSActionParams,
        "clap": _VTSActionParams,
        "motion": _VTSActionParams,
    }

    # idle 动画参数名回退表：当配置名在 VTS 中不可用时，按此顺序尝试常见参数名。
    # 若仍不匹配，会打印可用参数名提示用户手动配置。
    _IDLE_PARAM_FALLBACKS: dict[str, tuple[str, ...]] = {
        "head_x": ("HeadAngleX", "HeadX", "FaceAngleX", "FaceX", "NeckAngleX"),
        "head_y": ("HeadAngleY", "HeadY", "FaceAngleY", "FaceY", "NeckAngleY"),
        "head_z": ("HeadAngleZ", "HeadZ", "FaceAngleZ", "FaceZ", "NeckAngleZ"),
        "body_x": ("BodyAngleX", "BodyX", "BodyRotationX", "TorsoAngleX", "BodyPositionX"),
        "body_y": ("BodyAngleY", "BodyY", "BodyRotationY", "TorsoAngleY", "BodyPositionY"),
        "body_z": ("BodyAngleZ", "BodyZ", "BodyRotationZ", "TorsoAngleZ", "BodyPositionZ"),
    }

    def get_capabilities(self):
        from src.modules.types.capabilities import (
            ActionSpec,
            HandlerCapabilities,
            _pydantic_to_param_spec,
        )

        actions = [
            ActionSpec(
                name=local,
                description=f"VTS {local} action",
                parameters=_pydantic_to_param_spec(cls),
            )
            for local, cls in self._ACTION_PARAMS_SCHEMA.items()
        ]
        return HandlerCapabilities(actions=actions)

    class ConfigSchema(BaseConfig):
        type: str = "vts"

        vts_host: str = Field(default="localhost", description="VTS WebSocket主机地址")
        vts_port: int = Field(default=8001, ge=1, le=65535, description="VTS WebSocket端口")

        llm_matching_enabled: bool = Field(default=False, description="是否启用LLM智能热键匹配")
        llm_api_key: Optional[str] = Field(default=None, description="LLM API密钥")
        llm_base_url: Optional[str] = Field(default=None, description="LLM API地址")
        llm_model: str = Field(default="gpt-4o-mini", description="LLM模型")
        llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="LLM温度")
        llm_max_tokens: int = Field(default=50, ge=1, le=200, description="LLM最大token数")

        lip_sync_enabled: bool = Field(default=True, description="是否启用口型同步")
        volume_threshold: float = Field(default=0.01, ge=0.0, le=1.0, description="音量阈值")
        smoothing_factor: float = Field(default=0.3, ge=0.0, le=1.0, description="平滑因子")
        vowel_detection_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0, description="元音检测灵敏度")
        sample_rate: int = Field(default=16000, ge=8000, le=48000, description="音频采样率")

        # 口型自然度调参
        volume_gain: float = Field(default=1.0, ge=0.1, le=3.0, description="音量增益（越大嘴张得越大）")
        max_mouth_open: float = Field(default=0.6, ge=0.1, le=1.0, description="最大张嘴幅度上限，嘴型值域为 0~此值")
        silence_threshold: float = Field(default=0.02, ge=0.0, le=1.0, description="静音阈值，低于此值直接闭嘴")
        close_mouth_threshold: float = Field(
            default=0.06, ge=0.0, le=1.0, description="低音量阈值，低于此值大幅衰减嘴型"
        )
        power_curve: float = Field(
            default=1.0, ge=0.1, le=2.0, description="音量到嘴型的幂曲线（1.0=线性，>1 让嘴型更多分布在中低区间）"
        )
        vowel_open_weight: float = Field(default=0.5, ge=0.0, le=2.0, description="元音张嘴权重，降低可减少突跳")
        update_interval_ms: float = Field(
            default=30.0, ge=10.0, le=200.0, description="VTS 口型参数最小更新间隔（毫秒）"
        )
        mouth_open_lerp_speed: float = Field(
            default=0.35, ge=0.05, le=1.0, description="嘴型向目标值过渡速度，越小越平滑"
        )
        vowel_decay: float = Field(default=0.4, ge=0.05, le=0.95, description="元音衰减速度，越小元音嘴型越连续")
        min_mouth_delta: float = Field(default=0.005, ge=0.001, le=0.1, description="嘴型最小变化阈值，越小更新越连续")
        base_smile: float = Field(
            default=0.3,
            ge=0.0,
            le=1.0,
            description="常驻微笑基线值：平时与说完话后保持的 MouthSmile；该模型的 EyeSmile 也由 MouthSmile 驱动，可让眼睛保持笑意",
        )

        # 默认 idle 动画配置（不说话时轻微摇头晃脑 + 身体移动）
        idle_enabled: bool = Field(default=True, description="是否启用默认 idle 拟人动画")
        idle_param_head_x: str = Field(default="HeadAngleX", description="头部左右摇头参数名")
        idle_param_head_y: str = Field(default="HeadAngleY", description="头部上下点头参数名")
        idle_param_head_z: str = Field(default="HeadAngleZ", description="头部倾斜/歪头参数名")
        idle_param_body_x: str = Field(default="BodyX", description="身体左右移动参数名")
        idle_param_body_y: str = Field(default="BodyY", description="身体上下移动参数名")
        idle_param_body_z: str = Field(default="BodyZ", description="身体前后/深度移动参数名")
        idle_head_amplitude: float = Field(
            default=0.05, ge=0.0, le=30.0, description="头部晃动幅度（具体值域取决于模型 tracking 参数值域）"
        )
        idle_body_amplitude: float = Field(
            default=0.02, ge=0.0, le=30.0, description="身体移动幅度（具体值域取决于模型 tracking 参数值域）"
        )
        idle_speed: float = Field(default=1.0, ge=0.1, le=5.0, description="idle 动画速度倍率")
        idle_update_interval_ms: float = Field(default=40.0, ge=10.0, le=200.0, description="idle 参数更新间隔（毫秒）")
        idle_fade_speed: float = Field(default=0.15, ge=0.01, le=1.0, description="说话/静默切换时 idle 平滑过渡速度")
        idle_head_enabled: bool = Field(default=True, description="是否启用 idle 头部晃动")
        idle_body_enabled: bool = Field(default=True, description="是否启用 idle 身体移动")
        idle_pause_while_speaking: bool = Field(
            default=False,
            description="说话时是否暂停 idle 动画（默认 False，即始终运行）",
        )
        idle_extra_params: Dict[str, float] = Field(
            default_factory=dict,
            description="额外 idle 摆动参数（参数名 -> 幅度），如自定义袖子参数 SleeveRX/SleeveRY/SleeveLX/SleeveLY",
        )
        idle_extra_speed: Optional[float] = Field(
            default=None,
            ge=0.1,
            le=5.0,
            description="额外摆动参数（袖子等）的速度倍率；留空则跟随 idle_speed",
        )

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        audio_stream_channel: Optional[AudioStreamChannel] = None,
        prompt_service: Optional[PromptManager] = None,
    ):
        super().__init__(config, event_bus, audio_stream_channel)
        self.logger = get_logger(self.__class__.__name__)
        self._prompt_service = prompt_service

        self.typed_config = self.ConfigSchema.from_dict(config)
        self.vts_host = self.typed_config.vts_host
        self.vts_port = self.typed_config.vts_port
        self.lip_sync_enabled = self.typed_config.lip_sync_enabled
        self.sample_rate = self.typed_config.sample_rate

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

        self._vts = None
        self._vts_api_lock = asyncio.Lock()
        self._is_connecting = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._vts_subscription_id: Optional[str] = None

        self.render_count = 0
        self.error_count = 0
        self._current_intent_expressions: Dict[str, float] = {}

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
            prompt_service=self._prompt_service,
            openai_client=self._build_openai_client(),
            llm_model=self.typed_config.llm_model,
            llm_temperature=self.typed_config.llm_temperature,
            llm_max_tokens=self.typed_config.llm_max_tokens,
            llm_matching_enabled=self.typed_config.llm_matching_enabled,
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
            extra_params=self.typed_config.idle_extra_params,
            extra_speed=self.typed_config.idle_extra_speed,
        )
        # 常驻微笑基线交给 idle 每 tick 维持，防止被外部覆盖（该模型 EyeSmile 也由 MouthSmile 驱动）
        self.idle_motion.set_baseline_params({self.PARAM_MOUTH_SMILE: self.typed_config.base_smile})

        self.logger.info("VTSHandler初始化完成")

    async def _idle_set_param_proxy(self, parameter_name: str, value: float) -> bool:
        """idle 动画参数设置代理（静默写入，失败不刷屏）"""
        if not parameter_name:
            return False
        return await self.expression.set_parameter(parameter_name, value, weight=1, silent=True)

    async def _resolve_idle_parameter_names(self) -> dict[str, str]:
        """根据 VTS 当前可用参数列表，为 idle 每个轴挑选可用参数名。

        优先使用用户在配置中指定的名称；若不存在则按回退表尝试常见参数名；
        若仍无匹配，会打印可用参数名并保留原配置名，便于用户排查。
        """
        available = set(await self.expression.list_tracking_parameters())
        config_names = {
            "head_x": self.typed_config.idle_param_head_x,
            "head_y": self.typed_config.idle_param_head_y,
            "head_z": self.typed_config.idle_param_head_z,
            "body_x": self.typed_config.idle_param_body_x,
            "body_y": self.typed_config.idle_param_body_y,
            "body_z": self.typed_config.idle_param_body_z,
        }
        if not available:
            self.logger.warning(
                "无法获取 VTS 参数列表，idle 动画将使用配置中的参数名；"
                "若模型无晃动，请运行 list_vts_params.py 查看可用参数名后修改 [handlers.vts].idle_* 配置。"
            )
            return config_names

        resolved: dict[str, str] = {}
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
                    f"回退表 {candidates} 均不可用）。"
                    f"当前可用参数示例：{available_sample}。"
                    f"请在 config/output.toml [handlers.vts] 中把 idle_param_{axis} 改为实际可用参数名。"
                )
        return resolved

    def _build_openai_client(self) -> Optional[Any]:
        if not (self.typed_config.llm_matching_enabled and LLM_AVAILABLE and self.typed_config.llm_api_key):
            return None
        try:
            client = openai.AsyncOpenAI(
                api_key=self.typed_config.llm_api_key,
                base_url=self.typed_config.llm_base_url if self.typed_config.llm_base_url else None,
            )
            self.logger.info("LLM客户端初始化成功")
            return client
        except Exception as e:
            self.logger.warning(f"LLM客户端初始化失败: {e}")
            return None

    def _make_vts_request_proxy(self):
        """创建一个可调用代理，既支持发送 request，又暴露 pyvts 的 request 构造方法。

        所有 VTS API 调用都经过同一把 asyncio.Lock 串行化：pyvts 的 websocket
        连接不支持并发 recv，idle 动画（25Hz）与口型同步（33Hz）同时写入时会
        抛出 "cannot call recv while another coroutine is already running recv"。
        """
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

    async def _expression_set_param_proxy(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        return await self.expression.set_parameter(parameter_name, value, weight)

    async def init(self):
        try:
            import pyvts  # noqa: F401
            from pyvts import vts

            plugin_info = {
                "plugin_name": "Amaidesu_VTS_OutputProvider",
                "developer": "Phase 4 Implementation",
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
            self.logger.info("pyvts实例创建成功")
        except ImportError:
            self.logger.error("pyvts库不可用，VTSHandler将被禁用")
            self._vts = None
            raise ImportError("pyvts library not available") from None

        # 必须先创建 _vts 再调 super().init()，因为 base 的 init 内部会调用 _connect()
        await super().init()

        # 启动断线自动重连循环（VTS 后启动/中途重启都能自动恢复）
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._reconnect_task.set_name(f"{self.__class__.__name__}.reconnect_loop")

        if self.event_bus and not getattr(self, "_sticker_subscribed", False):
            from src.modules.events.payloads import StickerCommandPayload

            self.event_bus.on(
                CoreEvents.OUTPUT_STICKER_COMMAND,
                self._on_sticker_command,
                StickerCommandPayload,
            )
            self._sticker_subscribed = True

    async def _adapt_intent(self, intent: "Intent") -> Optional[Dict[str, Any]]:
        result: Dict[str, Any] = {"expressions": {}, "hotkeys": []}

        if intent.emotion is not None:
            emotion_str = intent.emotion.name
            if emotion_str in self._emotion_map:
                scale = float(intent.emotion.intensity)
                base = self._emotion_map[emotion_str]
                result["expressions"] = {k: v * scale for k, v in base.items()}
                self.logger.debug(f"情感映射: {emotion_str} (intensity={scale}) -> {result['expressions']}")

        if intent.action is not None:
            local_name = intent.action.name.split(".", 1)[-1]
            schema_cls = self._ACTION_PARAMS_SCHEMA.get(local_name)
            if schema_cls is None:
                self.logger.debug(f"action '{local_name}' 不在 vts _ACTION_PARAMS_SCHEMA 中,跳过")
                return None
            try:
                schema_cls.model_validate(intent.action.parameters or {})
            except Exception as e:
                self.logger.warning(f"vts action '{local_name}' 参数校验失败: {e}")
                return None

            if local_name in self._action_hotkey_map:
                result["hotkeys"].append(self._action_hotkey_map[local_name])
            else:
                return None

        # 把当前 emotion 对应的基础表情同步给 LipSync，让它在说话时保持、结束后淡出
        self._current_intent_expressions = dict(result["expressions"])
        self.lip_sync.set_base_expressions(self._current_intent_expressions)
        # 同步给 idle：Intent 占用的表情参数不写入常驻基线，避免互相覆盖
        self.idle_motion.set_baseline_overrides(self._current_intent_expressions)

        self.logger.debug(f"Intent适配结果: expressions={result['expressions']}, hotkeys={result['hotkeys']}")
        return result

    async def _on_sticker_command(self, event_name: str, payload: Any, source: str) -> None:
        if payload.target_handler != "vts":
            return
        self.logger.info(f"收到贴纸触发: sticker_id={payload.sticker_id}")
        if not payload.image_base64:
            # TODO: 当前 Intent 结构不含图片字段，StickerHandler 无法传递 image_base64。
            #       待 Decider/Intent 扩展图片字段后，此处调用 load_item 完成贴纸渲染。
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

    async def _render_to_platform(self, params: Dict[str, Any]) -> None:
        try:
            # 表情参数由 LipSync 在说话期间统一维持/淡出，避免重复设置造成冲突。
            # 但 LipSync 只会在音频流开始后接管；如果当前没有音频流，仍然直接设置一次。
            has_audio_stream = self.audio_stream_channel is not None
            for param_name, value in params.get("expressions", {}).items():
                if has_audio_stream and self.lip_sync.is_speaking:
                    continue
                await self.expression.set_parameter(param_name, float(value), weight=1)
            for hotkey in params.get("hotkeys", []):
                await self.hotkey_matcher.trigger_hotkey(hotkey)
        except Exception as e:
            self.logger.error(f"渲染到VTS失败: {e}")
            self.error_count += 1
            return

        self.render_count += 1
        self.logger.debug(f"VTS渲染成功: render_count={self.render_count}")

    async def _connect(self) -> None:
        if self._is_connecting or self._is_connected:
            return
        self._is_connecting = True
        try:
            if not self._vts:
                self.logger.error("pyvts 未初始化")
                return

            self.logger.info(f"开始连接VTS: {self.vts_host}:{self.vts_port}")
            await self._vts.connect()
            await self._vts.request_authenticate_token()
            await self._vts.request_authenticate()
            self._is_connected = True
            self.logger.info("VTS连接成功")

            await self.hotkey_matcher.load_hotkeys()

            # 连接成功后解析 VTS 可用参数名，把 idle 参数回退到实际存在的参数
            resolved = await self._resolve_idle_parameter_names()
            self.idle_motion.set_parameter_names(
                param_head_x=resolved.get("head_x"),
                param_head_y=resolved.get("head_y"),
                param_head_z=resolved.get("head_z"),
                param_body_x=resolved.get("body_x"),
                param_body_y=resolved.get("body_y"),
                param_body_z=resolved.get("body_z"),
            )

            if self.typed_config.idle_enabled:
                try:
                    self.idle_motion.start()
                    self.logger.info("VTS idle 动画已启动")
                except Exception as e:
                    self.logger.error(f"启动 idle 动画失败: {e}")

            # 应用常驻微笑基线（该模型 MouthSmile 同时驱动 EyeSmile，让眼睛保持笑意）
            try:
                await self.expression.set_parameter(
                    self.PARAM_MOUTH_SMILE, self.typed_config.base_smile, weight=1, silent=True
                )
            except Exception as e:
                self.logger.warning(f"应用常驻微笑基线失败: {e}")

            if self.audio_stream_channel and not self._vts_subscription_id:
                from src.modules.streaming.backpressure import SubscriberConfig

                self._vts_subscription_id = await self.audio_stream_channel.subscribe(
                    name="vts_lip_sync",
                    on_audio_start=self.lip_sync.on_start,
                    on_audio_chunk=self.lip_sync.on_chunk,
                    on_audio_end=self.lip_sync.on_end,
                    config=SubscriberConfig(queue_size=500, backpressure_strategy="drop_oldest"),
                )
                self.logger.info("VTS已订阅 AudioStreamChannel")
        except Exception as e:
            self.logger.error(f"VTS连接失败: {e}")
            self._is_connected = False
        finally:
            self._is_connecting = False

    async def _vts_health_check(self) -> bool:
        """轻量健康检查：连接可能已随 VTS 重启而静默断开"""
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
        """VTS 断线自动重连：未连接时定期重试；已连接时定期健康检查，断开则重连。"""
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
        # 先停自动重连，避免清理过程中又连上
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        # 先停止 idle 动画，避免在断连期间还尝试写参数
        try:
            await self.idle_motion.stop()
        except Exception as e:
            self.logger.warning(f"停止 idle 动画失败: {e}")

        if self._vts_subscription_id and self.audio_stream_channel:
            try:
                await self.audio_stream_channel.unsubscribe(self._vts_subscription_id)
            except Exception as e:
                self.logger.error(f"取消 AudioStreamChannel 订阅失败: {e}")
            finally:
                self._vts_subscription_id = None

        if not self._is_connected or not self._vts:
            return
        try:
            await self._vts.close()
            self.logger.info("VTS连接已关闭")
        except Exception as e:
            self.logger.warning(f"关闭VTS连接异常: {e}")
        finally:
            self._is_connected = False

    async def trigger_hotkey(self, hotkey_id: str) -> bool:
        return await self.hotkey_matcher.trigger_hotkey(hotkey_id)

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
            self.logger.warning("VTS未连接，无法加载道具")
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
        self, item_instance_id_list: Optional[List[str]] = None, file_name_list: Optional[List[str]] = None
    ) -> bool:
        if not self._is_connected:
            self.logger.warning("VTS未连接，无法卸载道具")
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

    async def _on_lip_sync_start(self, metadata: "AudioMetadata"):
        await self.lip_sync.on_start(metadata)

    async def _on_lip_sync_chunk(self, chunk: "AudioChunk"):
        await self.lip_sync.on_chunk(chunk)

    async def _on_lip_sync_end(self, metadata: "AudioMetadata"):
        await self.lip_sync.on_end(metadata)

    async def start_lip_sync_session(self, text: str = ""):
        await self.lip_sync.start_session(text)

    async def process_tts_audio(self, audio_data: bytes, sample_rate: int = 32000):
        await self.lip_sync.process_audio(audio_data, sample_rate)

    async def stop_lip_sync_session(self):
        await self.lip_sync.stop_session()

    async def _find_best_matching_hotkey_with_llm(self, text: str) -> Optional[str]:
        return await self.hotkey_matcher.find_best_match_with_llm(text)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "hotkey_count": len(self.hotkey_matcher.hotkey_list),
            "lip_sync_enabled": self.lip_sync_enabled,
            "llm_matching_enabled": self.typed_config.llm_matching_enabled,
        }
