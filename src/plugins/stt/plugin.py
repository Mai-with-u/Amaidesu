# Amaidesu STT Plugin: src/plugins/stt/plugin.py

import asyncio

# import logging
import os
import sys
import base64
import hashlib
import hmac
import json
import ssl
import time
import numpy as np
from datetime import datetime
from urllib.parse import urlencode
from typing import Dict, Any, Optional, List
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from maim_message import MessageBase, BaseMessageInfo, UserInfo, GroupInfo, Seg, FormatInfo

# --- 解决Windows中文用户名路径编码问题 ---
# 设置环境变量确保PyTorch和其他库能正确处理路径
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if os.name == "nt":  # Windows系统
    # 设置控制台代码页为UTF-8
    try:
        import subprocess

        subprocess.run(["chcp", "65001"], shell=True, capture_output=True)
    except Exception:
        pass  # 忽略错误，继续执行

# --- Remote Stream 支持 ---
# 检查是否安装了remote_stream依赖
try:
    from src.plugins.remote_stream.plugin import RemoteStreamService, StreamMessage, MessageType

    REMOTE_STREAM_AVAILABLE = True
except ImportError:
    REMOTE_STREAM_AVAILABLE = False
    print("提示: 未找到 remote_stream 插件，将使用本地音频输入。", file=sys.stderr)

# --- Dependencies Check & TOML ---
try:
    import torch
except ImportError:
    print("依赖缺失: 请运行 'pip install torch' 来使用 VAD 功能。", file=sys.stderr)
    torch = None
try:
    import sounddevice as sd
except ImportError:
    print("依赖缺失: 请运行 'pip install sounddevice' 来使用音频输入。", file=sys.stderr)
    sd = None
try:
    import aiohttp
except ImportError:
    print("依赖缺失: 请运行 'pip install aiohttp' 来与讯飞 API 通信。", file=sys.stderr)
    aiohttp = None
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import toml as tomllib
    except ImportError:
        print("依赖缺失: 请运行 'pip install toml' 来加载 STT 插件配置。", file=sys.stderr)
        tomllib = None

# --- Amaidesu Core Imports ---

# os.environ["http_proxy"] = "http://127.0.0.1:7890"
# os.environ["https_proxy"] = "http://127.0.0.1:7890"
# --- Plugin Configuration Loading ---
# _PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
# _CONFIG_FILE = os.path.join(_PLUGIN_DIR, "config.toml")
#
#
# def load_plugin_config() -> Dict[str, Any]:
#     """Loads the plugin's specific config.toml file."""
#     if tomllib is None:
#         logger.error("TOML library not available, cannot load STT plugin config.")
#         return {}
#     try:
#         with open(_CONFIG_FILE, "rb") as f:
#             config = tomllib.load(f)
#             logger.info(f"成功加载 STT 插件配置文件: {_CONFIG_FILE}")
#             return config
#     except FileNotFoundError:
#         logger.warning(f"STT 插件配置文件未找到: {_CONFIG_FILE}。将使用默认值。")
#     except tomllib.TOMLDecodeError as e:
#         logger.error(f"STT 插件配置文件 '{_CONFIG_FILE}' 格式无效: {e}。将使用默认值。")
#     except Exception as e:
#         logger.error(f"加载 STT 插件配置文件 '{_CONFIG_FILE}' 时发生未知错误: {e}", exc_info=True)
#     return {}


# Status for iFlytek frames
STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2

# 默认 WebSocket 地址
DEFAULT_IFLYTEK_HOST = "iat-api.xfyun.cn"
DEFAULT_IFLYTEK_PATH = "/v2/iat"


