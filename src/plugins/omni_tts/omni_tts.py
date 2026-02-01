"""
大模型音频生成模块 - 使用阿里云Qwen-Omni模型实现文本到语音的转换
"""

import os
import json
import aiohttp
import base64
from typing import List, Optional, AsyncGenerator
import io
import numpy as np
import soundfile as sf
import random
from datetime import datetime

# 导入音频处理库
try:
    from pydub import AudioSegment  # noqa: F401
    from pydub.generators import WhiteNoise  # noqa: F401

    PYDUB_AVAILABLE = True
except ImportError:
    print("警告: pydub库未安装，音频后处理功能将不可用")
    PYDUB_AVAILABLE = False


class OmniTTS:
    """使用阿里云Qwen-Omni模型实现的文本到语音转换类"""

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen-omni-turbo",
        voice: str = "Chelsie",
        format: str = "wav",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        # 音频后处理参数
        enable_post_processing: bool = False,
        volume_reduction_db: float = 0,
        noise_level: float = 0,
        blow_up_probability: float = 0.0,
        blow_up_texts: Optional[List[str]] = None,
        # 调试参数
        save_debug_audio: bool = True,
        debug_audio_dir: str = "debug_audio",
    ):
        """
        初始化OmniTTS类

        Args:
            api_key: 阿里云百炼API Key
            model_name: 模型名称，默认为qwen-omni-turbo
            voice: 语音音色，默认为Chelsie
            format: 音频格式，默认为wav
            base_url: API基础URL
            enable_post_processing: 是否启用音频后处理
            volume_reduction_db: 音量降低程度(dB)，值越大音量越低
            noise_level: 杂音强度，0-1之间的浮点数，0表示无杂音
            blow_up_probability: 触发"麦炸了"彩蛋的概率，0-1之间的浮点数
            blow_up_texts: 彩蛋触发时使用的文本列表
            save_debug_audio: 是否保存调试音频文件
            debug_audio_dir: 调试音频文件保存目录
        """
        self.api_key = api_key
        self.model_name = model_name
        self.voice = voice
        self.format = format
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        # 音频后处理参数
        self.enable_post_processing = True  # 强制启用后处理
        self.volume_reduction_db = volume_reduction_db
        self.noise_level = max(0, min(1, noise_level))  # 确保在0-1之间
        self.blow_up_probability = max(0, min(1, blow_up_probability))  # 确保在0-1之间
        self.blow_up_texts = blow_up_texts if blow_up_texts else ["我的麦真的很炸吗（大声急促）"]

        # 调试相关参数
        self.save_debug_audio = save_debug_audio
        self.debug_audio_dir = debug_audio_dir
        self.audio_counter = 0  # 音频文件计数器

        self.blow_up = False

        # 创建调试音频目录
        if self.save_debug_audio:
            os.makedirs(self.debug_audio_dir, exist_ok=True)
            print(f"调试音频将保存到目录: {self.debug_audio_dir}")

        # 检查pydub是否可用
        global PYDUB_AVAILABLE
        if not PYDUB_AVAILABLE:
            try:
                from pydub import AudioSegment  # noqa: F401
                from pydub.generators import WhiteNoise  # noqa: F401

                PYDUB_AVAILABLE = True
                print("成功导入pydub库")
            except ImportError:
                print("警告: pydub库未安装，音频后处理功能将不可用")
                PYDUB_AVAILABLE = False
                self.enable_post_processing = False

        # 尝试导入OpenAI库
        try:
            import openai  # noqa: F401

            self.openai_available = True
        except ImportError:
            self.openai_available = False
            print("OpenAI库未安装，将使用aiohttp直接调用API")

    def _save_debug_audio(self, audio_data: bytes, text: str, prefix: str = "") -> str:
        """
        保存调试音频文件

        Args:
            audio_data: 音频字节数据
            text: 生成音频的原始文本
            prefix: 文件名前缀（如 "raw_" 或 "processed_"）

        Returns:
            保存的文件路径
        """
        if not self.save_debug_audio or not audio_data:
            return ""

        try:
            # 增加计数器
            self.audio_counter += 1

            # 生成时间戳
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # 清理文本作为文件名的一部分（只取前20个字符，移除特殊字符）
            safe_text = "".join(c for c in text if c.isalnum() or c in "._- ")[:20]
            if not safe_text:
                safe_text = "audio"

            # 生成文件名
            filename = f"{prefix}{self.audio_counter:03d}_{timestamp}_{safe_text}.wav"
            filepath = os.path.join(self.debug_audio_dir, filename)

            # 保存文件
            with open(filepath, "wb") as f:
                f.write(audio_data)

            print(f"调试音频已保存: {filepath} (大小: {len(audio_data)} 字节)")
            return filepath

        except Exception as e:
            print(f"保存调试音频失败: {str(e)}")
            return ""

    async def generate_audio(self, text: str) -> bytes:
        """
        使用大模型生成音频数据

        Args:
            text: 需要转换为语音的文本

        Returns:
            音频数据的字节流
        """
        print(f"开始调用大模型API生成音频，文本: {text[:30]}{'...' if len(text) > 30 else ''}")

        self.blow_up = False
        if self.blow_up_texts and random.random() < self.blow_up_probability:
            self.blow_up = True
            text = random.choice(self.blow_up_texts)

        # 由于修改后的流式API现在只返回一个合并后的音频块，我们可以直接获取这个块
        audio_chunks = []
        async for chunk in self.generate_audio_stream(text):
            audio_chunks.append(chunk)

        if not audio_chunks:
            raise Exception("未能获取任何音频数据")

        # 合并所有音频块（通常只有一个）
        complete_audio = b"".join(audio_chunks)

        # 保存最终合并的音频
        self._save_debug_audio(complete_audio, text, "final_")

        return complete_audio

    async def generate_audio_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        使用大模型流式生成音频数据

        Args:
            text: 需要转换为语音的文本

        Yields:
            音频数据块的字节流
        """
        print(f"开始流式调用大模型API生成音频，文本: {text[:30]}{'...' if len(text) > 30 else ''}")
        print(f"生成prompt: {text}")
        prompt = f"复述这句话，不要输出其他内容，只输出'{text}'就好，不要输出其他内容，不要输出前后缀，不要输出'{text}'以外的内容，不要说：如果还有类似的需求或者想聊聊别的"

        # 优先使用OpenAI客户端(如果可用)
        if self.openai_available and self._should_use_openai_client():
            async for chunk in self._generate_with_openai_client(prompt):
                yield chunk
            return

        # 否则使用aiohttp直接调用API
        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}/chat/completions"

            # 按照官方示例构建消息格式 - 使用简单格式而不是嵌套格式
            payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "stream_options": {"include_usage": True},
                "modalities": ["text", "audio"],
                "audio": {"voice": self.voice, "format": self.format},
            }

            print(f"API请求: {url}")
            print(f"使用模型: {self.model_name}, 音色: {self.voice}, 格式: {self.format}")

            try:
                async with session.post(url, headers=self.headers, json=payload) as response:
                    print(f"API返回状态码: {response.status}")
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"API错误响应: {error_text}")
                        raise Exception(f"API请求失败: {response.status} - {error_text}")

                    # 处理流式响应
                    chunk_count = 0
                    audio_string = ""

                    async for line in response.content:
                        line = line.strip()
                        if not line:
                            continue

                        if line == b"data: [DONE]":
                            print("收到流结束标记 [DONE]")
                            continue

                        if line.startswith(b"data: "):
                            chunk_count += 1
                            json_str = line[6:].decode("utf-8")
                            try:
                                chunk = json.loads(json_str)

                                # 检查是否包含音频数据
                                if "choices" in chunk and len(chunk["choices"]) > 0:
                                    delta = chunk["choices"][0].get("delta", {})

                                    # 处理音频数据 - 直接从audio字段获取
                                    if delta and "audio" in delta:
                                        if "data" in delta["audio"]:
                                            # 累积base64字符串
                                            audio_string += delta["audio"]["data"]
                                        elif "transcript" in delta["audio"]:
                                            # 处理音频转录文本
                                            print(f"收到音频转录文本: {delta['audio']['transcript']}")
                            except json.JSONDecodeError as e:
                                print(f"无法解析JSON: {str(e)}")
                                print(f"原始数据: {json_str[:100]}...")
                                continue

                    print(f"流式处理完成，共处理 {chunk_count} 个数据块")

                    # 一次性解码所有音频数据
                    if audio_string:
                        # 解码base64数据
                        wav_bytes = base64.b64decode(audio_string)
                        # 转换为numpy数组
                        audio_np = np.frombuffer(wav_bytes, dtype=np.int16)
                        # 创建临时内存文件对象
                        audio_buffer = io.BytesIO()
                        # 使用soundfile写入正确格式的wav数据
                        sf.write(audio_buffer, audio_np, samplerate=24000, format="WAV")
                        # 获取处理后的音频数据
                        audio_buffer.seek(0)
                        audio_data = audio_buffer.read()

                        # 保存原始音频（后处理前）
                        self._save_debug_audio(audio_data, text, "raw_")

                        # 应用音频后处理
                        if self.enable_post_processing:
                            print("正在应用音频后处理...")
                            processed_audio_data = self._process_audio(audio_data)
                            # 保存处理后的音频
                            self._save_debug_audio(processed_audio_data, text, "processed_")
                            audio_data = processed_audio_data

                        print(f"成功获取音频数据，总大小: {len(audio_data)} 字节")
                        yield audio_data
                    else:
                        print("警告: 没有收到任何音频数据")
            except Exception as e:
                import traceback

                print(f"流式处理过程中发生错误: {str(e)}")
                print(f"详细错误信息: {traceback.format_exc()}")
                raise

    def _should_use_openai_client(self) -> bool:
        """判断是否应该使用OpenAI客户端"""
        return self.openai_available

    async def _generate_with_openai_client(self, text: str) -> AsyncGenerator[bytes, None]:
        """使用OpenAI客户端生成音频"""
        try:
            from openai import OpenAI

            print("使用OpenAI客户端调用API")
            print(f"PYDUB可用性状态: {PYDUB_AVAILABLE}")
            print(f"后处理功能状态: {self.enable_post_processing}")
            print(f"爆音模式状态: {self.blow_up}")

            client = OpenAI(api_key=self.api_key, base_url=self.base_url)

            print(f"OpenAI请求参数: model={self.model_name}, voice={self.voice}")
            completion = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": text}],
                modalities=["text", "audio"],
                audio={"voice": self.voice, "format": self.format},
                stream=True,
                stream_options={"include_usage": True},
            )

            # 用于累积所有base64字符串
            audio_string = ""

            for chunk in completion:
                if hasattr(chunk, "choices") and chunk.choices:
                    delta = chunk.choices[0].delta
                    # 检查是否有音频数据
                    if hasattr(delta, "audio"):
                        try:
                            # 累积base64字符串
                            if "data" in delta.audio:
                                audio_string += delta.audio["data"]
                            elif "transcript" in delta.audio:
                                print(f"[OpenAI客户端] 收到音频转录文本: {delta.audio['transcript']}")
                        except Exception as e:
                            print(f"[OpenAI客户端] 处理音频数据出错: {str(e)}")

            # 一次性解码所有音频数据
            if audio_string:
                print("准备解码音频数据...")
                # 解码base64数据
                wav_bytes = base64.b64decode(audio_string)
                # 转换为numpy数组
                audio_np = np.frombuffer(wav_bytes, dtype=np.int16)
                # 创建临时内存文件对象
                audio_buffer = io.BytesIO()
                # 使用soundfile写入正确格式的wav数据
                sf.write(audio_buffer, audio_np, samplerate=24000, format="WAV")
                # 获取处理后的音频数据
                audio_buffer.seek(0)
                audio_data = audio_buffer.read()

                print(f"解码成功，原始音频大小: {len(audio_data)} 字节, 爆音状态: {self.blow_up}")

                # 保存原始音频（后处理前）
                self._save_debug_audio(audio_data, text, "raw_openai_")

                # 应用音频后处理 - 无论后处理设置如何，在blow_up模式下都强制应用
                if self.enable_post_processing or self.blow_up:
                    print("[OpenAI客户端] 正在应用音频后处理...")
                    processed_audio_data = self._process_audio(audio_data)
                    # 保存处理后的音频
                    self._save_debug_audio(processed_audio_data, text, "processed_openai_")
                    audio_data = processed_audio_data

                print(f"[OpenAI客户端] 成功获取音频数据，总大小: {len(audio_data)} 字节")
                yield audio_data
            else:
                print("[OpenAI客户端] 警告: 没有收到任何音频数据")

        except ImportError:
            print("OpenAI库未正确安装，无法使用OpenAI客户端")
            raise
        except Exception as e:
            import traceback

            print(f"[OpenAI客户端] 处理过程中发生错误: {str(e)}")
            print(f"[OpenAI客户端] 详细错误信息: {traceback.format_exc()}")
            raise

    def _process_audio(self, audio_data: bytes) -> bytes:
        """
        对音频数据进行后处理（降低音量、添加杂音、柔化声音）

        Args:
            audio_data: 原始音频数据

        Returns:
            处理后的音频数据
        """
        print(
            f"进入_process_audio方法，PYDUB_AVAILABLE={PYDUB_AVAILABLE}, enable_post_processing={self.enable_post_processing}, blow_up={self.blow_up}"
        )

        if not PYDUB_AVAILABLE:
            print("警告: pydub库不可用，无法处理音频")
            return audio_data

        if not self.enable_post_processing and not self.blow_up:
            print("后处理功能未启用且非爆音模式，跳过处理")
            return audio_data

        try:
            # 保证pydub导入成功
            from pydub import AudioSegment
            from pydub.generators import WhiteNoise

            print("开始处理音频数据...")
            # 将音频数据加载到AudioSegment对象
            audio_segment = AudioSegment.from_wav(io.BytesIO(audio_data))
            original_duration = len(audio_segment)
            print(f"原始音频长度: {original_duration}ms")

            # 降低音量
            if self.volume_reduction_db > 0:
                audio_segment = audio_segment - self.volume_reduction_db
                print(f"已降低音量 {self.volume_reduction_db}dB")

            # 爆音模式下额外降低主音频音量，避免太响
            if self.blow_up:
                additional_reduction = 3.0  # 额外降低3dB
                audio_segment = audio_segment - additional_reduction
                print(f"爆音模式：额外降低主音频音量 {additional_reduction}dB")

            # 柔化声音 - 使用低通滤波实现声音钝化和柔化
            # 使用低通滤波器保留低频，降低高频尖锐感
            try:
                # 尝试应用低通滤波
                low_freq = audio_segment.low_pass_filter(2000)  # 2000Hz以下频率通过
                audio_segment = low_freq
                print("已应用低通滤波进行声音柔化")
            except Exception as e:
                print(f"应用低通滤波失败: {str(e)}")

            # 轻微混响效果 - 通过制作一个非常轻微的延迟副本并叠加实现
            try:
                if original_duration > 100 and not self.blow_up:  # 爆音模式下不添加混响
                    # 修复：确保混响不会截断原音频
                    delay_ms = min(10, original_duration // 10)  # 动态调整延迟时间
                    reverb_segment = audio_segment[delay_ms:] - 12  # 使用略延迟的副本，降低12dB

                    # 确保混响不会超出原音频长度
                    if len(reverb_segment) > 0:
                        # 在原音频长度内叠加混响，避免截断
                        audio_segment = audio_segment.overlay(reverb_segment, position=delay_ms)
                        print(f"已添加轻微混响效果 (延迟{delay_ms}ms)")
                    else:
                        print("音频太短，跳过混响效果")
                elif self.blow_up and original_duration > 50:
                    # 爆音模式下添加更强烈的混响
                    delay_ms = min(5, original_duration // 20)  # 动态调整延迟时间
                    reverb_segment = audio_segment[delay_ms:] - 6  # 更短的延迟，更高的音量

                    if len(reverb_segment) > 0:
                        audio_segment = audio_segment.overlay(reverb_segment, position=delay_ms)
                        print(f"已添加适度混响效果 (延迟{delay_ms}ms, -10dB)")
                    else:
                        print("音频太短，跳过爆音混响效果")
            except Exception as e:
                print(f"添加混响效果失败: {str(e)}")

            # 确保处理后的音频长度不短于原始音频
            final_duration = len(audio_segment)
            if final_duration < original_duration:
                print(f"警告: 处理后音频长度变短 ({final_duration}ms < {original_duration}ms)")
                # 可选：用静音填充到原始长度
                if original_duration - final_duration > 10:  # 如果差异超过10ms才填充
                    silence = AudioSegment.silent(duration=original_duration - final_duration)
                    audio_segment = audio_segment + silence
                    print(f"已用静音填充到原始长度 {original_duration}ms")

            # 为所有音频添加0.1-0.3秒的结尾静音，让音频听起来更自然
            ending_silence_duration = random.randint(100, 300)  # 0.1-0.3秒的随机静音
            ending_silence = AudioSegment.silent(duration=ending_silence_duration)
            audio_segment = audio_segment + ending_silence
            print(f"已添加 {ending_silence_duration}ms 的结尾静音")

            # 为短音频（<3秒）添加额外的缓冲静音，防止播放截断
            if original_duration < 3000:  # 少于3秒的短音频
                buffer_duration = min(200, original_duration * 0.1)  # 添加200ms或10%长度的缓冲，取较小值
                buffer_silence = AudioSegment.silent(duration=buffer_duration)
                audio_segment = audio_segment + buffer_silence
                print(f"为短音频添加 {buffer_duration}ms 的缓冲静音，防止播放截断")

            # 现在在完整的音频（包括新增的静音）上添加杂音 - 使用更加温和、自然的噪声
            if self.noise_level > 0 or self.blow_up:
                try:
                    # 使用WhiteNoise并应用低通滤波使其接近粉红噪声效果
                    from pydub.generators import WhiteNoise

                    # 使用当前音频段的长度生成噪声（包括新增的静音）
                    current_duration = len(audio_segment)
                    noise = WhiteNoise().to_audio_segment(duration=current_duration)
                    print(f"已生成覆盖完整音频的基础噪声 (时长: {current_duration}ms)")

                    # 应用强烈的低通滤波使白噪声听起来更接近自然环境噪声
                    if not self.blow_up:  # 只在非爆炸模式下应用滤波
                        try:
                            noise = noise.low_pass_filter(300)  # 更强的滤波，只保留非常低的频率
                            print("已对噪声应用低通滤波")
                        except Exception as e:
                            print(f"滤波白噪声失败: {str(e)}")
                    else:
                        noise = noise.low_pass_filter(1200)  # 更强的滤波，只保留非常低的频率
                        print("爆炸模式：对噪声应用适度滤波")

                    # 调整噪声音量
                    if self.blow_up:
                        # 爆音模式 - 使用适中的噪声强度，避免太刺耳
                        actual_noise_level = random.uniform(0.2, 0.6)  # 降低最大噪声强度
                        # 适度增加噪声音量，但不要太大
                        volume_reduction_db = 15 + (10 * actual_noise_level)  # 15-25dB的降低范围
                        noise = noise - volume_reduction_db
                        audio_segment = audio_segment.overlay(noise)
                        print(f"爆音模式！噪声强度设置为{actual_noise_level * 100:.1f}%，覆盖整个音频包括静音")
                    else:
                        # 正常噪声处理，降低噪声音量
                        # 计算噪声音量降低值（dB）
                        noise_reduction_db = 80 - (self.noise_level * 80)  # noise_level=0时降低80dB, =1时降低0dB
                        noise = noise - noise_reduction_db
                        audio_segment = audio_segment.overlay(noise)
                        print(f"已添加背景噪声覆盖整个音频包括静音 (降低{noise_reduction_db:.1f}dB)")
                except Exception as e:
                    print(f"添加噪声失败: {str(e)}")

            # 转换回字节数据
            output = io.BytesIO()
            audio_segment.export(output, format="wav")
            processed_data = output.getvalue()
            print(f"音频处理完成，处理后大小: {len(processed_data)} 字节")
            return processed_data

        except Exception as e:
            import traceback

            print(f"音频后处理过程中发生错误: {str(e)}")
            print(f"详细错误信息: {traceback.format_exc()}")
            # 出错时返回原始音频
            return audio_data
