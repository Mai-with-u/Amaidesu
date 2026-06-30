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

from typing import TYPE_CHECKING, Any, Coroutine, Dict, List, Optional

from pydantic import BaseModel, Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager
from src.modules.streaming.audio_stream_channel import AudioStreamChannel

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
        self._is_connecting = False
        self._vts_subscription_id: Optional[str] = None

        self.render_count = 0
        self.error_count = 0

        self.lip_sync = LipSyncProcessor(
            logger_name=f"{self.__class__.__name__}.LipSync",
            sample_rate=self.sample_rate,
            volume_threshold=self.typed_config.volume_threshold,
            smoothing_factor=self.typed_config.smoothing_factor,
            vowel_detection_sensitivity=self.typed_config.vowel_detection_sensitivity,
            vts_set_parameter=self._expression_set_param_proxy,
            is_connected=lambda: self._is_connected,
        )
        self.hotkey_matcher = HotkeyMatcher(
            logger_name=f"{self.__class__.__name__}.Hotkey",
            is_connected=lambda: self._is_connected,
            vts_request=self._vts_request_proxy,
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
            vts_request=self._vts_request_proxy,
        )

        self.logger.info("VTSHandler初始化完成")

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

    def _vts_request_proxy(self, request) -> Coroutine[Any, Any, Any]:
        return self._vts.request(request)

    async def _expression_set_param_proxy(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        return await self.expression.set_parameter(parameter_name, value, weight)

    async def init(self):
        await super().init()
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
            for param_name, value in params.get("expressions", {}).items():
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
            await self._vts.request_authentication_token()
            await self._vts.request_authentication()
            self._is_connected = True
            self.logger.info("VTS连接成功")

            await self.hotkey_matcher.load_hotkeys()

            if self.audio_stream_channel and not self._vts_subscription_id:
                self._vts_subscription_id = await self.audio_stream_channel.subscribe(
                    name="vts_lip_sync",
                    on_audio_start=self.lip_sync.on_start,
                    on_audio_chunk=self.lip_sync.on_chunk,
                    on_audio_end=self.lip_sync.on_end,
                )
                self.logger.info("VTS已订阅 AudioStreamChannel")
        except Exception as e:
            self.logger.error(f"VTS连接失败: {e}")
            self._is_connected = False
        finally:
            self._is_connecting = False

    async def _disconnect(self) -> None:
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