class STTPlugin(BasePlugin):
    """
    语音转文字 (Speech-to-Text) 插件，使用讯飞流式 API 和本地 VAD。
    使用 sounddevice 捕获音频，通过 VAD 判断话语起止，实时发送到讯飞，
    并将最终识别结果包装成 MessageBase 发送回 Core。
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)  # Initialize BasePlugin
        self.config = self.plugin_config  # 直接使用注入的 plugin_config
        # self.logger = logger  # 已由基类初始化

        # --- Basic Dependency Check ---
        if torch is None or sd is None or aiohttp is None or tomllib is None:
            self.logger.error("缺少核心依赖 (torch, sounddevice, aiohttp, toml)，STT 插件禁用。")
            return

        # --- Load Specific Config Sections ---
        self.iflytek_config = self.config.get("iflytek_asr", {})
        self.vad_config = self.config.get("vad", {})
        self.audio_config = self.config.get("audio", {})
        self.message_config = self.config.get("message_config", {})  # Used by _create_stt_message
        self.enable_correction = self.config.get("enable_correction", True)  # For potential future use

        # --- Prompt Context Tags (from message_config) ---
        self.context_tags: Optional[List[str]] = self.message_config.get("context_tags")
        if not isinstance(self.context_tags, list):
            if self.context_tags is not None:
                self.logger.warning(
                    f"Config 'context_tags' in [message_config] is not a list ({type(self.context_tags)}), will fetch all context."
                )
            self.context_tags = None
        elif not self.context_tags:
            self.logger.info("'context_tags' in [message_config] is empty, will fetch all context.")
            self.context_tags = None
        else:
            self.logger.info(f"Will fetch context with tags: {self.context_tags}")

        # --- Template Items (from message_config, for _create_stt_message) ---
        self.template_items = None
        if self.message_config.get("enable_template_info", False):
            self.template_items = self.message_config.get("template_items", {})
            if not self.template_items:
                self.logger.warning("配置启用了 template_info，但在 message_config 中未找到 template_items。")

        # --- iFlytek Config Check ---
        if not all([self.iflytek_config.get(k) for k in ["appid", "api_key", "api_secret", "host", "path"]]):
            self.logger.error("讯飞 ASR 配置不完整 (appid, api_key, api_secret, host, path)，STT 插件禁用。")
            return

        # --- VAD Model Loading ---
        self.vad_enabled = self.vad_config.get("enable", True)
        self.vad_model = None
        self.vad_utils = None  # Silero VAD specific utils, might not be needed directly
        if self.vad_enabled:
            try:
                self.logger.info("加载 Silero VAD 模型... (trust_repo=True)")

                # 解决中文用户名路径编码问题：设置自定义缓存目录
                original_hub_dir = torch.hub.get_dir()
                safe_cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".torch_cache")

                # 确保缓存目录存在
                os.makedirs(safe_cache_dir, exist_ok=True)

                # 临时设置torch.hub缓存目录到安全路径
                torch.hub.set_dir(safe_cache_dir)

                try:
                    # This loads the VAD model itself
                    self.vad_model, self.vad_utils = torch.hub.load(
                        repo_or_dir="snakers4/silero-vad",
                        model="silero_vad",
                        source="github",  # Explicitly specify source to avoid confusion
                        force_reload=False,
                        onnx=False,  # Assuming we want PyTorch version
                        trust_repo=True,
                        skip_validation=True,
                    )
                finally:
                    # 恢复原始缓存目录设置
                    torch.hub.set_dir(original_hub_dir)
                # Unpack utils if needed later, but might not be necessary for basic VAD
                # (get_speech_timestamps, save_audio, read_audio, VADIterator, collect_chunks) = self.vad_utils
                self.logger.info("Silero VAD 模型加载成功。")
            except Exception as e:
                self.logger.error(f"加载 Silero VAD 模型失败: {e}", exc_info=True)
                try:
                    self.vad_model, self.vad_utils = torch.hub.load(
                        repo_or_dir=os.path.join(safe_cache_dir, "snakers4_silero-vad_master"),
                        model="silero_vad",
                        source="local",  # Explicitly specify source to avoid confusion
                        force_reload=False,
                        onnx=False,  # Assuming we want PyTorch version
                        trust_repo=True,
                        skip_validation=True,
                    )
                    self.logger.info("Silero VAD 模型加载成功 (本地缓存)。")
                except Exception:
                    self.logger.warning("VAD 功能将不可用，禁用 STT 插件。")
                self.vad_enabled = False
        else:
            self.logger.info("VAD 在配置中被禁用，无法运行真流式 STT，禁用插件。")
            self.vad_enabled = False  # Keep this to indicate VAD is off

        # --- Audio Config ---
        self.sample_rate = self.audio_config.get(
            "sample_rate", 16000
        )  # Ensure this matches Iflytek's requirement (16k or 8k)
        if self.sample_rate not in [8000, 16000]:
            self.logger.warning(f"Sample rate {self.sample_rate} is not 8000 or 16000, using 16000.")
            self.sample_rate = 16000
        self.channels = self.audio_config.get("channels", 1)
        self.dtype_str = self.audio_config.get("dtype", "int16")  # Iflytek expects int16 (L16)
        self.dtype = np.int16 if self.dtype_str == "int16" else np.float32
        self.input_device_name = self.audio_config.get("stt_input_device_name") or None
        self.input_device_index = self._find_device_index(self.input_device_name, kind="input")

        # --- VAD Parameters ---
        # Frame size must match Silero VAD requirements
        if self.sample_rate == 16000:
            self.block_size_samples = 512
        elif self.sample_rate == 8000:
            self.block_size_samples = 256
        else:
            # Should have been corrected earlier, but set a default just in case
            self.logger.error(
                f"Unsupported sample rate {self.sample_rate} for VAD block size calculation. Falling back to 16kHz/512 samples."
            )
            self.sample_rate = 16000  # Correct sample rate if inconsistent
            self.block_size_samples = 512

        # Calculate block_size_ms based on the required sample count
        self.block_size_ms = int(self.block_size_samples * 1000 / self.sample_rate)
        self.logger.info(
            f"Using VAD block size: {self.block_size_samples} samples ({self.block_size_ms} ms) for sample rate {self.sample_rate}"
        )

        # These might not be strictly needed anymore if processing chunk by chunk
        # self.speech_buffer_duration = 0.2
        # self.speech_buffer_frames = int(self.speech_buffer_duration * self.sample_rate / self.block_size_samples)

        self.min_silence_duration_ms = int(self.vad_config.get("silence_seconds", 1.0) * 1000)
        # self.min_speech_duration_ms = 250 # Not used in this streaming logic
        # self.max_record_seconds = self.vad_config.get("max_record_seconds", 15.0) # Not used
        self.vad_threshold = self.vad_config.get("vad_threshold", 0.5)

        # --- Control Flow & State ---
        self._stt_task: Optional[asyncio.Task] = None
        self.stop_event = asyncio.Event()
        self._internal_audio_queue = asyncio.Queue()  # Internal queue for audio chunks

        # WebSocket and Session state variables
        self._session = None
        self._active_ws = None
        self._active_receiver_task = None
        self._is_speaking: bool = False
        self._silence_started_time: Optional[float] = None

        # --- Remote Stream 支持 ---
        self.use_remote_stream = self.audio_config.get("use_remote_stream", False) and REMOTE_STREAM_AVAILABLE
        self.remote_stream_service = None
        self.remote_audio_received = False  # 标记是否已从远程接收到音频

        self.logger.info(f"STT 插件初始化完成。VAD Enabled={self.vad_enabled}, Remote Stream={self.use_remote_stream}")

    def _find_device_index(self, device_name: Optional[str], kind: str = "input") -> Optional[int]:
        """根据设备名称查找设备索引。"""
        if sd is None:
            self.logger.error("sounddevice library not available.")
            return None
        try:
            devices = sd.query_devices()
            if device_name:
                for i, device in enumerate(devices):
                    if device_name.lower() in device["name"].lower() and device[f"max_{kind}_channels"] > 0:
                        self.logger.info(f"找到 {kind} 设备 '{device['name']}' (匹配 '{device_name}')，索引: {i}")
                        return i
                self.logger.warning(f"未找到名称包含 '{device_name}' 的 {kind} 设备，将使用默认设备。")

            default_device_indices = sd.default.device
            default_index = default_device_indices[0] if kind == "input" else default_device_indices[1]

            if default_index == -1:
                self.logger.warning(f"未找到默认 {kind} 设备。将尝试使用 None (由 sounddevice 选择)。")
                return None

            default_device_info = sd.query_devices(default_index)
            if default_device_info[f"max_{kind}_channels"] > 0:
                self.logger.info(f"使用默认 {kind} 设备索引: {default_index} ({default_device_info['name']})")
                return default_index
            else:
                self.logger.warning(
                    f"默认设备 {default_index} ({default_device_info['name']}) 没有 {kind} 通道。尝试使用 None。"
                )
                return None

        except Exception as e:
            self.logger.error(f"查找音频设备时出错: {e}", exc_info=True)
            return None

    async def setup(self):
        """启动 STT 主任务。"""
        await super().setup()
        if not self.vad_enabled:
            self.logger.warning("VAD is not enabled or failed to load. STT plugin will not run.")
            return

        # 注册 Remote Stream 音频回调（如果启用）
        print(self.use_remote_stream)
        if self.use_remote_stream:
            try:
                # 获取 remote_stream 服务
                remote_stream_service = self.core.get_service("remote_stream")
                if remote_stream_service:
                    self.remote_stream_service = remote_stream_service
                    # 注册音频数据回调
                    self.remote_stream_service.register_audio_callback("data", self._handle_remote_audio)
                    self.logger.info("成功注册 Remote Stream 音频回调")
                else:
                    self.logger.warning("未找到 Remote Stream 服务，将使用本地麦克风")
                    self.use_remote_stream = False
            except Exception as e:
                self.logger.error(f"注册 Remote Stream 回调失败: {e}")
                self.use_remote_stream = False

        # 启动后台任务
        self._stt_task = asyncio.create_task(self._stt_worker())

    async def cleanup(self):
        """停止 STT 任务并清理资源。"""
        self.logger.info("请求停止 STT 插件...")
        self.stop_event.set()

        # 取消注册 Remote Stream 回调
        if self.use_remote_stream and self.remote_stream_service:
            try:
                self.remote_stream_service.unregister_audio_callback("data", self._handle_remote_audio)
                self.logger.info("已取消注册 Remote Stream 音频回调")
            except Exception as e:
                self.logger.warning(f"取消注册 Remote Stream 回调失败: {e}")

        stt_task_to_wait = self._stt_task
        self._stt_task = None

        if stt_task_to_wait and not stt_task_to_wait.done():
            self.logger.info("正在等待 STT worker 任务结束 (最多 5 秒)...")
            try:
                await asyncio.wait_for(stt_task_to_wait, timeout=5.0)
                self.logger.info("STT worker 任务正常结束。")
            except asyncio.TimeoutError:
                self.logger.warning("STT worker 任务在超时后仍未结束，将强制取消。")
                stt_task_to_wait.cancel()
                try:
                    await stt_task_to_wait
                except asyncio.CancelledError:
                    self.logger.info("STT worker 任务被成功取消。")
            except asyncio.CancelledError:
                self.logger.info("STT worker 任务已被取消 (在等待前)。")
            except Exception as e:
                self.logger.error(f"等待 STT worker 任务结束时出错: {e}", exc_info=True)

        self.logger.debug("清理 WebSocket 连接和 aiohttp session...")
        await self._close_iflytek_connection(send_last_frame=False)

        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("Aiohttp session 已关闭。")
            self._session = None

        self._active_ws = None
        self._active_receiver_task = None
        self._is_speaking = False
        self._silence_started_time = None

        self.logger.info("STT 插件清理完成。")
        await super().cleanup()

    def _handle_remote_audio(self, audio_data: Dict[str, Any]):
        """处理从 Remote Stream 接收到的音频数据

        Args:
            audio_data: 音频数据字典，包含 'binary' 键，值为音频二进制数据
        """
        if not audio_data or "binary" not in audio_data:
            self.logger.warning("接收到的远程音频数据无效或不包含二进制数据")
            return

        try:
            # 获取二进制音频数据
            audio_bytes = audio_data["binary"]

            # 检查音频格式（如果提供）并转换
            format_info = audio_data.get("format", {})
            sample_rate = format_info.get("sample_rate", self.sample_rate)

            # 如果采样率不匹配，记录警告（未来可以添加重采样功能）
            if sample_rate != self.sample_rate:
                self.logger.warning(
                    f"远程音频采样率 ({sample_rate}) 与本地设置 ({self.sample_rate}) 不匹配，可能影响识别质量"
                )

            # 标记已从远程接收到音频
            self.remote_audio_received = True

            # 将音频数据放入内部队列进行处理
            # 使用 call_soon_threadsafe 确保线程安全，因为回调可能在不同的线程中执行
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(self._internal_audio_queue.put_nowait, audio_bytes)
                # self.logger.debug(f"已将 {len(audio_bytes)} 字节远程音频数据加入处理队列")
            except RuntimeError:
                # 如果没有运行的事件循环，尝试直接放入队列（虽然不太安全）
                try:
                    self._internal_audio_queue.put_nowait(audio_bytes)
                except asyncio.QueueFull:
                    # 队列满，丢弃数据
                    pass
            except asyncio.QueueFull:
                # 队列满，丢弃数据（这是正常的，处理速度可能慢于输入）
                # self.logger.debug("内部音频队列已满，丢弃远程音频数据")
                pass

        except Exception as e:
            self.logger.error(f"处理远程音频数据时出错: {e}", exc_info=True)

    # --- WebSocket Connection Management Helpers (New for True Streaming) ---

    async def _ensure_iflytek_connection(self) -> bool:
        """
        确保存在一个活动的、有效的讯飞 WebSocket 连接。
        如果连接不存在或已关闭/接收器未运行，则尝试建立新连接并启动接收器。
        返回 True 表示连接成功或已存在且可用，False 表示连接失败。
        """
        # Check if existing connection is active and receiver is running
        if self._active_ws and not self._active_ws.closed:
            if self._active_receiver_task and not self._active_receiver_task.done():
                return True  # Connection is valid
            else:
                self.logger.warning("WebSocket 存在但接收器任务未运行，将尝试重新连接。")
                # Use the new close function to clean up
                await self._close_iflytek_connection(send_last_frame=False)

        # Ensure session exists
        if not self._session or self._session.closed:
            self.logger.warning("Aiohttp session 已关闭或未初始化，正在重新创建。")
            try:
                self._session = aiohttp.ClientSession()
                self.logger.info("已为 STT 创建新的 aiohttp session。")
            except Exception as e:
                self.logger.error(f"创建 aiohttp session 失败: {e}", exc_info=True)
                return False  # Cannot proceed

        # Attempt to establish new connection
        try:
            auth_url = self._build_iflytek_auth_url()  # Use existing auth URL builder
            self.logger.info("尝试连接到讯飞 WebSocket...")
            # Specify SSL context explicitly for wss
            ssl_context = ssl.create_default_context()
            self._active_ws = await self._session.ws_connect(
                auth_url,
                autoping=True,
                heartbeat=30,
                ssl=ssl_context,  # Use explicit SSL context
            )
            self.logger.info("成功连接到讯飞 WebSocket。")

            # Ensure previous receiver task is cancelled before starting new one
            if self._active_receiver_task and not self._active_receiver_task.done():
                self.logger.warning("发现残留的接收器任务，正在取消...")
                self._active_receiver_task.cancel()
                try:
                    await asyncio.wait_for(self._active_receiver_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass  # Ignore errors during cleanup of old task

            # Start the new receiver task
            self._active_receiver_task = asyncio.create_task(
                self._iflytek_receiver(self._active_ws), name="IflytekReceiver"
            )
            self.logger.info("已启动讯飞接收器任务。")
            return True  # Connection and receiver started successfully

        except ValueError as e:  # Catch config errors from auth URL build
            self.logger.error(f"构建认证 URL 失败 (检查 API 配置): {e}")
        except aiohttp.ClientConnectorError as e:
            self.logger.error(f"连接讯飞 WebSocket 失败: 连接错误 - {e}")
        except aiohttp.WSServerHandshakeError as e:
            self.logger.error(f"连接讯飞 WebSocket 失败: 握手错误 - Status: {e.status}, Message: {e.message}")
        except Exception as e:
            self.logger.exception(f"建立讯飞 WebSocket 连接时发生未知错误: {e}")

        # --- Cleanup partially created resources on failure --- (Corrected Structure)
        self.logger.debug("连接尝试失败，清理可能存在的局部资源...")
        if self._active_ws and not self._active_ws.closed:
            try:
                await self._active_ws.close()
                self.logger.debug("在连接/启动接收器失败后关闭了部分创建的 WebSocket 连接。")
            except Exception as close_err:
                self.logger.error(f"尝试关闭部分创建的 WebSocket 时出错: {close_err}")
        self._active_ws = None
        if self._active_receiver_task and not self._active_receiver_task.done():
            self._active_receiver_task.cancel()
        self._active_receiver_task = None

        return False  # Explicitly return False indicating connection failure

    # This is the NEW close function for the true streaming approach
    async def _close_iflytek_connection(self, send_last_frame: bool = True):
        """安全地关闭当前的讯飞 WebSocket 连接和关联的任务。"""
        ws_to_close = self._active_ws
        receiver_task_to_await = self._active_receiver_task

        # Reset instance variables immediately
        self._active_ws = None
        self._active_receiver_task = None

        if not ws_to_close:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()
            return

        if ws_to_close.closed:
            self.logger.debug("调用关闭连接，但 WS 已关闭。")
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()
            return

        self.logger.info(f"正在关闭讯飞 WebSocket 连接 (发送结束帧: {send_last_frame})...")

        try:
            if send_last_frame:
                try:
                    last_frame = self._build_iflytek_frame(STATUS_LAST_FRAME)  # Build frame using instance method
                    await asyncio.wait_for(ws_to_close.send_bytes(last_frame), timeout=2.0)
                    self.logger.debug("已发送结束帧 (STATUS_LAST_FRAME) 到讯飞。")
                except ConnectionResetError:
                    self.logger.warning("尝试发送结束帧时连接已重置。")
                except asyncio.TimeoutError:
                    self.logger.warning("发送结束帧超时。")
                except Exception as send_err:
                    self.logger.error(f"发送结束帧时出错: {send_err}", exc_info=True)

            # Wait briefly for the receiver task
            if receiver_task_to_await and not receiver_task_to_await.done():
                self.logger.debug("等待接收器任务完成 (最多 1 秒)...")
                try:
                    await asyncio.wait_for(receiver_task_to_await, timeout=1.0)
                    self.logger.debug("接收器任务完成。")
                except asyncio.TimeoutError:
                    self.logger.warning("等待接收器任务完成超时，正在取消任务。")
                    receiver_task_to_await.cancel()
                except asyncio.CancelledError:
                    self.logger.info("等待期间接收器任务已被取消。")
                except Exception as e:
                    self.logger.exception(f"等待接收器任务时出错: {e}")

            # Close the WebSocket
            await ws_to_close.close()
            self.logger.info(f"讯飞 WebSocket 连接已成功关闭 (代码: {ws_to_close.close_code})。")

        except asyncio.CancelledError:
            self.logger.info("连接关闭任务被取消。")
            if not ws_to_close.closed:
                await ws_to_close.close()  # Ensure close
        except Exception as e:
            self.logger.exception(f"关闭讯飞连接过程中出错: {e}")
        finally:
            # Final safety net: ensure task is cancelled
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()

    # --- Main Worker (New for True Streaming) ---
    async def _stt_worker(self):
        """
        Main worker loop: Captures audio via sounddevice or remote stream, puts chunks into an internal queue,
        processes chunks with VAD, manages WebSocket connection, sends audio stream,
        and the receiver sends final recognized text back to Core.
        """
        if not self.vad_model:  # VAD check already done in setup, but double-check
            self.logger.critical("VAD model not loaded. STT worker cannot run.")
            return

        loop = asyncio.get_event_loop()
        stream = None
        self._internal_audio_queue = asyncio.Queue()  # Ensure queue is created

        # 只有在不使用远程流时才启动本地麦克风捕获
        if not self.use_remote_stream:

            def audio_callback(indata, frame_count, time_info, status):
                """Callback function for sounddevice stream. Puts raw bytes into queue."""
                if status:
                    self.logger.warning(f"Audio input status: {status}")
                try:
                    # Convert numpy array to bytes (int16 is expected by Iflytek)
                    if indata.dtype == np.float32:
                        indata_int16 = (indata * 32767.0).astype(np.int16)
                    elif indata.dtype == np.int16:
                        indata_int16 = indata
                    else:
                        self.logger.warning(f"Unsupported input dtype {indata.dtype}, attempting cast to int16.")
                        indata_int16 = indata.astype(np.int16)

                    # Ensure mono if necessary
                    if indata_int16.ndim > 1 and self.channels == 1:
                        audio_bytes = indata_int16[:, 0].tobytes()
                    elif indata_int16.ndim == 1 and self.channels == 1:
                        audio_bytes = indata_int16.tobytes()
                    else:
                        self.logger.warning(
                            f"Unexpected audio dimensions: {indata_int16.shape} for channels: {self.channels}"
                        )
                        # Attempt to take first channel if stereo but configured mono
                        if indata_int16.ndim > 1:
                            audio_bytes = indata_int16[:, 0].tobytes()
                        else:
                            audio_bytes = indata_int16.tobytes()  # Fallback

                    # Put data into the internal queue
                    loop.call_soon_threadsafe(self._internal_audio_queue.put_nowait, audio_bytes)
                except asyncio.QueueFull:
                    # This is okay, means processing is slower than input, VAD will handle segments
                    # self.logger.warning("Internal STT audio queue full! Discarding data.") # Too noisy
                    pass
                except Exception as e:
                    self.logger.error(f"Error in audio callback: {e}", exc_info=True)

        try:
            # --- Start Audio Stream (仅在不使用远程流时) ---
            if not self.use_remote_stream:
                self.logger.info("正在启动本地麦克风输入流...")
                stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.block_size_samples,
                    device=self.input_device_index,
                    channels=self.channels,
                    dtype=self.dtype_str,  # Use configured dtype for input
                    callback=audio_callback,
                )
                stream.start()
                self.logger.info(f"本地音频流已启动。监听语音中 (VAD 阈值: {self.vad_threshold})...")
            else:
                self.logger.info(f"使用远程音频流模式，等待远程音频输入 (VAD 阈值: {self.vad_threshold})...")

            # --- Processing Loop --- (Reads from internal queue)
            speech_chunk_count = 0  # Track chunks sent in current utterance
            # Timeout should be slightly less than silence threshold for responsiveness
            timeout_duration = max(0.1, self.min_silence_duration_ms / 1000.0 * 0.8)

            while not self.stop_event.is_set():
                try:
                    # Wait for audio chunk from the internal queue with timeout
                    audio_chunk_bytes = await asyncio.wait_for(
                        self._internal_audio_queue.get(), timeout=timeout_duration
                    )
                    self._internal_audio_queue.task_done()

                except asyncio.TimeoutError:
                    # 在远程流模式下，如果长时间没收到远程音频，记录一下日志
                    if self.use_remote_stream and not self.remote_audio_received:
                        self.logger.debug("正在等待远程音频数据...")

                    # Timeout: indicates potential end of speech due to lack of audio
                    if self._is_speaking:
                        # If we were speaking, timeout marks the *start* of silence
                        self.logger.debug("Audio queue timeout while speaking, assuming silence start.")
                        self._is_speaking = False
                        self._silence_started_time = time.monotonic()
                        # Continue loop to check silence duration on next iteration or timeout

                    # Check if silence duration threshold has been reached *now*
                    if self._silence_started_time is not None and (
                        time.monotonic() - self._silence_started_time > self.min_silence_duration_ms / 1000.0
                    ):
                        # Silence duration threshold reached
                        if self._active_ws:
                            self.logger.info(
                                f"Silence duration exceeded threshold ({self.min_silence_duration_ms}ms). Ending utterance via timeout."
                            )
                            # Only close if we actually sent something
                            if speech_chunk_count > 0:
                                await self._close_iflytek_connection(send_last_frame=True)
                            else:
                                self.logger.info(
                                    "Silence threshold reached but no speech chunks were sent. Closing connection silently."
                                )
                                await self._close_iflytek_connection(send_last_frame=False)
                            speech_chunk_count = 0  # Reset count
                            self._is_speaking = False  # Ensure state reset
                        # else: No active connection, just reset timer below
                        self._silence_started_time = None  # Reset timer after handling
                    # Continue loop after timeout handling
                    continue
                except asyncio.CancelledError:
                    self.logger.info("Internal audio queue get cancelled.")
                    break  # Exit loop if queue get is cancelled

                # --- VAD Check --- (Process the received audio chunk)
                try:
                    # Convert bytes back to numpy array (int16) for VAD model
                    audio_np = np.frombuffer(audio_chunk_bytes, dtype=np.int16)
                    # Convert to float32 for Silero VAD
                    audio_float32 = audio_np.astype(np.float32) / 32768.0
                    if audio_float32.ndim == 0 or audio_float32.size == 0:
                        # self.logger.warning("Empty audio chunk after conversion, skipping VAD.")
                        continue  # Skip empty chunks
                    audio_tensor = torch.from_numpy(audio_float32)

                    # Perform VAD check
                    speech_prob = self.vad_model(audio_tensor, self.sample_rate).item()
                    is_speech = speech_prob > self.vad_threshold

                except Exception as e:
                    self.logger.exception(f"VAD processing error: {e}. Skipping chunk.")
                    continue

                now = time.monotonic()

                # --- State Machine Logic --- (Same as before)
                if is_speech:
                    # --- Detected Speech ---
                    if not self._is_speaking:
                        # -- Transition: Silence -> Speech (Utterance Start) --
                        self.logger.info(f"VAD: Speech started (Prob: {speech_prob:.2f})")
                        self._is_speaking = True
                        self._silence_started_time = None  # Clear silence timer
                        speech_chunk_count = 0  # Reset count for new utterance

                        # Ensure connection exists or establish one
                        if not await self._ensure_iflytek_connection():
                            self.logger.error("Failed to establish STT connection, cannot start utterance.")
                            self._is_speaking = False  # Reset state
                            continue  # Skip this chunk

                        # Send First Frame (Contains the first speech chunk)
                        if self._active_ws:
                            try:
                                first_frame = self._build_iflytek_frame(STATUS_FIRST_FRAME, audio_chunk_bytes)
                                await asyncio.wait_for(self._active_ws.send_bytes(first_frame), timeout=2.0)
                                speech_chunk_count += 1  # Increment count after sending first frame
                                self.logger.debug("Sent first frame with audio.")
                            except asyncio.TimeoutError:
                                self.logger.warning("Timeout sending first frame.")
                                await self._close_iflytek_connection(send_last_frame=False)
                                self._is_speaking = False
                                speech_chunk_count = 0
                                continue
                            except Exception as e:
                                self.logger.exception(f"Error sending first frame: {e}. Closing connection.")
                                await self._close_iflytek_connection(send_last_frame=False)
                                self._is_speaking = False
                                speech_chunk_count = 0
                                continue
                        else:  # Should not happen if ensure_connection worked
                            self.logger.error("_ensure_iflytek_connection returned True but _active_ws is None!")
                            self._is_speaking = False
                            continue

                    # -- State: Speaking (Continuing Speech) --
                    # Only send if it's NOT the first chunk (already sent with first_frame)
                    elif speech_chunk_count > 0 and self._active_ws and not self._active_ws.closed:
                        try:
                            data_frame = self._build_iflytek_frame(STATUS_CONTINUE_FRAME, audio_chunk_bytes)
                            await asyncio.wait_for(self._active_ws.send_bytes(data_frame), timeout=1.0)
                            speech_chunk_count += 1
                            # self.logger.debug(f"Sent audio frame {speech_chunk_count}.") # Verbose
                        except asyncio.TimeoutError:
                            self.logger.warning("Timeout sending audio frame.")
                            await self._close_iflytek_connection(send_last_frame=False)
                            self._is_speaking = False
                            speech_chunk_count = 0
                        except ConnectionResetError:
                            self.logger.warning("Connection reset while trying to send audio frame. Closing locally.")
                            await self._close_iflytek_connection(send_last_frame=False)
                            self._is_speaking = False
                            speech_chunk_count = 0
                        except Exception as e:
                            self.logger.exception(f"Error sending audio frame: {e}. Closing connection.")
                            await self._close_iflytek_connection(send_last_frame=False)
                            self._is_speaking = False
                            speech_chunk_count = 0
                    elif self._is_speaking:
                        # Defensive check if connection dropped mid-speech
                        self.logger.warning("Detected speech but STT connection is not active.")
                        self._is_speaking = False
                        speech_chunk_count = 0

                else:
                    # --- Detected Silence --- (Logic mostly same)
                    if self._is_speaking:
                        # -- Transition: Speech -> Silence --
                        self.logger.debug("VAD: Speech ended (Silence detected)")
                        self._is_speaking = False
                        self._silence_started_time = now  # Start timing silence

                        # Send this silent chunk
                        if self._active_ws and not self._active_ws.closed:
                            try:
                                data_frame = self._build_iflytek_frame(STATUS_CONTINUE_FRAME, audio_chunk_bytes)
                                await asyncio.wait_for(self._active_ws.send_bytes(data_frame), timeout=1.0)
                            except asyncio.TimeoutError:
                                self.logger.warning("Timeout sending silent frame.")
                                await self._close_iflytek_connection(send_last_frame=False)
                                self._silence_started_time = None
                                speech_chunk_count = 0
                            except ConnectionResetError:
                                self.logger.warning("Connection reset sending silent frame. Closing locally.")
                                await self._close_iflytek_connection(send_last_frame=False)
                                self._silence_started_time = None
                                speech_chunk_count = 0
                            except Exception as e:
                                self.logger.exception(f"Error sending silent frame: {e}. Closing connection.")
                                await self._close_iflytek_connection(send_last_frame=False)
                                self._silence_started_time = None
                                speech_chunk_count = 0

                    # -- State: Silence (Continuing Silence after Speech) --
                    elif self._silence_started_time is not None:
                        # Check if silence duration threshold reached
                        if now - self._silence_started_time > self.min_silence_duration_ms / 1000.0:
                            if self._active_ws:
                                self.logger.info(
                                    f"Silence duration threshold ({self.min_silence_duration_ms}ms) reached after silence chunk. Ending utterance."
                                )
                                if speech_chunk_count > 0:
                                    await self._close_iflytek_connection(send_last_frame=True)
                                else:
                                    self.logger.info("Silence after VAD trigger, but no speech sent. Closing silently.")
                                    await self._close_iflytek_connection(send_last_frame=False)
                                speech_chunk_count = 0
                            self._silence_started_time = None  # Reset timer after handling

        except sd.PortAudioError as pae:
            self.logger.error(f"PortAudio error: {pae}")
        except asyncio.CancelledError:
            self.logger.info("STT worker task explicitly cancelled.")
        except Exception as e:
            self.logger.exception(f"Fatal error in STT worker loop: {e}")
        finally:
            self.logger.info("STT worker循环结束。清理资源...")
            # 停止音频流（如果启用了本地麦克风）
            if not self.use_remote_stream and stream is not None:
                try:
                    stream.stop()
                    stream.close()
                    self.logger.debug("本地麦克风流已停止并关闭。")
                except Exception as sd_err:
                    self.logger.error(f"停止本地麦克风流时出错: {sd_err}", exc_info=True)
            # 确保连接已关闭
            await self._close_iflytek_connection(send_last_frame=False)
            self.logger.info("STT worker已完成。")

    # --- Iflytek Receiver (Modified in previous step) ---
    async def _iflytek_receiver(self, ws: aiohttp.ClientWebSocketResponse):
        """
        Receives messages from iFlytek WebSocket, processes results,
        and sends the final text back to Core via send_to_maicore.
        """
        utterance_failed = False  # Track if the utterance had errors
        self.logger.debug("讯飞接收器任务启动。")
        self.full_text = ""
        try:
            async for msg in ws:
                if self.stop_event.is_set():  # Check for plugin stop signal
                    self.logger.info("Receiver detected stop event.")
                    break

                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        resp = json.loads(msg.data)
                        if resp.get("code", -1) != 0:
                            err_msg = f"讯飞 API 错误: Code={resp.get('code')}, Message={resp.get('message')}"
                            self.logger.error(err_msg)
                            utterance_failed = True
                            break  # Stop processing this utterance

                        data = resp.get("data", {})
                        status = data.get("status", -1)
                        result = data.get("result", {})
                        if hasattr(self, "full_text") and status == STATUS_LAST_FRAME:
                            full_text = self.full_text.strip()  # Clean up whitespace
                        self.full_text = ""

                        # Extract text segments correctly
                        if "ws" in result:
                            for w in result["ws"]:
                                for cw in w.get("cw", []):
                                    self.full_text += cw.get("w", "")
                        if status == STATUS_LAST_FRAME:
                            full_text += self.full_text

                            # self.logger.debug(f"Intermediate text: '{text_segment}' (Total: '{full_text}')") # Optional: verbose
                            self.logger.info(f"讯飞收到最终结果: '{full_text}'")
                            if full_text.strip() and not utterance_failed:  # Only send non-empty, non-failed results
                                try:
                                    # --- 修正服务调用 (Re-inserted from old logic) ---
                                    final_text_to_send = full_text.strip()
                                    if self.enable_correction:
                                        correction_service = self.core.get_service("stt_correction")
                                        if correction_service:
                                            self.logger.debug("找到 stt_correction 服务，尝试修正文本...")
                                            try:
                                                # Use the stripped full_text as input
                                                corrected = await correction_service.correct_text(final_text_to_send)
                                                if corrected and isinstance(corrected, str):
                                                    self.logger.info(f"修正后 STT 结果: '{corrected}'")
                                                    final_text_to_send = corrected  # Use corrected text
                                                elif corrected:
                                                    self.logger.warning(
                                                        f"STT 修正服务返回了非字符串结果 ({type(corrected)})，使用原始文本。"
                                                    )
                                                else:
                                                    self.logger.info("STT 修正服务未返回有效结果，使用原始文本。")
                                            except AttributeError:
                                                self.logger.error(
                                                    "获取到的 'stt_correction' 服务没有 'correct_text' 方法。"
                                                )
                                            except Exception as correct_err:
                                                self.logger.error(
                                                    f"调用 stt_correction 服务时出错: {correct_err}", exc_info=True
                                                )
                                        else:
                                            self.logger.warning("配置启用了 STT 修正，但未找到 'stt_correction' 服务。")
                                    # --- 使用 (可能) 修正后的文本发送消息到 Core ---
                                    if "none" in final_text_to_send.lower():
                                        self.logger.warning("识别结果为空，不发送。")
                                        break
                                    message_to_send = await self._create_stt_message(final_text_to_send)
                                    self.logger.debug(f"准备发送 STT 消息对象到 Core: {repr(message_to_send)}")
                                    await self.core.send_to_maicore(message_to_send)
                                    self.logger.info(
                                        f"STT 结果已发送到 Core: {message_to_send.message_info.message_id}"
                                    )
                                except Exception as send_err:
                                    self.logger.error(f"创建或发送 STT 结果到 Core 时出错: {send_err}", exc_info=True)
                            elif utterance_failed:
                                self.logger.warning("Utterance failed due to API error, not sending result.")
                            else:
                                self.logger.info("最终识别结果为空，不发送。")
                            # full_text = "" # Reset unnecessary as connection will close
                            break  # End receiving loop for this utterance

                    except json.JSONDecodeError:
                        self.logger.error(f"无法解码来自讯飞的 JSON: {msg.data}")
                        utterance_failed = True  # Mark as failed
                        break
                    except Exception as e:
                        self.logger.exception(f"处理讯飞消息时出错: {e}")
                        utterance_failed = True  # Mark as failed
                        break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    err = ws.exception() or RuntimeError("讯飞 WebSocket 错误")
                    self.logger.error(f"讯飞 WebSocket 错误: {err}")
                    utterance_failed = True  # Mark as failed
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    self.logger.warning(f"讯飞 WebSocket 连接被关闭: Code={ws.close_code}")
                    if not utterance_failed and full_text.strip():
                        self.logger.warning(f"在最终结果前连接关闭。最后累积文本: '{full_text.strip()}' (未发送)")
                        # Decide policy: maybe send partial? Currently not.
                    break
        except asyncio.CancelledError:
            self.logger.info("讯飞接收器任务被取消。")
        except Exception as e:
            self.logger.exception(f"讯飞接收器任务异常: {e}")
        finally:
            # Remove future logic
            self.logger.debug(f"讯飞接收器任务结束 (Utterance failed: {utterance_failed})。")

    # --- Iflytek Frame Builders and Auth (Keep as is, using self.iflytek_config) ---

    def _build_iflytek_auth_url(self) -> str:
        # ... (Keep existing code)
        cfg = self.iflytek_config
        if not all([cfg.get(k) for k in ["host", "path", "api_secret", "api_key"]]):
            raise ValueError("Iflytek config missing host, path, api_key, or api_secret.")
        host = cfg["host"]
        path = cfg["path"]
        url = f"wss://{host}{path}"
        # Use UTC time for consistency
        date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature_sha = hmac.new(
            cfg["api_secret"].encode("utf-8"), signature_origin.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        signature_sha_base64 = base64.b64encode(signature_sha).decode(encoding="utf-8")
        authorization_origin = f'api_key="{cfg["api_key"]}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(encoding="utf-8")
        # URL encode the parameters
        v = {"authorization": authorization, "date": date, "host": host}
        signed_url = url + "?" + urlencode(v)  # No need for quote_via=quote with urlencode defaults
        self.logger.debug(f"Generated Iflytek auth URL (ends): ...?{urlencode(v)[-30:]}")
        return signed_url

    # --- _get_iflytek_authorization is implicitly kept as it's called by _build_iflytek_auth_url ---

    def _build_iflytek_common_params(self) -> dict:
        # ... (Keep existing code)
        return {"app_id": str(self.iflytek_config["appid"])}

    def _build_iflytek_business_params(self) -> dict:
        # ... (Keep existing code)
        return {
            "language": self.iflytek_config.get("language", "zh_cn"),
            "domain": self.iflytek_config.get("domain", "iat"),
            "accent": self.iflytek_config.get("accent", "mandarin"),
            "ptt": self.iflytek_config.get("ptt", 1),  # Punctuation default on
            "rlang": self.iflytek_config.get("rlang", "zh-cn"),  # Dynamic correction lang
            "vinfo": self.iflytek_config.get("vinfo", 1),  # Return time info default on
            "dwa": self.iflytek_config.get("dwa", "wpgs"),  # Dynamic correction enable keyword
            "vad_eos": self.iflytek_config.get("vad_eos", self.min_silence_duration_ms),  # Server VAD timeout
            "nunum": 0,  # Disable number conversion
        }

    def _build_iflytek_data_params(self, status: int, audio_chunk_bytes: bytes = b"") -> dict:
        # ... (Keep existing code)
        return {
            "status": status,
            "format": f"audio/L16;rate={self.sample_rate}",  # Ensure rate matches input
            "encoding": "raw",
            "audio": base64.b64encode(audio_chunk_bytes).decode("utf-8"),
        }

    def _build_iflytek_frame(self, status: int, audio_chunk_bytes: bytes = b"") -> bytes:
        # ... (Keep existing code)
        frame = {
            "common": self._build_iflytek_common_params(),
            "business": self._build_iflytek_business_params(),
            "data": self._build_iflytek_data_params(status, audio_chunk_bytes),
        }
        return json.dumps(frame).encode("utf-8")

    # --- Message Creation Helper (Keep as is, uses self.message_config) ---
    async def _create_stt_message(self, text: str) -> MessageBase:
        # ... (Keep existing code from previous version) ...
        """使用从 config.toml 加载的 [message_config] 创建 MessageBase 对象。"""
        timestamp = time.time()
        cfg = self.message_config

        # --- User Info ---
        user_id_from_config = cfg.get("user_id", "stt_user")  # Use string ID consistently
        user_info = UserInfo(
            platform=self.core.platform,
            user_id=str(user_id_from_config),  # Ensure string
            user_nickname=cfg.get("user_nickname", "语音"),
            user_cardname=cfg.get("user_cardname", ""),
        )

        # --- Group Info (Conditional) ---
        group_info: Optional[GroupInfo] = None
        if cfg.get("enable_group_info", False):  # Default to False unless specified
            group_id_from_config = cfg.get("group_id", "stt_group")  # Use string ID
            group_info = GroupInfo(
                platform=self.core.platform,
                group_id=str(group_id_from_config),  # Ensure string
                group_name=cfg.get("group_name", "stt_default"),
            )

        # --- Format Info ---
        format_info = FormatInfo(
            content_format=cfg.get("content_format", ["text"]),
            accept_format=cfg.get("accept_format", ["text", "vts_command"]),
        )

        # --- Template Info (Conditional & Modification) ---
        final_template_info_value = None
        if cfg.get("enable_template_info", False) and self.template_items:
            modified_template_items = (self.template_items or {}).copy()
            additional_context = ""
            prompt_ctx_service = self.core.get_service("prompt_context")
            if prompt_ctx_service:
                try:
                    additional_context = await prompt_ctx_service.get_formatted_context(tags=self.context_tags)
                    if additional_context:
                        self.logger.debug(f"获取到聚合 Prompt 上下文: '{additional_context[:100]}...'")
                except Exception as e:
                    self.logger.error(f"调用 prompt_context 服务时出错: {e}", exc_info=True)

            main_prompt_key = cfg.get("main_prompt_key", "reasoning_prompt_main")  # Allow config key override
            if additional_context and main_prompt_key in modified_template_items:
                original_prompt = modified_template_items[main_prompt_key]
                modified_template_items[main_prompt_key] = original_prompt + "\n" + additional_context
                self.logger.debug(f"已将聚合上下文追加到 '{main_prompt_key}'。")

            final_template_info_value = {"template_items": modified_template_items}

        # --- Additional Config ---
        additional_config = cfg.get("additional_config", {}).copy()
        additional_config["source"] = "stt_plugin"  # Identify source
        additional_config["sender_name"] = user_info.user_nickname  # Convenience

        # --- Base Message Info ---
        message_info = BaseMessageInfo(
            platform=self.core.platform,
            message_id=f"stt_{int(timestamp * 1000)}_{hash(text) % 10000}",
            time=int(timestamp),
            user_info=user_info,
            group_info=group_info,
            template_info=final_template_info_value,
            format_info=format_info,
            additional_config=additional_config,
        )

        # --- Message Segment ---
        message_segment = Seg(
            "seglist",
            [
                Seg(type="text", data=text),
                Seg("priority_info", {"message_type": "normal", "priority": 0}),
            ],
        )

        # --- Final MessageBase ---
        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=text)


# --- Plugin Entry Point --- (Keep as is)
plugin_entrypoint = STTPlugin
