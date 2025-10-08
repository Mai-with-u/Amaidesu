# src/plugins/websocket_lip_sync/plugin.py

import asyncio
import json
from typing import Any, Dict, Optional
import numpy as np
import threading
from collections import deque
import websockets
from .action_sender import action_sender

# 尝试导入音频分析相关库
try:
    import librosa
    import scipy.signal
    AUDIO_ANALYSIS_AVAILABLE = True
except ImportError:
    librosa = None
    scipy = None
    AUDIO_ANALYSIS_AVAILABLE = False

# 从 core 导入基类和核心类
from core.plugin_manager import BasePlugin
from core.amaidesu_core import AmaidesuCore
from maim_message.message_base import MessageBase

# 导入状态管理模块
from .mai_state import WarudoStateManager, MoodStateManager
# 导入眨眼任务
from .small_actions.blink_action import BlinkTask
# 导入眼部移动任务
from .small_actions.shift_action import ShiftTask
# 导入回复状态管理器
from .reply_state import ReplyState

class WarudoPlugin(BasePlugin):
    """
    Analyzes audio from TTS and sends lip-sync parameters to a specified WebSocket address.
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.config = self.plugin_config

        # --- WebSocket 客户端配置 ---
        self.ws_host = self.config.get("ws_host", "localhost")
        self.ws_port = self.config.get("ws_port", 19190)
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._connection_task: Optional[asyncio.Task] = None
        self._is_connected = False

        # --- 口型同步相关配置 ---
        lip_sync_config = self.config.get("lip_sync", {})
        self.lip_sync_enabled = lip_sync_config.get("enabled", True)
        self.volume_threshold = lip_sync_config.get("volume_threshold", 0.01)
        self.smoothing_factor = lip_sync_config.get("smoothing_factor", 0.3)
        self.vowel_detection_sensitivity = lip_sync_config.get("vowel_detection_sensitivity", 0.5)
        self.sample_rate = lip_sync_config.get("sample_rate", 32000)
        self.buffer_size = lip_sync_config.get("buffer_size", 1024)
        # 音频超时配置：如果超过这个时间没有音频数据，就认为停止说话了
        self.audio_timeout = lip_sync_config.get("audio_timeout", 1.5)  # 0.5秒

        self.is_speaking = False     # 是否真正在处理音频数据
        self.reseted = False        # 是否重置了嘴部状态
        self.last_audio_time = 0     # 最后一次收到音频数据的时间
        
        # 口型同步状态变量
        self.audio_buffer = deque(maxlen=self.sample_rate * 2)  # 2秒音频缓存
        self.current_vowel_values = {"A": 0.0, "I": 0.0, "U": 0.0, "E": 0.0, "O": 0.0}
        self.current_volume = 0.0
        self.audio_analysis_lock = threading.Lock()
        self.accumulated_audio = bytearray()

        # 元音频率特征
        self.vowel_formants = {
            "A": [730, 1090], "I": [270, 2290], "U": [300, 870],
            "E": [530, 1840], "O": [570, 840],
        }

        # 检查音频分析依赖
        if self.lip_sync_enabled and not AUDIO_ANALYSIS_AVAILABLE:
            self.logger.warning(
                "Lip sync enabled but audio analysis libraries not available. Install with: pip install librosa scipy"
            )
            self.lip_sync_enabled = False
            
        # 初始化状态管理器
        self.state_manager = WarudoStateManager(self.logger)
        
        # 初始化心情管理器
        self.mood_manager = MoodStateManager(self.state_manager, self.logger)
        
        # 初始化眨眼任务
        self.blink_task = BlinkTask(self.state_manager, self.logger)
        
        # 初始化眼部移动任务
        self.shift_task = ShiftTask(self.state_manager, self.logger)
        
        self.reply_state = ReplyState(self.state_manager, self.logger)
        
        # --- 字幕相关配置 ---
        subtitle_config = self.config.get("subtitle", {})
        self.subtitle_enabled = subtitle_config.get("enabled", True)
        self.subtitle_port = subtitle_config.get("port", 8766)
        self.subtitle_show_status = subtitle_config.get("show_status", False)
        
        # 初始化字幕管理器
        if self.subtitle_enabled:
            from .talk_subtitle import ReplyGenerationManager
            self.subtitle_manager = ReplyGenerationManager(
                port=self.subtitle_port,
                show_status=self.subtitle_show_status,
                logger=self.logger
            )
        else:
            self.subtitle_manager = None
        
        # watching消息翻译表
        self.watching_translation = {
            "lens": "相机",
            "danmu": "弹幕", 
            "wandering": None
        }
            
    async def handle_maicore_message(self, message: MessageBase):
        """处理从 MaiCore 收到的消息，根据消息段类型进行不同的处理。"""
        # 处理 emotion 类型的消息段
        if message.message_segment.type == "emotion":
            emotion_data = message.message_segment.data
            self.logger.info(f"收到emotion消息: '{emotion_data}'")
            # 处理心情数据，更新到mai_state
            await self._handle_emotion_data(emotion_data)
            
        if message.message_segment.type == "state":
            state_data = message.message_segment.data
            self.logger.info(f"收到state消息: '{state_data}'")
            await self.reply_state.deal_state(state_data)
            
        if message.message_segment.type == "body_action":
            body_action_data = message.message_segment.data
            self.logger.info(f"收到body_action消息: '{body_action_data}'")
            await action_sender.send_action("body_action", str(body_action_data))
            
        if message.message_segment.type == "head_action":
            head_action_data = message.message_segment.data
            self.logger.info(f"收到head_action消息: '{head_action_data}'")
            await action_sender.send_action("head_action", str(head_action_data))


    async def setup(self):
        await super().setup()
        self.logger.info("Initializing WarudoPlugin setup...")
        if not self.lip_sync_enabled:
            self.logger.error("SETUP FAILED for WarudoPlugin: Lip-sync is disabled in config or dependencies (librosa, scipy) are missing.")
            return
        
        self.core.register_websocket_handler("*", self.handle_maicore_message)
        self.logger.info("Warudo 插件已设置，监听所有 MaiCore WebSocket 消息。")

        # 启动连接的后台任务
        self._connection_task = asyncio.create_task(self._connect_websocket(), name="Warudo_Connect")
        self.logger.info("WebSocket connection task started.")

        # 注册为TTS音频数据处理器
        self.core.register_service("warudo", self)
        self.logger.info("Registered 'warudo' service for audio analysis.")
        
        # 启动字幕管理器服务器
        if self.subtitle_manager:
            await self.subtitle_manager.start_server()
            self.logger.info("字幕管理器服务器已启动")
            
            # 将回复页面管理器注册为核心服务，供其他插件使用
            self.core.register_service("reply_generation_manager", self.subtitle_manager)
            self.logger.info("回复页面管理器已注册为核心服务")


    async def _connect_websocket(self):
        """Internal task to connect to the WebSocket server."""
        uri = f"ws://{self.ws_host}:{self.ws_port}"
        first_connection = True
        
        while True:
            try:
                self.logger.info(f"Attempting to connect to WebSocket server at {uri}...")
                self.websocket = await websockets.connect(uri)
                self._is_connected = True
                self.logger.info(f"Successfully connected to WebSocket server at {uri}.")
                
                # 设置状态管理器的WebSocket连接
                action_sender.set_websocket(self.websocket)
                
                # 只在首次连接时启动任务
                if first_connection:
                    self.logger.info("状态管理器监控已启动")
                    
                    # 启动眨眼任务
                    await self.blink_task.start()
                    self.logger.info("眨眼任务已启动")
                    
                    # 启动眼部移动任务
                    await self.shift_task.start()
                    self.logger.info("眼部移动任务已启动")
                    
                    first_connection = False
                else:
                    # 重连时只需要重新设置WebSocket连接
                    self.logger.info("WebSocket重连成功，服务继续运行")
                
                # Keep the connection alive
                await self.websocket.wait_closed()
            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError) as e:
                self.logger.warning(f"WebSocket connection lost or refused: {e}. Reconnecting in 5 seconds...")
            except Exception as e:
                self.logger.error(f"An unexpected error occurred with WebSocket connection: {e}. Reconnecting in 5 seconds...")
            finally:
                self._is_connected = False
                # 断开时不停止任务，让服务继续运行
                self.websocket = None
                action_sender.set_websocket(None)
                self.logger.info("WebSocket断开，但服务继续运行")
                await asyncio.sleep(5)

    async def cleanup(self):
        """Close the WebSocket connection and cancel tasks."""
        self.logger.info("Cleaning up WarudoPlugin...")
        
        # 停止眨眼任务
        await self.blink_task.stop()
        self.logger.info("眨眼任务已停止")
        
        # 停止眼部移动任务
        await self.shift_task.stop()
        self.logger.info("眼部移动任务已停止")
        
        # 停止字幕管理器服务器
        if self.subtitle_manager:
            try:
                await self.subtitle_manager.stop_server()
                self.logger.info("字幕管理器服务器已停止")
            except Exception as e:
                self.logger.error(f"停止字幕管理器服务器时出错: {e}")
                # 强制清理
                self.subtitle_manager = None
        
        # 取消WebSocket连接任务
        if self._connection_task:
            self._connection_task.cancel()
            try:
                await asyncio.wait_for(self._connection_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                self.logger.debug("WebSocket连接任务已取消")
        
        # 关闭WebSocket连接
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=2.0)
                self.logger.info("WebSocket connection closed.")
            except asyncio.TimeoutError:
                self.logger.warning("WebSocket关闭超时")
        
        try:
            await super().cleanup()
        except Exception as e:
            self.logger.error(f"父类清理时出错: {e}")
        
        self.logger.info("WarudoPlugin清理完成")

    async def analyze_audio_chunk(self, audio_data: bytes, sample_rate: int) -> Dict[str, float]:
        """Analyzes a chunk of audio data and returns lip-sync parameters."""
        if not AUDIO_ANALYSIS_AVAILABLE:
            return {}

        try:
            # 将字节数据转换为numpy数组
            audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

            # 1. 计算音量 (RMS)
            rms = np.sqrt(np.mean(audio_np**2))
            volume = float(rms)

            # 2. 分析元音特征
            # 动态计算合适的FFT窗口大小，确保不超过输入信号长度
            audio_length = len(audio_np)
            n_fft = min(512, audio_length)  # 使用较小的FFT窗口大小，最大不超过音频长度
            
            # 如果音频太短，跳过频谱分析
            if audio_length < 64:  # 最小FFT窗口大小
                vowel_features = {f"vowel_{vowel.lower()}": 0.0 for vowel in self.vowel_formants.keys()}
            else:
                D = librosa.stft(audio_np, n_fft=n_fft, hop_length=n_fft//4)
                magnitude, phase = librosa.magphase(D)
                freqs = librosa.fft_frequencies(sr=sample_rate, n_fft=n_fft)
                vowel_features = self._analyze_vowel_features(magnitude, freqs)

            return {"volume": volume, **vowel_features}
        except Exception as e:
            self.logger.error(f"Error during audio analysis: {e}", exc_info=True)
            return {}

    def _analyze_vowel_features(self, magnitude: np.ndarray, freqs: np.ndarray) -> Dict[str, float]:
        """Analyzes spectral magnitude to determine vowel features."""
        vowel_strengths = {}
        total_energy = np.sum(magnitude)
        if total_energy == 0:
            return {f"vowel_{vowel.lower()}": 0.0 for vowel in self.vowel_formants.keys()}

        for vowel, (f1, f2) in self.vowel_formants.items():
            f1_idx = np.argmin(np.abs(freqs - f1))
            f2_idx = np.argmin(np.abs(freqs - f2))
            
            # 添加边界检查，防止索引越界
            mag_len = magnitude.shape[0]
            f1_start = max(0, f1_idx - 5)
            f1_end = min(mag_len, f1_idx + 5)
            f2_start = max(0, f2_idx - 5)
            f2_end = min(mag_len, f2_idx + 5)
            
            energy = np.sum(magnitude[f1_start:f1_end]) + np.sum(magnitude[f2_start:f2_end])
            vowel_strengths[f"vowel_{vowel.lower()}"] = energy / total_energy
            
        return vowel_strengths

    async def _check_audio_timeout(self):
        """检查音频超时，如果超时则停止说话状态"""
        if self.is_speaking and self.last_audio_time > 0:
            current_time = asyncio.get_event_loop().time()
            if current_time - self.last_audio_time > self.audio_timeout:
                self.logger.info("音频数据超时，停止说话状态")
                await self._stop_speaking()

    async def _start_speaking(self):
        """开始说话状态"""
        if not self.is_speaking:
            self.is_speaking = True
            self.reseted = True
            self.logger.info("开始说话状态")

    async def _stop_speaking(self):
        """停止说话状态"""
        if self.is_speaking:
            self.is_speaking = False
            await self.reply_state.stop_talking()
            # 无论连接状态如何，都尝试清理口型状态
            try:
                # 发送最终的闭嘴指令（清零所有元音值和音量）
                stop_parameters = {
                    "volume": 0.0,
                    "vowel_a": 0.0,
                    "vowel_i": 0.0,
                    "vowel_u": 0.0,
                    "vowel_e": 0.0,
                    "vowel_o": 0.0
                }
                await self._update_lip_sync_parameters(stop_parameters)
                
            except Exception as e:
                self.logger.error(f"停止说话时处理嘴部状态失败: {e}")
            
            self.logger.info("停止说话状态")

    async def process_tts_audio(self, audio_data: bytes, sample_rate: int):
        """Receives audio from TTS, analyzes it, and sends parameters via WebSocket."""
        # 只检查会话是否激活，移除连接状态检查
        # if not self.session_active:
            # return

        # 开始说话状态（如果还没有的话）
        if not self.reseted:
            await self._start_speaking()
            await self.reply_state.start_talking()

        with self.audio_analysis_lock:
            self.accumulated_audio.extend(audio_data)
            
        # self.logger.info(f"accumulated_audio: {len(self.accumulated_audio)}")

        # 使用 while 循环处理所有累积的音频数据
        required_bytes = self.buffer_size * 2  # int16 = 2 bytes
        audio_processed = False
        while len(self.accumulated_audio) >= required_bytes:
            # 在循环内部锁定，仅在操作共享数据时
            with self.audio_analysis_lock:
                chunk_to_analyze = self.accumulated_audio[:required_bytes]
                self.accumulated_audio = self.accumulated_audio[required_bytes:]

            # self.logger.info(f"chunk_to_analyze: {len(chunk_to_analyze)}")
            analysis_result = await self.analyze_audio_chunk(bytes(chunk_to_analyze), sample_rate)
            if analysis_result:
                await self._update_lip_sync_parameters(analysis_result)
                audio_processed = True

        # 只有在实际处理了音频数据时才更新最后音频时间
        if audio_processed:
            self.last_audio_time = asyncio.get_event_loop().time()
        
        # 检查音频超时
        await self._check_audio_timeout()

        # --- 修改：计算并等待块播放时长，并加入可配置的偏移量 ---
        try:
            # 计算当前处理的音频块的播放时长
            chunk_duration = self.buffer_size / sample_rate
            # 等待音频块播放完成，实现与音频播放同步
            await asyncio.sleep(chunk_duration)
        except Exception as e:
            self.logger.error(f"Error during lip-sync delay: {e}", exc_info=True)

    async def _update_lip_sync_parameters(self, analysis_result: Dict[str, float]):
        """平滑处理参数并更新到mai_state中"""
        # 平滑处理参数
        smoothing = self.smoothing_factor
        self.current_volume = self.current_volume * smoothing + analysis_result.get("volume", 0.0) * (1 - smoothing)
        
        for vowel in self.vowel_formants.keys():
            key = f"vowel_{vowel.lower()}"
            self.current_vowel_values[vowel] = self.current_vowel_values[vowel] * smoothing + analysis_result.get(key, 0.0) * (1 - smoothing)

        # 更新LipSyncState到mai_state
        lip_sync_states = {}
        
        # 映射元音到LipSyncState
        vowel_mapping = {
            "A": "VowelA",
            "I": "VowelI",
            "U": "VowelU",
            "E": "VowelE",
            "O": "VowelO"
        }
        
        # 检查音量是否足够进行口型同步
        if self.current_volume < self.volume_threshold:
            # 音量太低，设置为闭嘴状态
            for vowel in vowel_mapping.values():
                lip_sync_states[vowel] = 0.0
        else:
            # 音量足够，找到最强的元音作为主要口型
            max_vowel = None
            max_value = 0.0
            
            for vowel, value in self.current_vowel_values.items():
                if value > max_value:
                    max_value = value
                    max_vowel = vowel
            
            # 设置所有元音为0，然后设置主要元音
            for vowel in vowel_mapping.values():
                lip_sync_states[vowel] = 0.0
                
            # 如果找到了主要元音，设置其强度
            if max_vowel and max_vowel in vowel_mapping:
                # 口型强度 = 音量强度 * 元音检测强度 * 敏感度
                # 这样确保口型程度既反映了声音大小，也反映了元音特征
                intensity = min(1.0, self.current_volume * max_value * self.vowel_detection_sensitivity * 10)
                lip_sync_states[vowel_mapping[max_vowel]] = intensity
        
        # 批量更新状态
        self.state_manager.mouth_state.set_vowel_state(lip_sync_states)
        self.logger.debug(f"更新口型状态到mai_state: {lip_sync_states} (音量: {self.current_volume:.3f})")

    async def start_lip_sync_session(self, text: str = ""):
        """Called by TTS plugin to start a lip-sync session."""
        self.logger.info(f"即将开始Lip-sync session，文本长度: {len(text)} 字符")
        
        self.accumulated_audio.clear()
        self.last_audio_time = 0
        self.reseted = False

    async def stop_lip_sync_session(self):
        """Called by TTS plugin to stop a lip-sync session."""
        # 如果正在说话，停止说话状态（这会自动处理嘴部状态恢复）
        if self.is_speaking:
            await self._stop_speaking()

        self.logger.info("口型同步会话已停止，回复内容保持显示")
    
    
    
    async def _handle_emotion_data(self, emotion_data: Any):
        """
        处理从MaiCore收到的心情数据
        
        Args:
            emotion_data: 心情数据，可能是字典或JSON字符串
        """
        try:
            # 解析心情数据
            if isinstance(emotion_data, dict):
                mood_data = emotion_data
            elif isinstance(emotion_data, str):
                mood_data = json.loads(emotion_data)
            else:
                self.logger.warning(f"不支持的emotion数据类型: {type(emotion_data)}")
                return
            
            self.logger.debug(f"解析后的心情数据: {mood_data}")
            
            # 更新心情状态
            has_changes = self.mood_manager.update_mood(mood_data)
            
            if has_changes:
                self.logger.info("心情状态发生变化，表情已更新")
            else:
                self.logger.debug("心情状态无变化")
                
        except json.JSONDecodeError:
            self.logger.error(f"emotion消息不是有效的JSON格式: {emotion_data}")
        except Exception as e:
            self.logger.error(f"处理emotion数据时出错: {e}", exc_info=True)
    

# 必须有这个入口点
plugin_entrypoint = WarudoPlugin 