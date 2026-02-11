"""
VTS Provider - Output Domain: VTS虚拟形象渲染实现

职责:
- 接收Intent并适配为VTS特定参数
- 渲染到VTube Studio
- 保留所有现有VTS插件功能
- 热键触发、表情控制、口型同步、道具管理、LLM智能热键匹配
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import Field, field_validator

from src.domains.output.providers.avatar.base import AvatarProviderBase as BaseAvatarProvider
from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.prompts import get_prompt_manager

if TYPE_CHECKING:
    from src.modules.streaming.audio_chunk import AudioChunk, AudioMetadata

    from src.modules.types import Intent

# LLM匹配依赖
LLM_AVAILABLE = False
try:
    import openai
except ImportError:
    pass


class VTSProvider(BaseAvatarProvider):
    """
    VTS Provider实现

    核心功能:
    - 热键触发
    - 表情控制（微笑、闭眼等）
    - 口型同步（VTS口型参数更新）
    - 道具管理（加载、卸载、更新）
    - LLM智能热键匹配
    """

    # ==================== 情感和动作映射配置 ====================

    # VTS参数名定义（常量）
    PARAM_MOUTH_SMILE = "MouthSmile"
    PARAM_MOUTH_OPEN = "MouthOpen"
    PARAM_EYE_OPEN_LEFT = "EyeOpenLeft"
    PARAM_EYE_OPEN_RIGHT = "EyeOpenRight"

    class ConfigSchema(BaseProviderConfig):
        """VTS输出Provider配置"""

        type: str = "vts"

        # VTS连接配置
        vts_host: str = Field(default="localhost", description="VTS WebSocket主机地址")
        vts_port: int = Field(default=8001, ge=1, le=65535, description="VTS WebSocket端口")

        # LLM智能匹配配置
        llm_matching_enabled: bool = Field(default=False, description="是否启用LLM智能热键匹配")

    def __init__(self, config: Dict[str, Any]):
        """
        初始化VTS Provider

        Args:
            config: Provider配置（来自[rendering.outputs.vts]）
        """
        super().__init__(config)
        self.logger = get_logger(self.__class__.__name__)

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # VTS连接配置
        self.vts_host = self.typed_config.vts_host
        self.vts_port = self.typed_config.vts_port

        # LLM智能匹配配置
        self.llm_matching_enabled = self.typed_config.llm_matching_enabled
        self.llm_api_key = self.typed_config.llm_api_key or ""
        self.llm_base_url = self.typed_config.llm_base_url or ""
        self.llm_model = self.typed_config.llm_model
        self.llm_temperature = self.typed_config.llm_temperature
        self.llm_max_tokens = self.typed_config.llm_max_tokens

        # 创建实例级别的可变对象，避免类变量存储可变对象的违规
        self._emotion_map = {
            "happy": {
                "MouthSmile": 1.0,
            },
            "surprised": {
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
                "MouthOpen": 0.5,
            },
            "sad": {
                "MouthSmile": -0.3,
                "EyeOpenLeft": 0.7,
                "EyeOpenRight": 0.7,
            },
            "angry": {
                "EyeOpenLeft": 0.6,
                "EyeOpenRight": 0.6,
                "MouthSmile": -0.5,
            },
            "shy": {
                "MouthSmile": 0.3,
                "EyeOpenLeft": 0.8,
                "EyeOpenRight": 0.8,
            },
            "love": {
                "MouthSmile": 0.8,
                "EyeOpenLeft": 0.9,
                "EyeOpenRight": 0.9,
            },
            "excited": {
                "MouthSmile": 1.0,
                "EyeOpenLeft": 1.0,
                "EyeOpenRight": 1.0,
            },
            "confused": {
                "EyeOpenLeft": 0.7,
                "EyeOpenRight": 0.7,
                "MouthOpen": 0.2,
            },
            "scared": {
                "EyeOpenLeft": 0.5,
                "EyeOpenRight": 0.5,
                "MouthOpen": 0.3,
            },
            "neutral": {
                # 默认状态，不设置参数
            },
        }
        self._action_hotkey_map = {
            "blink": "Blink",
            "nod": "Nod",
            "shake": "Shake",
            "wave": "Wave",
            "clap": "Clap",
            "motion": "Motion",
        }

        # 初始化LLM客户端
        self.openai_client = None
        if self.llm_matching_enabled and LLM_AVAILABLE and self.llm_api_key:
            try:
                self.openai_client = openai.AsyncOpenAI(
                    api_key=self.llm_api_key, base_url=self.llm_base_url if self.llm_base_url else None
                )
                self.logger.info("LLM客户端初始化成功")
            except Exception as e:
                self.logger.warning(f"LLM客户端初始化失败: {e}")

        # 情感热键映射
        self.emotion_hotkey_mapping = self.typed_config.emotion_hotkey_mapping

        # 缓存热键列表
        self.hotkey_list: List[Dict[str, Any]] = []
        self.hotkey_list_last_update: float = 0.0

        # 口型同步配置
        self.lip_sync_enabled = self.typed_config.lip_sync_enabled
        self.volume_threshold = self.typed_config.volume_threshold
        self.smoothing_factor = self.typed_config.smoothing_factor
        self.vowel_detection_sensitivity = self.typed_config.vowel_detection_sensitivity
        self.sample_rate = self.typed_config.sample_rate

        # 音频分析状态
        self.is_speaking = False
        self.current_vowel_values = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0
        self.audio_analysis_lock = asyncio.Lock()

        # 元音频率特征
        self.vowel_formants = {
            "A": [730, 1090],  # /a/ 的第一和第二共振峰
            "I": [270, 2290],  # /i/ 的第一和第二共振峰
            "U": [300, 870],  # /u/ 的第一和第二共振峰
            "E": [530, 1840],  # /e/ 的第一和第二共振峰
            "O": [570, 840],  # /o/ 的第一和第二共振峰
        }

        # VTS连接状态
        self._vts = None
        self._is_connecting = False

        # AudioStreamChannel 订阅
        self._vts_subscription_id: Optional[str] = None

        # 统计信息
        self.render_count = 0
        self.error_count = 0

        self.logger.info("VTSProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        # 初始化pyvts
        try:
            import pyvts  # noqa: F401
            from pyvts import vts, vts_request  # noqa: F401

            plugin_info = {
                "plugin_name": "Amaidesu_VTS_OutputProvider",
                "developer": "Phase 4 Implementation",
                "authentication_token_path": "./refactor/vts_token.txt",
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
            self.logger.error("pyvts库不可用，VTSProvider将被禁用")
            self._vts = None
            raise ImportError("pyvts library not available") from None

        # 初始化音频分析相关
        self.accumulated_audio = bytearray()
        self.accumulation_start_time = None
        self.audio_playback_start_time = None

    # ==================== AvatarProviderBase 抽象方法实现 ====================

    def _adapt_intent(self, intent: "Intent") -> Dict[str, Any]:
        """
        适配 Intent 为 VTS 特定参数

        Args:
            intent: 平台无关的 Intent

        Returns:
            VTS参数字典，包含:
            - expressions: Dict[str, float] - VTS参数值
            - hotkeys: List[str] - 热键ID列表
        """
        from src.modules.types import EmotionType

        result = {
            "expressions": {},
            "hotkeys": [],
        }

        # 1. 适配情感为VTS参数
        emotion_str = intent.emotion.value if isinstance(intent.emotion, EmotionType) else str(intent.emotion)
        if emotion_str in self._emotion_map:
            result["expressions"].update(self._emotion_map[emotion_str])
            self.logger.debug(f"情感映射: {emotion_str} -> {self._emotion_map[emotion_str]}")
        elif action_type_str in self._action_hotkey_map:
            hotkey_prefix = self._action_hotkey_map[action_type_str]
            # 尝试匹配完整热键名称
            matched_hotkey = self._find_hotkey_by_name(hotkey_prefix, action_name)
            if matched_hotkey:
                result["hotkeys"].append(matched_hotkey)

        self.logger.debug(f"Intent适配结果: expressions={result['expressions']}, hotkeys={result['hotkeys']}")
        return result

    async def _render_internal(self, params: Dict[str, Any]) -> None:
        """
        渲染到VTS平台

        Args:
            params: _adapt_intent() 返回的VTS参数字典
        """
        try:
            expressions = params.get("expressions", {})
            hotkeys = params.get("hotkeys", [])

            # 1. 应用VTS表情参数
            for param_name, param_value in expressions.items():
                await self.set_parameter_value(param_name, param_value)
                self.logger.debug(f"设置VTS参数: {param_name} = {param_value}")

            # 2. 触发热键
            for hotkey in hotkeys:
                await self.trigger_hotkey(hotkey)

            # 3. 更新音频分析状态（如果正在TTS播放）
            if self.lip_sync_enabled:
                await self._check_audio_state()

            self.render_count += 1

        except Exception as e:
            self.logger.error(f"VTS渲染失败: {e}", exc_info=True)
            self.error_count += 1
            raise RuntimeError(f"VTS渲染失败: {e}") from e

    async def _connect(self) -> None:
        """连接到VTS平台"""
        if not self._vts or self._is_connecting:
            return

        self._is_connecting = True

        try:
            self.logger.info(f"正在连接到VTS Studio... (Host: {self.vts_host}, Port: {self.vts_port})")
            await self._vts.connect()

            # 请求认证token
            self.logger.info("请求认证token...")
            await self._vts.request_authenticate_token()

            # 认证
            self.logger.info("正在使用token进行认证...")
            authenticated = await self._vts.request_authenticate()

            if authenticated:
                self.logger.info("VTS Studio认证成功!")
                self._is_connected = True

                # 加载热键列表
                await self._load_hotkeys()

                # 注册 AudioStreamChannel 订阅（如果启用口型同步）
                audio_channel = (
                    self._dependencies.get("audio_stream_channel") if hasattr(self, "_dependencies") else None
                )
                if audio_channel and self.lip_sync_enabled:
                    from src.modules.streaming.backpressure import BackpressureStrategy, SubscriberConfig

                    self._vts_subscription_id = await audio_channel.subscribe(
                        name="vts_lip_sync",
                        on_audio_start=self._on_lip_sync_start,
                        on_audio_chunk=self._on_lip_sync_chunk,
                        on_audio_end=self._on_lip_sync_end,
                        config=SubscriberConfig(
                            queue_size=100,
                            backpressure_strategy=BackpressureStrategy.DROP_NEWEST,
                        ),
                    )
                    self.logger.info("VTS 口型同步已订阅 AudioStreamChannel")

                # 测试微笑
                await self.smile(0)
                await self.close_eyes()
                # 重新睁眼
                await self.open_eyes()
            else:
                self.logger.error("VTS Studio认证失败")
                self._is_connected = False

        except ConnectionRefusedError:
            self.logger.error("VTS连接被拒绝，请确保VTS Studio正在运行并启用了API")
        except asyncio.TimeoutError:
            self.logger.error("VTS连接或认证超时")
        except Exception as e:
            self.logger.error(f"VTS连接或认证失败: {e}", exc_info=True)
        finally:
            self._is_connecting = False

    async def _disconnect(self) -> None:
        """断开VTS平台连接"""
        # 取消 AudioStreamChannel 订阅
        audio_channel = self._dependencies.get("audio_stream_channel") if hasattr(self, "_dependencies") else None
        if audio_channel and self._vts_subscription_id:
            try:
                await audio_channel.unsubscribe(self._vts_subscription_id)
                self.logger.info("VTS 口型同步已取消订阅 AudioStreamChannel")
            except Exception as e:
                self.logger.error(f"取消 AudioStreamChannel 订阅失败: {e}")
            finally:
                self._vts_subscription_id = None

        if self._vts:
            self.logger.info("正在断开VTS连接...")
            try:
                await self._vts.close()
                self.logger.info("VTS连接已断开")
            except Exception as e:
                self.logger.error(f"断开VTS连接失败: {e}")

        self._is_connected = False
        self._is_connecting = False

        # 清理热键列表
        self.hotkey_list.clear()

        # 清理音频分析状态
        self.accumulated_audio = bytearray()
        self.accumulation_start_time = None
        self.audio_playback_start_time = None

    # ==================== 辅助方法 ====================

    def _find_hotkey_by_name(self, prefix: str, suffix: str = "") -> Optional[str]:
        """
        根据前缀和后缀查找热键

        Args:
            prefix: 热键名称前缀
            suffix: 热键名称后缀

        Returns:
            匹配的热键ID，如果未找到返回None
        """
        if not self.hotkey_list:
            return None

        # 尝试精确匹配
        target_name = f"{prefix}_{suffix}" if suffix else prefix
        for hotkey in self.hotkey_list:
            if hotkey.get("name") == target_name:
                return hotkey.get("hotkeyID")

        # 尝试前缀匹配
        for hotkey in self.hotkey_list:
            name = hotkey.get("name", "")
            if name.startswith(prefix):
                if not suffix or suffix in name:
                    return hotkey.get("hotkeyID")

        return None

    # ==================== 向后兼容方法 ====================

    @property
    def _is_connected_and_authenticated(self) -> bool:
        """向后兼容的属性"""
        return self._is_connected

    # ==================== 旧的连接管理方法（向后兼容） ====================

    async def connect(self):
        """启动VTS连接和认证（向后兼容方法）"""
        await self._connect()

    async def disconnect(self):
        """断开VTS连接（向后兼容方法）"""
        await self._disconnect()

    # ==================== 热键管理 ====================

    async def _load_hotkeys(self):
        """获取VTS热键列表"""
        if not self._is_connected or not self._vts:
            self.logger.warning("VTS未连接，跳过加载热键列表")
            return

        try:
            response = await self._vts.request(self._vts.vts_request.requestHotKeyList())

            if response and response.get("data") and "availableHotkeys" in response["data"]:
                hotkeys = response["data"]["availableHotkeys"]
                self.hotkey_list = hotkeys

                # 缓存热键列表
                self.hotkey_list_last_update = time.time()
                self.logger.info(f"成功加载 {len(hotkeys)} 个热键")

            else:
                self.logger.warning(f"获取热键列表失败: {response}")
        except Exception as e:
            self.logger.error(f"获取热键列表失败: {e}")

    async def trigger_hotkey(self, hotkey_id: str) -> bool:
        """触发热键"""
        if not self._is_connected:
            self.logger.warning(f"VTS未连接，无法触发热键: {hotkey_id}")
            return False

        try:
            self.logger.info(f"触发热键: {hotkey_id}")

            request_msg = self._vts.vts_request.requestTriggerHotKey(hotkeyID=hotkey_id)
            response = await self._vts.request(request_msg)

            if response and response.get("messageType") == "HotkeyTriggerResponse":
                self.logger.info(f"热键 {hotkey_id} 触发成功")
                return True
            else:
                self.logger.warning(f"热键 {hotkey_id} 触发失败: {response}")
                return False

        except Exception as e:
            self.logger.error(f"触发热键失败: {hotkey_id}: {e}")
            return False

    # ==================== 表情控制 ====================

    async def smile(self, value: float = 1) -> bool:
        """控制微笑"""
        if not self._is_connected:
            return False

        try:
            return await self.set_parameter_value(self.PARAM_MOUTH_SMILE, value)
        except Exception as e:
            self.logger.error(f"设置微笑参数失败: {e}")
            return False

    async def close_eyes(self) -> bool:
        """闭眼"""
        if not self._is_connected:
            return False

        try:
            await self.set_parameter_value(self.PARAM_EYE_OPEN_LEFT, 0.0)
            await self.set_parameter_value(self.PARAM_EYE_OPEN_RIGHT, 0.0)
            self.logger.info("闭眼成功")
            return True
        except Exception as e:
            self.logger.error(f"闭眼失败: {e}")
            return False

    async def open_eyes(self) -> bool:
        """睁眼"""
        if not self._is_connected:
            return False

        try:
            await self.set_parameter_value(self.PARAM_EYE_OPEN_LEFT, 1.0)
            await self.set_parameter_value(self.PARAM_EYE_OPEN_RIGHT, 1.0)
            self.logger.info("睁眼成功")
            return True
        except Exception as e:
            self.logger.error(f"睁眼失败: {e}")
            return False

    # ==================== 参数设置 ====================

    async def set_parameter_value(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        """设置VTS参数值"""
        if not self._is_connected:
            self.logger.warning(f"VTS未连接，无法设置参数: {parameter_name} = {value}")
            return False

        try:
            response = await self._vts.request(
                self._vts.vts_request.requestSetParameterValue(parameter_name, value, weight)
            )

            if response and response.get("messageType") == "InjectParameterDataResponse":
                self.logger.debug(f"VTS参数 {parameter_name} 已设置为: {value}")
                return True
            else:
                self.logger.warning(f"设置VTS参数失败: {parameter_name}: {response}")
                return False
        except Exception as e:
            self.logger.error(f"设置VTS参数异常: {parameter_name}: {e}")
            return False

    async def get_parameter_value(self, parameter_name: str) -> Optional[float]:
        """获取VTS参数值"""
        if not self._is_connected:
            return None

        try:
            response = await self._vts.request(self._vts.vts_request.requestParameterValue(parameter_name))

            if response and response.get("messageType") == "ParameterValueResponse":
                return response.get("data", {}).get("value", 0.0)
            else:
                self.logger.warning(f"获取VTS参数失败: {parameter_name}: {response}")
                return None
        except Exception as e:
            self.logger.error(f"获取VTS参数异常: {parameter_name}: {e}")
            return None

    # ==================== 道具管理 ====================

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
        """
        加载道具

        Args:
            file_name: 文件名或Base64编码字符串
            position_x: X位置（-1到1）
            position_y: Y位置（-1到1）
            size: 大小（0到1）
            rotation: 旋转角度（0到360）
            fade_time: 淡入淡出时间（0到2）
            order: 层级（0到100）
            fail_if_order_taken: 如果订单号被占用则失败
            smoothing: 平滑（0到1）
            censored: 是否审查
            flipped: 是否翻转
            locked: 是否锁定
            unload_when_plugin_disconnects: 插件断开时是否自动卸载
            custom_data_base64: Base64编码的图片数据
            custom_data_ask_user_first: 是否询问用户确认
            custom_data_skip_asking_user_if_whitelisted: 是否跳过白名单检查
            custom_data_ask_timer: 计时器（-1表示无限制）

        Returns:
            道具实例ID，如果加载失败返回None
        """
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
                    self.logger.info(f"道具已加载: {instance_id}")
                    return instance_id
                else:
                    self.logger.warning(f"道具加载失败: {response}")
                    return None
            else:
                self.logger.warning(f"道具加载失败: {response}")
                return None

        except Exception as e:
            self.logger.error(f"加载道具失败: {e}", exc_info=True)
            return None

    async def unload_item(
        self, item_instance_id_list: Optional[List[str]] = None, file_name_list: Optional[List[str]] = None
    ) -> bool:
        """根据文件名卸载道具"""
        if not self._is_connected:
            self.logger.warning("VTS未连接，无法卸载道具")
            return False

        try:
            if not item_instance_id_list and not file_name_list:
                return False

            # 优先使用instance_id_list，如果提供了
            data = {
                "instanceIDs": item_instance_id_list if item_instance_id_list else [],
                "fileNames": file_name_list if file_name_list else [],
            }

            response = await self._vts.request(
                self._vts.vts_request.BaseRequest(message_type="ItemUnloadRequest", data=data)
            )

            if response and response.get("messageType") == "ItemUnloadResponse":
                self.logger.info(f"道具已卸载: {data}")
                return True
            else:
                self.logger.warning(f"道具卸载失败: {response}")
                return False
        except Exception as e:
            self.logger.error(f"卸载道具失败: {e}")
            return False

    # ==================== AudioStreamChannel 回调 ====================

    async def _on_lip_sync_start(self, metadata: "AudioMetadata"):
        """AudioStreamChannel: 音频流开始回调"""
        if not self.lip_sync_enabled or not self._is_connected:
            return

        self.logger.debug(f"收到音频流开始通知: {metadata.text[:30]}...")
        await self.start_lip_sync_session(metadata.text)

    async def _on_lip_sync_chunk(self, chunk: "AudioChunk"):
        """AudioStreamChannel: 音频块回调"""
        if not self.lip_sync_enabled or not self._is_connected:
            return

        try:
            # 重采样到 VTS 期望的采样率
            from src.modules.streaming.audio_utils import resample_audio

            audio_data = resample_audio(chunk.data, chunk.sample_rate, self.sample_rate)
            await self.process_tts_audio(audio_data, self.sample_rate)
        except Exception as e:
            self.logger.error(f"处理音频块失败: {e}")

    async def _on_lip_sync_end(self, metadata: "AudioMetadata"):
        """AudioStreamChannel: 音频流结束回调"""
        if not self.lip_sync_enabled or not self._is_connected:
            return

        self.logger.debug("收到音频流结束通知")
        await self.stop_lip_sync_session()

    # ==================== 口型同步 ====================

    async def _check_audio_state(self):
        """
        检查音频分析状态并更新口型参数

        这个方法由render_internal调用，用于在每次渲染时检查音频状态。
        注意：这个方法目前不做任何操作，口型参数由start_lip_sync_session和stop_lip_sync_session管理。
        """
        # 暂时不做任何操作，口型参数由start/stop_lip_sync_session管理
        pass

    async def start_lip_sync_session(self, text: str = ""):
        """启动口型同步会话"""
        if not self.lip_sync_enabled or not self._is_connected:
            return

        self.logger.info(f"启动口型同步会话: {text[:30]}...")

        self.is_speaking = True
        self.current_vowel_values = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0

        # 重置音频分析状态
        self.accumulated_audio = bytearray()
        self.accumulation_start_time = None
        self.audio_playback_start_time = None

    async def process_tts_audio(self, audio_data: bytes, sample_rate: int = 32000):
        """
        处理来自TTS的音频数据

        Args:
            audio_data: 音频数据字节
            sample_rate: 采样率（默认32000）
        """
        if not self.lip_sync_enabled or not self._is_connected:
            return

        try:
            # 转换音频数据为numpy数组
            import numpy as np

            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

            # 计算音量 (RMS)
            volume = np.sqrt(np.mean(audio_array**2))
            volume = float(max(0.0, min(1.0, volume * 10)))  # 放大并限制在0-1范围

            # 只有音量超过阈值时才进行元音分析
            if volume < self.volume_threshold:
                return

            # 计算频谱
            if len(audio_array) < 512:
                self.logger.debug("音频数据太短，无法有效分析元音")
                return

            # 使用FFT计算频谱
            fft = np.fft.rfft(audio_array)
            magnitude = np.abs(fft)
            freqs = np.fft.rfftfreq(len(audio_array), 1.0 / self.sample_rate)

            # 分析元音特征
            vowel_scores = {}
            for vowel, formants in self.vowel_formants.items():
                score = 0.0
                # 计算每个共振峰的能量
                for formant_freq in formants:
                    freq_mask = (freqs >= formant_freq - 50) & (freqs <= formant_freq + 50)
                    formant_energy = float(np.mean(magnitude[freq_mask]))
                    score += formant_energy

                vowel_scores[vowel] = float(score)

            # 归一化元音分数
            total_score = sum(vowel_scores.values()) + 1e-6
            vowel_values = {vowel: float(score / total_score) for vowel, score in vowel_scores.items()}

            # 应用敏感度调整
            for vowel in vowel_values:
                vowel_values[vowel] = float(min(1.0, vowel_values[vowel] * self.vowel_detection_sensitivity))

            # 更新VTS口型参数
            await self._update_lip_sync_parameters(volume, vowel_values)

        except Exception as e:
            self.logger.error(f"分析音频数据失败: {e}")

    async def _update_lip_sync_parameters(self, volume: float, vowel_values: Dict[str, float]):
        """更新VTS口型参数"""
        if not self._is_connected:
            return

        # 更新音量参数
        await self.set_parameter_value("VoiceVolume", volume)

        # 更新嘴巴张开参数（等效于音量）
        await self.set_parameter_value("MouthOpen", volume)

        # 更新静音参数
        silence_value = 1.0 if volume < self.volume_threshold else 0.0
        await self.set_parameter_value("VoiceSilence", silence_value)

        # 更新元音参数
        for vowel, value in vowel_values.items():
            param_name = f"Voice{vowel}"
            await self.set_parameter_value(param_name, value)

        # 更新静音参数（有声音时所有元音为0）
        if volume < self.volume_threshold:
            for vowel in ["A", "I", "U", "E", "O"]:
                await self.set_parameter_value(f"Voice{vowel}", 0.0)

        self.current_vowel_values = vowel_values.copy()
        self.current_volume = volume

    async def stop_lip_sync_session(self):
        """停止口型同步会话"""
        if not self.lip_sync_enabled or not self._is_connected:
            return

        self.is_speaking = False

        # 重置所有口型参数
        try:
            await self.set_parameter_value("VoiceSilence", 1.0)
            await self.set_parameter_value("VoiceVolume", 0.0)
            await self.set_parameter_value("MouthOpen", 0.0)
            await self.set_parameter_value("MouthSmile", 0.0)
            await self.set_parameter_value("EyeOpenLeft", 1.0)
            await self.set_parameter_value("EyeOpenRight", 1.0)

            # 重置元音参数
            for vowel in ["A", "I", "U", "E", "O"]:
                await self.set_parameter_value(f"Voice{vowel}", 0.0)

            self.current_vowel_values = {v: 0.0 for v in self.current_vowel_values}
            self.current_volume = 0.0

        except Exception as e:
            self.logger.error(f"重置口型参数失败: {e}")

        # 重置音频分析状态
        self.accumulated_audio = bytearray()
        self.accumulation_start_time = None
        self.audio_playback_start_time = None

    # ==================== LLM智能热键匹配 ====================

    async def _find_best_matching_hotkey_with_llm(self, text: str) -> Optional[str]:
        """使用LLM从热键列表中选择最匹配的热键"""
        if not self.llm_matching_enabled or not LLM_AVAILABLE:
            return None

        if not self.hotkey_list:
            self.logger.warning("热键列表为空，无法使用LLM匹配")
            return None

        # 构造热键列表字符串
        hotkey_str = "\n".join([f"- {hotkey.get('name', '')}" for hotkey in self.hotkey_list])

        # 使用 PromptManager 渲染提示词模板
        prompt = get_prompt_manager().render("output/vts_hotkey", text=text, hotkey_list_str=hotkey_str)

        try:
            if not self.openai_client:
                self.logger.warning("LLM客户端未初始化")
                return None

            response = self.openai_client.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.llm_temperature,
                max_tokens=self.llm_max_tokens,
            )

            if response and response.choices:
                selected_hotkey = response.choices[0].message.content.strip()

                # 验证返回的热键是否在列表中
                if selected_hotkey != "NONE" and selected_hotkey in [
                    hotkey.get("name", "") for hotkey in self.hotkey_list
                ]:
                    self.logger.info(f"LLM为文本'{text[:30]}...'选择了热键: {selected_hotkey}")
                    return selected_hotkey
                else:
                    self.logger.debug(f"LLM认为文本'{text[:30]}...'没有合适的热键匹配")
                    return None

            else:
                self.logger.warning("LLM API返回了无效响应")
                return None

        except Exception as e:
            self.logger.error(f"LLM匹配热键失败: {e}")
            return None

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取Provider统计信息"""
        return {
            "name": self.__class__.__name__,
            "is_connected": self._is_connected,
            "render_count": self.render_count,
            "error_count": self.error_count,
            "hotkey_count": len(self.hotkey_list),
            "lip_sync_enabled": self.lip_sync_enabled,
            "llm_matching_enabled": self.llm_matching_enabled,
            "concurrent_rendering": False,
        }
