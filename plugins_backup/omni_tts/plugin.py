# Amaidesu Omni TTS Plugin: src/plugins/omni_tts/plugin.py

import asyncio
import os
import sys
import socket
import tempfile
from typing import Dict, Any, Optional
import numpy as np
from dataclasses import dataclass
from collections import deque

# --- Dependencies Check ---
dependencies_ok = True
try:
    import aiohttp
except ImportError:
    print("依赖缺失: 请运行 'pip install aiohttp' 来使用 Qwen TTS 功能。", file=sys.stderr)
    dependencies_ok = False

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    print("依赖缺失: 请运行 'pip install sounddevice soundfile' 来使用音频播放功能。", file=sys.stderr)
    dependencies_ok = False

# --- TOML Loading ---
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import toml as tomllib
    except ImportError:
        print("依赖缺失: 请运行 'pip install toml' 来加载 Omni TTS 插件配置。", file=sys.stderr)
        tomllib = None
        dependencies_ok = False

# --- Amaidesu Core Imports ---
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from maim_message import MessageBase

# --- Omni TTS Engine ---
try:
    from .omni_tts import OmniTTS
    OMNI_TTS_AVAILABLE = True
except ImportError as e:
    print(f"错误: 无法导入 OmniTTS: {e}", file=sys.stderr)
    OMNI_TTS_AVAILABLE = False


@dataclass
class TTSTask:
    """TTS 任务数据类"""
    task_id: int
    text: str
    audio_data: Optional[bytes] = None
    error: Optional[Exception] = None
    processed: bool = False


