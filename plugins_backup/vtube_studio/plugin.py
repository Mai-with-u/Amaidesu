# src/plugins/vtube_studio/plugin.py

import asyncio
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import numpy as np
from maim_message.message_base import MessageBase

# 尝试导入 openai，如果失败则设为 None
try:
    import openai
except ImportError:
    openai = None

# 尝试导入 pyvts，如果失败则设为 None
try:
    import pyvts
except ImportError:
    pyvts = None

# 尝试导入音频分析相关库
try:
    import librosa
    import scipy.signal

    AUDIO_ANALYSIS_AVAILABLE = True
except ImportError:
    librosa = None
    scipy = None
    AUDIO_ANALYSIS_AVAILABLE = False

# 类型检查时的导入
if TYPE_CHECKING and pyvts is not None:
    pass  # 暂时不需要特定的类型导入

# 从 core 导入基类和核心类
from src.core.amaidesu_core import AmaidesuCore
from src.core.plugin_manager import BasePlugin

# --- Helper Function ---


# --- Plugin Class ---
class VTubeStudioPlugin(BasePlugin):
    """
    Connects to VTube Studio, allows triggering hotkeys,
    and registers available actions to PromptContext.
    Now includes lip sync functionality for audio analysis.
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.config = self.plugin_config

        # --- pyvts 实例 ---
        self.vts: Optional[Any] = None  # 避免在 pyvts 未安装时的类型错误
        self._connection_task: Optional[asyncio.Task] = None
        self._is_connected_and_authenticated = False
        self._auth_token = None
        self._auth_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # --- 依赖检查 ---
        if pyvts is None:
            self.logger.error(
                "pyvts library not found. Please install it (`pip install pyvts`). VTubeStudioPlugin disabled."
            )
            return

        # --- 加载配置 ---
        self.plugin_name = self.config.get("plugin_name", "Amaidesu_VTS_Connector")
        self.developer = self.config.get("developer", "Amaidesu User")
        self.token_path = self.config.get("authentication_token_path", "./vts_token.txt")
        self.vts_host = self.config.get("vts_host")  # None means use default
        self.vts_port = self.config.get("vts_port")  # None means use default

        # Prompt Context 相关配置（已弃用，保留兼容性）
        self.register_hotkeys = self.config.get("register_hotkeys_context", True)
        self.hotkeys_priority = self.config.get("hotkeys_context_priority", 50)
        self.hotkeys_prefix = self.config.get("hotkeys_context_prefix", "你可以触发以下模型热键：")
        self.hotkey_format = self.config.get("hotkey_format", "'%s' (ID: %s)")
        self.hotkeys_separator = self.config.get("hotkeys_separator", ", ")

        # LLM 相关配置
        self.llm_matching_enabled = self.config.get("llm_matching_enabled", True)
        self.llm_api_key = self.config.get("llm_api_key", "")
        self.llm_base_url = self.config.get("llm_base_url", "https://api.siliconflow.cn/v1")
        self.llm_model = self.config.get("llm_model", "deepseek-chat")
        self.llm_temperature = self.config.get("llm_temperature", 0.1)
        self.llm_max_tokens = self.config.get("llm_max_tokens", 100)

        # 预设的表情/热键库
        self.emotion_hotkey_mapping = self.config.get(
            "emotion_hotkey_mapping",
            {
                "happy": ["微笑", "笑", "开心", "高兴", "愉快", "喜悦"],
                "surprised": ["惊讶", "吃惊", "震惊", "意外"],
                "sad": ["难过", "伤心", "悲伤", "沮丧", "失落"],
                "angry": ["生气", "愤怒", "不满", "恼火"],
                "shy": ["害羞", "脸红", "羞涩", "不好意思"],
                "wink": ["眨眼", "wink", "眨眨眼"],
            },
        )

        # --- 口型同步相关配置 ---
        lip_sync_config = self.config.get("lip_sync", {})
        self.lip_sync_enabled = lip_sync_config.get("enabled", True)
        self.volume_threshold = lip_sync_config.get("volume_threshold", 0.01)
        self.smoothing_factor = lip_sync_config.get("smoothing_factor", 0.3)
        self.vowel_detection_sensitivity = lip_sync_config.get("vowel_detection_sensitivity", 0.5)
        self.sample_rate = lip_sync_config.get("sample_rate", 32000)
        self.buffer_size = lip_sync_config.get("buffer_size", 1024)

        # 音频累积和时间同步配置
        self.min_accumulation_duration = lip_sync_config.get("min_accumulation_duration", 0.4)
        self.playback_sync_enabled = lip_sync_config.get("playback_sync_enabled", True)

        # 口型同步状态变量
        self.audio_buffer = deque(maxlen=self.sample_rate * 20)  # 2秒音频缓存
        self.current_vowel_values = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0
        self.is_speaking = False
        self.audio_analysis_lock = threading.Lock()

        # 音频累积相关变量
        self.accumulated_audio = bytearray()  # 累积的音频数据
        self.accumulation_start_time = None  # 累积开始时间（实际时间）
        self.audio_playback_start_time = None  # 音频播放开始时间

        # 元音频率特征（简化版本）
        self.vowel_formants = {
            "A": [730, 1090],  # /a/ 的第一和第二共振峰
            "I": [270, 2290],  # /i/ 的第一和第二共振峰
            "U": [300, 870],  # /u/ 的第一和第二共振峰
            "E": [530, 1840],  # /e/ 的第一和第二共振峰
            "O": [570, 840],  # /o/ 的第一和第二共振峰
        }

        # 检查音频分析依赖
        if self.lip_sync_enabled and not AUDIO_ANALYSIS_AVAILABLE:
            self.logger.warning(
                "Lip sync enabled but audio analysis libraries not available. Install with: pip install librosa scipy"
            )
            self.lip_sync_enabled = False

        # 缓存热键列表
        self.hotkey_list: List[Dict[str, Any]] = []

        # --- 初始化 pyvts ---
        plugin_info = {
            "plugin_name": self.plugin_name,
            "developer": self.developer,
            "authentication_token_path": self.token_path,
        }
        # 处理 host, port, name, version，确保字典包含它们，使用默认值填充
        vts_api_info = {
            "host": self.config.get("vts_host", "localhost"),  # Use config value or default
            "port": self.config.get("vts_port", 8001),  # Use config value or default
            "name": self.config.get("vts_api_name", "VTubeStudioPublicAPI"),  # Use config value or default
            "version": self.config.get("vts_api_version", "1.0"),  # Use config value or default
        }

        try:
            # Pass the guaranteed populated vts_api_info dict
            self.vts = pyvts.vts(plugin_info=plugin_info, vts_api_info=vts_api_info)
            self.logger.info("pyvts instance created.")
        except Exception as e:
            self.logger.error(f"Failed to initialize pyvts: {e}", exc_info=True)

        # --- OpenAI LLM 检查 ---
        if self.llm_matching_enabled:
            if openai is None:
                self.logger.error(
                    "openai library not found. Please install it (`pip install openai`). LLM匹配功能已禁用."
                )
                self.llm_matching_enabled = False
            elif not self.llm_api_key:
                self.logger.warning("LLM API key not configured. LLM匹配功能已禁用.")
                self.llm_matching_enabled = False
            else:
                # 初始化OpenAI客户端
                self.openai_client = openai.OpenAI(api_key=self.llm_api_key, base_url=self.llm_base_url)
                self.logger.info(f"LLM客户端已初始化，使用模型: {self.llm_model}")

    def _setup_avatar_adapter(self):
        """初始化通用虚拟形象控制系统的适配器"""
        try:
            # 导入适配器
            from src.plugins.vtube_studio.avatar_adapter import VTSAdapter

            # 获取 AvatarControlManager（已在核心初始化时创建）
            avatar_manager = self.core.avatar
            if not avatar_manager:
                self.logger.warning("AvatarControlManager 未在核心中初始化，跳过适配器设置")
                return

            self.logger.info("使用核心中的 AvatarControlManager")

            # 创建 VTS 适配器
            self.vts_adapter = VTSAdapter(self)
            avatar_manager.register_adapter(self.vts_adapter)

            # 设置为活跃适配器（如果还没有活跃适配器）
            if avatar_manager.get_active_adapter() is None:
                avatar_manager.set_active_adapter("vts")
                self.logger.info("设置 VTS 为默认活跃适配器")

            # 同时也注册为服务（向后兼容）
            self.core.register_service("avatar_control_manager", avatar_manager)
            self.logger.info("已集成通用虚拟形象控制系统")

        except ImportError as e:
            self.logger.warning(f"无法导入虚拟形象控制模块: {e}")
        except Exception as e:
            self.logger.error(f"设置虚拟形象适配器时出错: {e}", exc_info=True)

    async def setup(self):
        await super().setup()
        if not self.vts:
            self.logger.warning("VTubeStudioPlugin setup skipped (failed init).")
            return
        # 注册处理函数，监听所有 WebSocket 消消息
        self.core.register_websocket_handler("*", self.handle_maicore_message)
        self.logger.info("VTube Studio 插件已设置，监听所有 MaiCore WebSocket 消息。")

        # 启动连接和认证的后台任务
        self._connection_task = asyncio.create_task(self._connect_and_auth(), name="VTS_ConnectAuth")
        self.logger.info("VTube Studio connection and authentication task started.")

        # --- Register self as a service for triggering actions ---
        self.core.register_service("vts_control", self)
        self.logger.info("Registered 'vts_control' service.")

        # --- 注册为TTS音频数据处理器 ---
        if self.lip_sync_enabled:
            self.core.register_service("vts_lip_sync", self)
            self.logger.info("Registered 'vts_lip_sync' service for audio analysis.")

        # --- 集成通用虚拟形象控制系统 ---
        self._setup_avatar_adapter()

    async def _connect_and_auth(self):
        """Internal task to connect, authenticate, and register context."""
        if not self.vts:
            return
        try:
            self.logger.info("Attempting to connect to VTube Studio...")
            await self.vts.connect()
            self.logger.info("Connected to VTube Studio WebSocket.")

            # --- 调整顺序：总是先处理 token ---
            self.logger.info("Requesting authentication token (will prompt in VTS if needed)...")
            # 这会请求新token或检查/加载现有token
            await self.vts.request_authenticate_token()
            # self._auth_token = self.vts.token # 会报错
            self.logger.info("Token request process completed.")

            # --- 然后再进行认证 ---
            self.logger.info("Attempting to authenticate using token...")
            authenticated = await self.vts.request_authenticate()

            if authenticated:
                self.logger.info("Successfully authenticated with VTube Studio API.")
                self._is_connected_and_authenticated = True

                # --- 认证成功后，获取热键列表 ---
                await self._load_hotkeys()

                # 测试微笑
                await self.smile(0)

                # 测试闭眼
                # await asyncio.sleep(5)
                await self.close_eyes()

            else:
                # 如果 token 流程没问题，这里应该不会失败，但还是处理一下
                self.logger.error("Authentication failed even after token request process.")
                self._is_connected_and_authenticated = False

        except ConnectionRefusedError:
            self.logger.error("Connection to VTube Studio refused. Is VTS running and the API enabled?")
        except asyncio.TimeoutError:
            self.logger.error("Connection or authentication to VTube Studio timed out.")
        except Exception as e:
            # 加一个特定的 KeyErorr 检查，以防万一
            if isinstance(e, KeyError) and "authenticated" in str(e):
                self.logger.error(
                    f"KeyError accessing 'authenticated' during authentication. Unexpected VTS response? {e}",
                    exc_info=True,
                )
            else:
                self.logger.error(f"Error during VTube Studio connection/authentication: {e}", exc_info=True)
            self._is_connected_and_authenticated = False  # 保证出错时状态为 False
        finally:
            # 如果认证失败，确保状态正确
            if not self._is_connected_and_authenticated:
                self.logger.warning("VTS Connection/Authentication failed.")
                # 可以在这里尝试关闭连接，防止 pyvts 内部状态问题
                if self.vts:
                    try:
                        await self.vts.close()
                        self.logger.debug("Closed VTS connection due to auth failure.")
                    except Exception as close_error:
                        self.logger.debug(f"Error closing VTS connection: {close_error}")

    async def get_hotkey_list(self) -> Optional[list[Dict[str, Any]]]:
        """Requests the list of available hotkeys from VTube Studio.

        Returns:
            A list of hotkey dictionaries (containing 'name', 'hotkeyID', etc.)
            if successful, None otherwise.
        """
        if not self._is_connected_and_authenticated or not self.vts:
            self.logger.warning("Cannot get hotkey list: Not connected or authenticated.")
            return None

        try:
            self.logger.warning("Requesting VTube Studio hotkey list...")
            response = await self.vts.request(self.vts.vts_request.requestHotKeyList())

            if response and response.get("data") and "availableHotkeys" in response["data"]:
                hotkeys = response["data"]["availableHotkeys"]
                self.logger.warning(f"Received {len(hotkeys)} hotkeys from VTS.")
                return hotkeys
            else:
                self.logger.warning(f"Could not get hotkey list from VTS or invalid response format: {response}")
                return None
        except Exception as e:
            self.logger.error(f"Error requesting hotkey list from VTS: {e}", exc_info=True)
            return None

    async def _load_hotkeys(self):
        """获取热键列表"""
        # 获取热键列表
        self.hotkey_list = await self.get_hotkey_list()
        if not self.hotkey_list:
            self.logger.warning("无法获取热键列表")
            return

        self.logger.info(f"成功加载 {len(self.hotkey_list)} 个热键")

    async def _find_best_matching_hotkey_with_llm(self, text: str) -> Optional[str]:
        """使用LLM从热键列表中选择最匹配的热键"""
        if not self.llm_matching_enabled or not self.hotkey_list:
            return None

        # 构造热键列表字符串
        hotkey_names = [hotkey.get("name", "") for hotkey in self.hotkey_list if hotkey.get("name")]
        if not hotkey_names:
            return None

        hotkey_list_str = "\n".join([f"- {name}" for name in hotkey_names])

        # 构造提示词
        #         prompt = f"""你是一个VTube Studio热键匹配助手。根据用户的文本内容，从提供的热键列表中选择最合适的热键。

        # 用户文本: "{text}"

        # 可用热键列表:
        # {hotkey_list_str}

        # 规则:
        # 1. 仔细分析用户文本的情感和动作意图
        # 2. 从热键列表中选择最匹配的一个热键名称
        # 3. 如果没有合适的匹配，返回 "NONE"
        # 4. 只返回热键名称或"NONE"，不要其他解释

        # 你的选择:"""
        prompt = f"""你是一个VTube Studio热键匹配助手。根据用户的文本内容，从提供的热键列表中选择最合适的热键。

用户文本: "{text}"

可用热键列表:
{hotkey_list_str}

规则:
1. 仔细分析用户文本的情感和动作意图
2. 无论如何都从热键列表中选择最匹配的一个热键名称，只返回热键名称，不要其他解释

你的选择:"""

        try:
            response = self.openai_client.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.llm_temperature,
                max_tokens=self.llm_max_tokens,
            )

            if response and response.choices:
                selected_hotkey = response.choices[0].message.content.strip()

                # 验证返回的热键名称是否在列表中
                if selected_hotkey != "NONE" and selected_hotkey in hotkey_names:
                    self.logger.info(f"LLM为文本'{text}'选择了热键: {selected_hotkey}")
                    return selected_hotkey
                elif selected_hotkey == "NONE":
                    self.logger.debug(f"LLM认为文本'{text}'没有合适的热键匹配")
                    return None
                else:
                    self.logger.warning(f"LLM返回了无效的热键名称: {selected_hotkey}")
                    return None
            else:
                self.logger.warning("LLM API返回了无效响应")
                return None

        except Exception as e:
            self.logger.error(f"使用LLM匹配热键时出错: {e}")
            return None

    async def cleanup(self):
        self.logger.info("Cleaning up VTubeStudioPlugin...")
        # 停止后台连接任务
        if self._connection_task and not self._connection_task.done():
            self.logger.debug("Cancelling VTS connection task...")
            self._connection_task.cancel()
            try:
                await asyncio.wait_for(self._connection_task, timeout=2.0)
            except asyncio.TimeoutError:
                self.logger.warning("VTS connection task did not finish cancelling in time.")
            except asyncio.CancelledError:
                pass  # Expected

        # 关闭 pyvts 连接
        if self.vts:
            try:
                self.logger.info("Closing connection to VTube Studio...")
                await self.vts.close()
                self.logger.info("VTube Studio connection closed.")
            except Exception as e:
                self.logger.error(f"Error closing VTube Studio connection: {e}", exc_info=True)

        # 清理热键缓存
        self.hotkey_list.clear()

        self._is_connected_and_authenticated = False
        await super().cleanup()

    # --- Public method for triggering hotkey (to be called by CommandProcessor) ---
    async def handle_maicore_message(self, message: MessageBase):
        """处理从 MaiCore 收到的消息，根据消息段类型进行不同的处理。"""
        if not message.message_segment:
            return

        # 处理 vtb_text 类型的消息段（新的LLM匹配功能）
        if message.message_segment.type == "vtb_text":
            text_data = message.message_segment.data
            if not isinstance(text_data, str) or not text_data.strip():
                self.logger.debug("收到非字符串或空的vtb_text消息段，跳过")
                return

            text_data = text_data.strip()
            self.logger.info(f"收到vtb_text消息: '{text_data[:50]}...'")

            # 使用LLM找到最匹配的热键
            if self.llm_matching_enabled:
                best_hotkey = await self._find_best_matching_hotkey_with_llm(text_data)
                if best_hotkey:
                    self.logger.info(f"基于LLM为vtb_text触发热键: {best_hotkey}")
                    await self.trigger_hotkey(best_hotkey)
                else:
                    self.logger.debug(f"未找到与vtb_text匹配的热键: {text_data}")

    async def trigger_hotkey(self, hotkey_id: str) -> bool:
        """
        Triggers a hotkey in VTube Studio by its ID.

        Args:
            hotkey_id: The ID of the hotkey to trigger.Hotkey name or unique id of hotkey to execute, can be obtained via VTSRequest.requestHotKeyList()

        Returns:
            True if the request was sent successfully, False otherwise.
        """
        if not self._is_connected_and_authenticated or not self.vts:
            self.logger.warning(f"Cannot trigger hotkey '{hotkey_id}': Not connected or authenticated.")
            return False

        self.logger.info(f"Attempting to trigger hotkey with ID: {hotkey_id}")
        try:
            request_msg = self.vts.vts_request.requestTriggerHotKey(hotkeyID=hotkey_id)
            response = await self.vts.request(request_msg)
            # Check response for success/error if needed - pyvts might raise exceptions on API errors
            if response and response.get("messageType") == "APIError":
                error_data = response.get("data", {})
                self.logger.error(
                    f"API Error triggering hotkey '{hotkey_id}': ID {error_data.get('errorID')}, Msg: {error_data.get('message')}"
                )
                return False
            elif response and response.get("messageType") == "HotkeyTriggerResponse":
                self.logger.info(f"Successfully sent trigger request for hotkey: {hotkey_id}")
                return True
            else:
                self.logger.warning(
                    f"Unexpected response type when triggering hotkey '{hotkey_id}': {response.get('messageType') if response else 'No Response'}"
                )
                return False

        except Exception as e:
            self.logger.error(f"Error sending trigger hotkey request for '{hotkey_id}': {e}", exc_info=True)
            return False

    async def get_parameter_value(self, parameter_name: str) -> bool:
        """
        获取 VTS 参数值
        parameter : str
            参数名称
        """
        if not self._is_connected_and_authenticated or not self.vts:
            self.logger.warning(f"无法获取 '{parameter_name}' 参数值: 未连接或未认证。")
            return False

        try:
            response = await self.vts.request(self.vts.vts_request.requestParameterValue(parameter_name))
            if response and response.get("messageType") == "ParameterValueResponse":
                self.logger.info(f"成功获取 '{parameter_name}' 参数值为 {response}")
                return response.get("data", {}).get("value", 0)
            else:
                self.logger.warning(f"获取 '{parameter_name}' 参数值失败: {response}")
                return False
        except Exception as e:
            self.logger.error(f"获取 '{parameter_name}' 参数值失败: {e}", exc_info=True)
            return False

    async def set_parameter_value(self, parameter_name: str, value: float, weight: float = 1) -> bool:
        """
        设置 VTS 参数值
        parameter : str
            参数名称
        value : float
            数据值，范围为 [-1000000, 1000000]
        weight : float, optional
            可以混合你的值与 VTS 面部跟踪参数，从 0 到 1,
        """
        if not self._is_connected_and_authenticated or not self.vts:
            self.logger.warning(f"无法设置 '{parameter_name}' 参数值: 未连接或未认证。")
            return False

        try:
            response = await self.vts.request(
                self.vts.vts_request.requestSetParameterValue(parameter_name, value, weight)
            )
            if response and response.get("messageType") == "InjectParameterDataResponse":
                self.logger.debug(f"成功设置 '{parameter_name}' 参数值为 {value}")
                return True
            else:
                self.logger.warning(f"设置 '{parameter_name}' 参数值失败: {response}")
                return False
        except Exception as e:
            self.logger.error(f"设置 '{parameter_name}' 参数值失败: {e}", exc_info=True)
            return False

    async def close_eyes(self) -> bool:
        """
        闭眼
        """
        # 并行闭上左右眼好像会有问题
        # return await asyncio.gather(
        #     self.set_parameter_value("EyeOpenLeft", 0), self.set_parameter_value("EyeOpenRight", 0)
        # )
        await self.set_parameter_value("EyeOpenLeft", 0)
        await self.set_parameter_value("EyeOpenRight", 0)

    async def open_eyes(self) -> bool:
        """
        睁眼
        """
        # 并行睁开左右眼
        # return await asyncio.gather(
        #     self.set_parameter_value("EyeOpenLeft", 1), self.set_parameter_value("EyeOpenRight", 1)
        # )
        await self.set_parameter_value("EyeOpenLeft", 1)
        await self.set_parameter_value("EyeOpenRight", 1)

    async def smile(self, value: float = 1) -> bool:
        """
        微笑控制,1 为嘻嘻,0 为不嘻嘻
        """
        return await self.set_parameter_value("MouthSmile", value)

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
        加载挂件
        """
        data = {
            "fileName": file_name,  # 就算用的是base64，但也要指定文件名
            "positionX": position_x,  # 屏幕范围为 [-1, 1]，0 为屏幕中心，合法范围为[-1000, 1000]
            "positionY": position_y,  # 屏幕范围为 [-1, 1]，0 为屏幕中心，合法范围为[-1000, 1000]
            "size": size,  # 范围为 [0, 1]
            "rotation": rotation,  # 范围为 [0, 360]
            "fadeTime": fade_time,  # 范围为 [0, 2]
            "order": order,  # 范围为 [0, 100]
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

        response = await self.vts.request(self.vts.vts_request.BaseRequest(message_type="ItemLoadRequest", data=data))
        if not response or response.get("messageType") != "ItemLoadResponse":
            self.logger.error(f"加载挂件失败: {response}")
            return None

        self.logger.info(f"成功加载挂件: {response}")
        return response.get("data", {}).get("instanceID", None)

    async def unload_item(
        self,
        item_instance_id_list: Optional[list[str]] = None,
        file_name_list: Optional[list[str]] = None,
        unload_all_in_scene: bool = False,
        unload_all_loaded_by_this_plugin: bool = False,
        allow_unloading_items_loaded_by_user_or_other_plugins: bool = True,
    ) -> bool:
        """
        卸载挂件
        """
        if item_instance_id_list is None:
            item_instance_id_list = []
        if file_name_list is None:
            file_name_list = []

        data = {
            "unloadAllInScene": unload_all_in_scene,
            "unloadAllLoadedByThisPlugin": unload_all_loaded_by_this_plugin,
            "allowUnloadingItemsLoadedByUserOrOtherPlugins": allow_unloading_items_loaded_by_user_or_other_plugins,
            "instanceIDs": item_instance_id_list,
            "fileNames": file_name_list,
        }

        response = await self.vts.request(self.vts.vts_request.BaseRequest(message_type="ItemUnloadRequest", data=data))
        if response and response.get("messageType") == "ItemUnloadResponse":
            self.logger.info(f"成功卸载挂件: {response}")
            return True
        else:
            self.logger.error(f"卸载挂件失败: {response}")
            return False

    # --- 未来可以添加处理 VTS 事件的方法 ---
    # async def handle_vts_event(self, event_data): ...

    # --- 未来可以添加触发热键的服务方法 ---
    # async def trigger_hotkey(self, hotkey_id: str) -> bool: ...
    # 并将 trigger_hotkey 注册为 Core 的服务

    async def analyze_audio_chunk(self, audio_data: bytes, sample_rate: int = 32000) -> Dict[str, float]:
        """
        分析音频块，检测音量和元音特征

        Args:
            audio_data: 音频数据字节
            sample_rate: 采样率

        Returns:
            包含音量和元音检测结果的字典
        """
        if not self.lip_sync_enabled or not AUDIO_ANALYSIS_AVAILABLE:
            return {"volume": 0.0, "A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}

        try:
            # 转换音频数据为numpy数组
            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

            if len(audio_array) == 0:
                return {"volume": 0.0, "A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}

            # 计算音量 (RMS)
            volume = np.sqrt(np.mean(audio_array**2))
            volume = float(max(0.0, min(1.0, volume * 10)))  # 放大并限制在0-1范围，转换为Python float

            # 只有音量超过阈值时才进行元音分析
            if volume < self.volume_threshold:
                return {"volume": volume, "A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}

            # 计算频谱
            if len(audio_array) < 512:
                # 音频太短，无法有效分析
                return {"volume": volume, "A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}

            # 使用FFT计算频谱
            fft = np.fft.rfft(audio_array)
            magnitude = np.abs(fft)
            freqs = np.fft.rfftfreq(len(audio_array), 1 / sample_rate)

            # 分析元音特征
            vowel_scores = self._analyze_vowel_features(magnitude, freqs)

            # 归一化元音分数
            total_score = sum(vowel_scores.values()) + 1e-6
            vowel_values = {vowel: float(score / total_score) for vowel, score in vowel_scores.items()}

            # 应用敏感度调整
            for vowel in vowel_values:
                vowel_values[vowel] = float(min(1.0, vowel_values[vowel] * self.vowel_detection_sensitivity))

            result = {"volume": volume}
            result.update(vowel_values)

            return result

        except Exception as e:
            self.logger.error(f"音频分析失败: {e}", exc_info=True)
            return {"volume": 0.0, "A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}

    def _analyze_vowel_features(self, magnitude: np.ndarray, freqs: np.ndarray) -> Dict[str, float]:
        """
        基于频谱分析元音特征

        Args:
            magnitude: 频谱幅度
            freqs: 频率数组

        Returns:
            各元音的分数字典
        """
        vowel_scores = {}

        for vowel, formants in self.vowel_formants.items():
            score = 0.0

            # 计算每个共振峰的能量
            for formant_freq in formants:
                # 找到共振峰频率附近的频谱能量
                freq_mask = (freqs >= formant_freq - 50) & (freqs <= formant_freq + 50)
                if np.any(freq_mask):
                    formant_energy = float(np.mean(magnitude[freq_mask]))  # 确保转换为Python float
                    score += formant_energy

            vowel_scores[vowel] = float(score)  # 确保转换为Python float

        return vowel_scores

    async def process_tts_audio(self, audio_data: bytes, sample_rate: int = 32000):
        """
        处理来自TTS的音频数据进行口型同步
        基于实际播放时间控制口型，确保与音频播放同步

        Args:
            audio_data: 音频数据字节
            sample_rate: 采样率
        """
        if not self.lip_sync_enabled or not self._is_connected_and_authenticated:
            return

        try:
            current_time = time.time()

            # 如果是第一个音频块，初始化累积和播放时间
            if self.accumulation_start_time is None:
                self.accumulation_start_time = current_time
                self.audio_playback_start_time = current_time
                self.accumulated_audio = bytearray()

            # 检查累积的音频数据大小，避免占用过多内存
            max_audio_buffer = 5 * sample_rate * 2  # 5秒的音频数据（16位，2字节/样本）
            if len(self.accumulated_audio) > max_audio_buffer:
                self.logger.warning("口型同步缓冲区过大，重置缓冲区")
                self.accumulated_audio = bytearray(self.accumulated_audio[-max_audio_buffer:])
                self.accumulation_start_time = current_time

            # 累积音频数据
            self.accumulated_audio.extend(audio_data)

            # 计算实际播放时间经过的时长
            elapsed_playback_time = current_time - self.audio_playback_start_time

            # 计算音频数据对应的时长
            accumulated_samples = len(self.accumulated_audio) // 2  # 16位音频，2字节per sample
            audio_duration = accumulated_samples / sample_rate

            # 检查是否需要处理累积的音频
            should_process = False

            # 基于实际播放时间的控制策略
            if (
                elapsed_playback_time >= self.min_accumulation_duration
                and audio_duration >= self.min_accumulation_duration
            ):
                # 播放时间和音频时长都达到最小要求，可以处理
                should_process = True

            if should_process:
                # 分析累积的音频特征
                analysis_result = await self.analyze_audio_chunk(bytes(self.accumulated_audio), sample_rate)

                # 更新口型参数
                await self._update_lip_sync_parameters(analysis_result)

                # 重置累积状态，但保持播放时间基准
                self.accumulated_audio = bytearray()
                self.accumulation_start_time = current_time

        except Exception as e:
            self.logger.error(f"处理TTS音频数据失败: {e}", exc_info=True)
            # 出错时重置累积状态
            self.accumulated_audio = bytearray()
            self.accumulation_start_time = None

    async def _update_lip_sync_parameters(self, analysis_result: Dict[str, float]):
        """
        根据音频分析结果更新VTS口型参数

        Args:
            analysis_result: 音频分析结果
        """
        if not self._is_connected_and_authenticated:
            return

        try:
            volume = float(analysis_result["volume"])  # 确保转换为Python原生float

            # 更新音量参数
            await self.set_parameter_value("VoiceVolume", volume)

            # 更新嘴巴张开参数（等效于音量）
            await self.set_parameter_value("MouthOpen", volume)

            # 更新静音参数（音量低于阈值时为1）
            silence_value = 1.0 if volume < self.volume_threshold else 0.0
            await self.set_parameter_value("VoiceSilence", silence_value)

            # 更新元音参数（仅在有声音时）
            if volume >= self.volume_threshold:
                for vowel in ["A", "I", "U", "E", "O"]:
                    param_name = f"Voice{vowel}"
                    vowel_value = float(analysis_result.get(vowel, 0.0))  # 确保转换为Python原生float

                    # 应用平滑滤波
                    with self.audio_analysis_lock:
                        current_value = self.current_vowel_values[vowel]
                        smoothed_value = (
                            current_value * (1 - self.smoothing_factor) + vowel_value * self.smoothing_factor
                        )
                        # 确保存储和传递的都是Python原生float
                        smoothed_value = float(smoothed_value)
                        self.current_vowel_values[vowel] = smoothed_value

                    await self.set_parameter_value(param_name, smoothed_value)
            else:
                # 静音时将所有元音参数设为0
                for vowel in ["A", "I", "U", "E", "O"]:
                    param_name = f"Voice{vowel}"
                    await self.set_parameter_value(param_name, 0.0)

                with self.audio_analysis_lock:
                    self.current_vowel_values[vowel] = 0.0

        except Exception as e:
            self.logger.error(f"更新口型参数失败: {e}", exc_info=True)

    async def start_lip_sync_session(self, text: str = ""):
        """
        开始口型同步会话

        Args:
            text: 即将播放的文本内容
        """
        if not self.lip_sync_enabled:
            return

        self.logger.info(f"开始口型同步会话: {text[:50]}...")
        self.is_speaking = True

        # 重置播放时间基准
        await self.reset_playback_timing()

        # 重置口型状态
        with self.audio_analysis_lock:
            for vowel in self.current_vowel_values:
                self.current_vowel_values[vowel] = 0.0
            self.current_volume = 0.0

    async def stop_lip_sync_session(self):
        """
        停止口型同步会话
        """
        if not self.lip_sync_enabled:
            return

        self.logger.info("停止口型同步会话")
        self.is_speaking = False

        # 重置所有口型参数为静音状态
        try:
            await self.set_parameter_value("VoiceSilence", 1.0)
            await self.set_parameter_value("VoiceVolume", 0.0)
            await self.set_parameter_value("MouthOpen", 0.0)

            for vowel in ["A", "I", "U", "E", "O"]:
                param_name = f"Voice{vowel}"
                await self.set_parameter_value(param_name, 0.0)

            with self.audio_analysis_lock:
                for vowel in self.current_vowel_values:
                    self.current_vowel_values[vowel] = 0.0
                self.current_volume = 0.0

            # 重置音频累积状态和时间基准
            self.accumulated_audio = bytearray()
            self.accumulation_start_time = None
            self.audio_playback_start_time = None

        except Exception as e:
            self.logger.error(f"重置口型参数失败: {e}", exc_info=True)

    async def reset_playback_timing(self):
        """
        重置播放时间基准，在开始新的TTS播放时调用
        确保口型同步与新的音频播放同步
        """
        if not self.lip_sync_enabled:
            return

        current_time = time.time()
        self.audio_playback_start_time = current_time
        self.accumulation_start_time = None
        self.accumulated_audio = bytearray()

        self.logger.debug("播放时间基准已重置，开始新的口型同步")


# --- Plugin Entry Point ---
plugin_entrypoint = VTubeStudioPlugin