class OmniTTSPlugin(BasePlugin):
    """专门处理阿里云Qwen-Omni TTS的插件"""

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.omni_config = self.plugin_config
        
        # 检查Omni TTS可用性
        if not OMNI_TTS_AVAILABLE:
            self.logger.error("Omni TTS 不可用，插件无法初始化。请检查依赖。")
            raise ImportError("Omni TTS 不可用")
        
        # --- 音频设备配置 ---
        self.output_device_name = self.omni_config.get("output_device_name") or None
        self.output_device_index = self._find_device_index(self.output_device_name, kind="output")
        
        # --- 队列系统配置 ---
        self.message_queue = deque()  # 消息队列
        self.processing_tasks: Dict[int, TTSTask] = {}  # 正在处理的任务
        self.next_task_id = 0  # 下一个任务ID
        self.next_play_id = 0  # 下一个要播放的任务ID
        self.queue_lock = asyncio.Lock()  # 队列操作锁
        self.play_lock = asyncio.Lock()  # 播放锁，确保同时只有一个音频播放
        self.queue_processor_running = False  # 队列处理器状态
        
        # --- Omni TTS 初始化 ---
        api_key = self.omni_config.get("api_key") or os.environ.get("DASHSCOPE_API_KEY")
        
        if not api_key:
            self.logger.error("Omni TTS API 密钥未配置。请在配置文件中设置 api_key 或设置环境变量 DASHSCOPE_API_KEY")
            raise ValueError("Omni TTS API 密钥未配置")
        
        # 获取后处理配置
        post_processing = self.omni_config.get("post_processing", {})
        
        self.omni_tts = OmniTTS(
            api_key=api_key,
            model_name=self.omni_config.get("model_name", "qwen2.5-omni-7b"),
            voice=self.omni_config.get("voice", "Chelsie"),
            format=self.omni_config.get("format", "wav"),
            base_url=self.omni_config.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            enable_post_processing=post_processing.get("enabled", False),
            volume_reduction_db=post_processing.get("volume_reduction", 0.0),
            noise_level=post_processing.get("noise_level", 0.0),
            blow_up_probability=self.omni_config.get("blow_up_probability", 0.0),
            blow_up_texts=self.omni_config.get("blow_up_texts", [])
        )
        
        self.logger.info(f"Omni TTS 插件初始化完成: {self.omni_config.get('model_name', 'qwen2.5-omni-7b')}")

        # --- UDP 广播配置 ---
        udp_config = self.omni_config.get("udp_broadcast", {})
        self.udp_enabled = udp_config.get("enable", False)
        self.udp_socket = None
        self.udp_dest = None
        if self.udp_enabled:
            host = udp_config.get("host", "127.0.0.1")
            port = udp_config.get("port", 9999)  # 使用不同的端口避免冲突
            try:
                self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_dest = (host, port)
                self.logger.info(f"Omni TTS UDP 广播已启用，目标: {self.udp_dest}")
            except Exception as e:
                self.logger.error(f"初始化 Omni TTS UDP socket 失败: {e}", exc_info=True)
                self.udp_socket = None
                self.udp_enabled = False
        else:
            self.logger.info("Omni TTS UDP 广播已禁用。")

    def _find_device_index(self, device_name: Optional[str], kind: str = "output") -> Optional[int]:
        """根据设备名称查找设备索引"""
        if "sd" not in globals():
            self.logger.error("sounddevice 库不可用，无法查找音频设备。")
            return None
        try:
            devices = sd.query_devices()
            if device_name:
                for i, device in enumerate(devices):
                    if device_name.lower() in device["name"].lower() and device[f"{kind}_channels"] > 0:
                        self.logger.info(f"找到 {kind} 设备 '{device['name']}' (匹配 '{device_name}')，索引: {i}")
                        return i
                self.logger.warning(f"未找到名称包含 '{device_name}' 的 {kind} 设备，将使用默认设备。")

            default_device_indices = sd.default.device
            default_index = default_device_indices[1] if kind == "output" else default_device_indices[0]
            if default_index == -1:
                self.logger.warning(f"未找到默认 {kind} 设备，将使用 None (由 sounddevice 选择)。")
                return None

            self.logger.info(f"使用默认 {kind} 设备索引: {default_index} ({sd.query_devices(default_index)['name']})")
            return default_index
        except Exception as e:
            self.logger.error(f"查找音频设备时出错: {e}，将使用 None (由 sounddevice 选择)", exc_info=True)
            return None

    async def setup(self):
        """注册处理来自 MaiCore 的 'text' 类型消息"""
        await super().setup()
        self.core.register_websocket_handler("*", self.handle_maicore_message)
        
        # 启动队列处理器
        if not self.queue_processor_running:
            self.queue_processor_running = True
            asyncio.create_task(self._queue_processor())
            
        self.logger.info("Omni TTS 插件已设置，监听所有 MaiCore WebSocket 消息。")

    async def cleanup(self):
        """关闭 UDP socket 和停止队列处理"""
        self.queue_processor_running = False
        
        if self.udp_socket:
            self.logger.info("正在关闭 Omni TTS UDP socket...")
            self.udp_socket.close()
            self.udp_socket = None
            
        # 等待所有处理中的任务完成
        while self.processing_tasks:
            await asyncio.sleep(0.1)
            
        await super().cleanup()

    async def handle_maicore_message(self, message: MessageBase):
        """处理从 MaiCore 收到的消息，如果是文本类型，则添加到队列"""
        if message.message_segment and message.message_segment.type == "text":
            original_text = message.message_segment.data
            if not isinstance(original_text, str) or not original_text.strip():
                self.logger.debug("收到非字符串或空文本消息段，跳过 Omni TTS。")
                return

            original_text = original_text.strip()
            self.logger.info(f"收到文本消息，添加到队列: '{original_text[:50]}...'")

            # 文本清理服务（可选）
            final_text = original_text
            cleanup_service = self.core.get_service("text_cleanup")
            if cleanup_service:
                self.logger.debug("找到 text_cleanup 服务，尝试清理文本...")
                try:
                    cleaned = await cleanup_service.clean_text(original_text)
                    if cleaned:
                        self.logger.info(
                            f"文本经 Cleanup 服务清理: '{cleaned[:50]}...' (原: '{original_text[:50]}...')"
                        )
                        final_text = cleaned
                    else:
                        self.logger.warning("Cleanup 服务调用失败或返回空，使用原始文本。")
                except Exception as e:
                    self.logger.error(f"调用 text_cleanup 服务时出错: {e}", exc_info=True)

            if not final_text:
                self.logger.warning("清理后文本为空，跳过后续处理。")
                return

            # 添加到队列
            await self._add_to_queue(final_text)

    async def _add_to_queue(self, text: str):
        """将文本添加到处理队列并立即开始TTS处理"""
        async with self.queue_lock:
            task_id = self.next_task_id
            self.next_task_id += 1
            
            task = TTSTask(task_id=task_id, text=text)
            self.message_queue.append(task)
            self.processing_tasks[task_id] = task
            
            self.logger.info(f"任务 {task_id} 已添加到队列: '{text[:30]}...' (队列长度: {len(self.message_queue)})")
        
        # 立即开始TTS处理
        self.logger.info(f"立即开始处理任务 {task_id}")
        asyncio.create_task(self._process_tts_task(task))

    async def _queue_processor(self):
        """队列处理器，专门负责按序播放"""
        self.logger.info("播放队列处理器已启动")
        
        while self.queue_processor_running:
            try:
                # 检查是否可以播放下一个任务
                await self._try_play_next()
                
                await asyncio.sleep(0.05)  # 更频繁检查播放队列
                
            except Exception as e:
                self.logger.error(f"播放队列处理器出错: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _process_tts_task(self, task: TTSTask):
        """处理单个TTS任务（生成音频）"""
        try:
            self.logger.info(f"开始处理任务 {task.task_id}: '{task.text[:30]}...'")
            
            # UDP 广播（可选）
            if self.udp_enabled and self.udp_socket and self.udp_dest:
                self._broadcast_text(task.text)
            
            # 生成音频
            audio_data = await self.omni_tts.generate_audio(task.text)
            task.audio_data = audio_data
            
            self.logger.info(f"任务 {task.task_id} 音频生成完成，立即检查播放队列")
            
            # TTS完成后立即尝试播放下一个任务
            asyncio.create_task(self._try_play_next())
            
        except Exception as e:
            self.logger.error(f"处理任务 {task.task_id} 时出错: {e}", exc_info=True)
            task.error = e
            # 即使出错也要尝试播放下一个
            asyncio.create_task(self._try_play_next())

    async def _try_play_next(self):
        """尝试播放下一个应该播放的任务"""
        # 检查是否有任务正在播放，如果有则等待
        if self.play_lock.locked():
            self.logger.debug(f"播放锁被占用，等待当前任务完成")
            return
            
        async with self.queue_lock:
            # 找到下一个应该播放的任务
            if self.processing_tasks.get(self.next_play_id) is not None:
                task = self.processing_tasks[self.next_play_id]
                
                # 检查任务是否准备好播放
                if task.audio_data is not None or task.error is not None:
                    self.logger.info(f"任务 {task.task_id} 准备就绪，立即开始播放")
                    
                    # 从队列中移除该任务
                    if task in self.message_queue:
                        self.message_queue.remove(task)
                    
                    # 更新下一个播放ID
                    self.next_play_id += 1
                    
                    # 启动播放任务（不等待）
                    asyncio.create_task(self._play_task(task))
                else:
                    self.logger.debug(f"任务 {task.task_id} 还未处理完成，等待中...")
            else:
                self.logger.debug(f"下一个播放任务 ID {self.next_play_id} 不存在")

    async def _play_task(self, task: TTSTask):
        """播放单个任务的音频"""
        async with self.play_lock:  # 确保同时只有一个音频播放
            try:
                if task.error:
                    self.logger.error(f"任务 {task.task_id} 有错误，跳过播放: {task.error}")
                    return
                
                if not task.audio_data:
                    self.logger.error(f"任务 {task.task_id} 没有音频数据，跳过播放")
                    return
                
                self.logger.info(f"开始播放任务 {task.task_id}: '{task.text[:30]}...'")
                await self._speak_audio(task.text, task.audio_data)
                self.logger.info(f"任务 {task.task_id} 播放完成")
                
            except Exception as e:
                self.logger.error(f"播放任务 {task.task_id} 时出错: {e}", exc_info=True)
            finally:
                # 清理任务
                async with self.queue_lock:
                    self.processing_tasks.pop(task.task_id, None)
                    task.processed = True
        
        # 播放完成后立即尝试播放下一个任务
        self.logger.debug(f"任务 {task.task_id} 播放锁已释放，立即检查下一个任务")
        asyncio.create_task(self._try_play_next())

    def _broadcast_text(self, text: str):
        """通过 UDP 发送文本"""
        if self.udp_socket and self.udp_dest:
            try:
                message_bytes = text.encode("utf-8")
                self.udp_socket.sendto(message_bytes, self.udp_dest)
                self.logger.debug(f"已发送 Omni TTS 内容到 UDP 监听器: {self.udp_dest}")
            except Exception as e:
                self.logger.warning(f"发送 Omni TTS 内容到 UDP 监听器失败: {e}")

    async def _speak_audio(self, text: str, audio_data: bytes):
        """播放音频数据"""
        if "sf" not in globals() or "sd" not in globals():
            self.logger.error("缺少必要的音频库 (soundfile, sounddevice)，无法播放。")
            return

        tmp_filename = None
        duration_seconds: Optional[float] = None
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir=tempfile.gettempdir()) as tmp_file:
                tmp_filename = tmp_file.name
                tmp_file.write(audio_data)
            self.logger.debug(f"音频已保存到临时文件: {tmp_filename}")
            
            # 读取音频并计算时长
            audio_array, samplerate = await asyncio.to_thread(sf.read, tmp_filename, dtype="float32")

            # --- 计算音频时长 ---
            self.logger.info(f"读取音频完成，采样率: {samplerate} Hz")
            if samplerate > 0 and isinstance(audio_array, np.ndarray):
                duration_seconds = len(audio_array) / samplerate
                self.logger.info(f"计算得到音频时长: {duration_seconds:.3f} 秒")
                self.logger.debug(f"音频数组形状: {audio_array.shape}, 数据类型: {audio_array.dtype}")
            else:
                self.logger.warning("无法计算音频时长 (采样率或数据无效)")

            # --- 通知 Subtitle Service ---
            if duration_seconds is not None and duration_seconds > 0:
                subtitle_service = self.core.get_service("subtitle_service")
                if subtitle_service:
                    self.logger.debug("找到 subtitle_service，准备记录语音信息...")
                    try:
                        asyncio.create_task(subtitle_service.record_speech(text, duration_seconds))
                    except Exception as e:
                        self.logger.error(f"调用 subtitle_service.record_speech 时出错: {e}", exc_info=True)

            # --- 播放音频 ---
            self.logger.info(f"开始播放音频 (设备索引: {self.output_device_index})...")
            
            try:
                expected_duration = duration_seconds if duration_seconds else len(audio_array) / samplerate
                self.logger.debug(f"预期播放时长: {expected_duration:.3f} 秒")
                
                # 停止所有现有播放
                try:
                    sd.stop()
                except:
                    pass
                
                import time
                actual_start_time = time.time()
                
                # 使用非阻塞播放 + 手动等待
                sd.play(audio_array, samplerate=samplerate, device=self.output_device_index)
                
                # 手动等待完整的音频时长 + 缓冲时间
                wait_time = expected_duration + 0.3
                self.logger.debug(f"手动等待音频播放: {wait_time:.3f} 秒")
                await asyncio.sleep(wait_time)
                
                # 确保播放停止
                sd.stop()
                
                actual_end_time = time.time()
                actual_total_time = actual_end_time - actual_start_time
                self.logger.info(f"音频播放完成，总耗时: {actual_total_time:.3f} 秒")
                
            except Exception as play_error:
                self.logger.error(f"音频播放失败: {play_error}")
                # 备用播放方案
                try:
                    self.logger.info("尝试备用播放方案")
                    backup_filename = tmp_filename if tmp_filename else f"backup_omni_audio_{int(time.time())}.wav"
                    if not os.path.exists(backup_filename):
                        sf.write(backup_filename, audio_array, samplerate)
                    
                    import subprocess
                    import platform
                    
                    if platform.system() == "Windows":
                        cmd = ['powershell', '-c', f'(New-Object Media.SoundPlayer "{backup_filename}").PlaySync()']
                        process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        await process.wait()
                    else:
                        for player in ['aplay', 'paplay', 'afplay']:
                            try:
                                process = await asyncio.create_subprocess_exec(player, backup_filename, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                await process.wait()
                                break
                            except FileNotFoundError:
                                continue
                    
                    self.logger.info("备用播放完成")
                    
                    if backup_filename != tmp_filename:
                        try:
                            os.remove(backup_filename)
                        except:
                            pass
                            
                except Exception as backup_error:
                    self.logger.error(f"备用播放也失败: {backup_error}")
                    raise play_error

        except Exception as e:
            self.logger.error(f"音频处理或播放时发生错误: {type(e).__name__} - {e}", exc_info=True)
        finally:
            if tmp_filename and os.path.exists(tmp_filename):
                try:
                    os.remove(tmp_filename)
                    self.logger.debug(f"已删除临时文件: {tmp_filename}")
                except Exception as e_rem:
                    self.logger.warning(f"删除临时文件 {tmp_filename} 时出错: {e_rem}")


# --- Plugin Entry Point ---
plugin_entrypoint = OmniTTSPlugin 