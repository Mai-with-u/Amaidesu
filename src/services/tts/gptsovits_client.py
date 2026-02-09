"""
GPT-SoVITS TTS 客户端

提供 GPT-SoVITS API 客户端实现：
- 支持同步和流式 TTS
- 参考音频管理
- 预设管理
"""

import re
from typing import Optional, Dict, Any, Iterator
import requests

from src.core.utils.logger import get_logger


class GPTSoVITSClient:
    """
    GPT-SoVITS TTS 客户端

    封装 GPT-SoVITS API 调用逻辑，提供同步和流式 TTS 功能。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9880):
        """
        初始化 GPT-SoVITS 客户端

        Args:
            host: API 服务器地址
            port: API 服务器端口
        """
        self.host = host
        self.port = port
        self.base_url = f"http://{self.host}:{self.port}"
        self.logger = get_logger("GPTSoVITSClient")

        # 参考音频配置
        self._ref_audio_path: Optional[str] = None
        self._prompt_text: str = ""

        # 预设管理
        self._current_preset: str = "default"
        self._initialized: bool = False

        # 请求配置
        self._timeout = (3.05, 60)  # (连接超时, 读取超时)

    def initialize(self) -> None:
        """初始化客户端"""
        if self._initialized:
            return
        self._initialized = True
        self.logger.info(f"GPTSoVITSClient 初始化完成: {self.base_url}")

    def load_preset(self, preset_name: str = "default") -> None:
        """
        加载指定名称的角色预设

        Args:
            preset_name: 预设名称
        """
        if not self._initialized:
            self.initialize()

        self._current_preset = preset_name
        self.logger.debug(f"加载预设: {preset_name}")

    def set_refer_audio(self, audio_path: str, prompt_text: str) -> None:
        """
        设置参考音频和对应的提示文本

        Args:
            audio_path: 参考音频文件路径
            prompt_text: 提示文本

        Raises:
            ValueError: 如果参数为空
        """
        if not audio_path:
            raise ValueError("audio_path 不能为空")
        if not prompt_text:
            raise ValueError("prompt_text 不能为空")

        self._ref_audio_path = audio_path
        self._prompt_text = prompt_text
        self.logger.debug(f"设置参考音频: {audio_path}")

    def set_gpt_weights(self, weights_path: str) -> None:
        """
        设置 GPT 权重

        Args:
            weights_path: 权重文件路径

        Raises:
            Exception: 如果设置失败
        """
        response = requests.get(
            f"{self.base_url}/set_gpt_weights", params={"weights_path": weights_path}, timeout=self._timeout[0]
        )
        if response.status_code != 200:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"设置 GPT 权重失败: {error_msg}")
        self.logger.info(f"GPT 权重已更新: {weights_path}")

    def set_sovits_weights(self, weights_path: str) -> None:
        """
        设置 SoVITS 权重

        Args:
            weights_path: 权重文件路径

        Raises:
            Exception: 如果设置失败
        """
        response = requests.get(
            f"{self.base_url}/set_sovits_weights", params={"weights_path": weights_path}, timeout=self._timeout[0]
        )
        if response.status_code != 200:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"设置 SoVITS 权重失败: {error_msg}")
        self.logger.info(f"SoVITS 权重已更新: {weights_path}")

    def _detect_language(self, text: str, text_lang: str) -> str:
        """
        检测文本语言

        Args:
            text: 输入文本
            text_lang: 指定的语言模式

        Returns:
            检测后的语言代码
        """
        if text_lang == "auto":
            has_english = bool(re.search(r"[a-zA-Z]", text))
            if not has_english:
                return "zh"
            return "en"
        return text_lang

    def _build_params(
        self,
        text: str,
        ref_audio_path: Optional[str] = None,
        text_lang: Optional[str] = None,
        prompt_text: Optional[str] = None,
        prompt_lang: Optional[str] = None,
        top_k: Optional[int] = None,
        top_p: Optional[float] = None,
        temperature: Optional[float] = None,
        text_split_method: Optional[str] = None,
        batch_size: Optional[int] = None,
        batch_threshold: Optional[float] = None,
        speed_factor: Optional[float] = None,
        streaming_mode: bool = True,
        media_type: str = "wav",
        repetition_penalty: Optional[float] = None,
        sample_steps: Optional[int] = None,
        super_sampling: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        构建 TTS 请求参数

        Args:
            text: 输入文本
            ref_audio_path: 参考音频路径
            text_lang: 文本语言
            prompt_text: 提示文本
            prompt_lang: 提示语言
            top_k: Top-K 采样
            top_p: Top-P 采样
            temperature: 温度参数
            text_split_method: 文本分割方法
            batch_size: 批处理大小
            batch_threshold: 批处理阈值
            speed_factor: 语速因子
            streaming_mode: 是否流式
            media_type: 媒体类型
            repetition_penalty: 重复惩罚
            sample_steps: 采样步数
            super_sampling: 超采样

        Returns:
            请求参数字典
        """
        # 使用传入的 ref_audio_path 和 prompt_text，否则使用持久化的值
        ref_audio_path = ref_audio_path or self._ref_audio_path
        if not ref_audio_path:
            raise ValueError("未设置参考音频，请先调用 set_refer_audio 设置参考音频和提示文本")

        prompt_text = prompt_text if prompt_text is not None else self._prompt_text

        # 语言检测
        if text_lang:
            text_lang = self._detect_language(text, text_lang)

        return {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang or "zh",
            "top_k": top_k,
            "top_p": top_p,
            "temperature": temperature,
            "text_split_method": text_split_method,
            "batch_size": batch_size,
            "batch_threshold": batch_threshold,
            "speed_factor": speed_factor,
            "streaming_mode": streaming_mode,
            "media_type": media_type,
            "repetition_penalty": repetition_penalty,
            "sample_steps": sample_steps,
            "super_sampling": super_sampling if super_sampling is not None else True,
        }

    def tts(
        self,
        text: str,
        ref_audio_path: Optional[str] = None,
        text_lang: Optional[str] = None,
        prompt_text: Optional[str] = None,
        prompt_lang: Optional[str] = None,
        top_k: Optional[int] = None,
        top_p: Optional[float] = None,
        temperature: Optional[float] = None,
        text_split_method: Optional[str] = None,
        batch_size: Optional[int] = None,
        batch_threshold: Optional[float] = None,
        speed_factor: Optional[float] = None,
        streaming_mode: bool = False,
        media_type: str = "wav",
        repetition_penalty: Optional[float] = None,
        sample_steps: Optional[int] = None,
        super_sampling: Optional[bool] = None,
    ) -> bytes:
        """
        同步文本转语音

        Args:
            text: 输入文本
            ref_audio_path: 参考音频路径
            text_lang: 文本语言
            prompt_text: 提示文本
            prompt_lang: 提示语言
            top_k: Top-K 采样
            top_p: Top-P 采样
            temperature: 温度参数
            text_split_method: 文本分割方法
            batch_size: 批处理大小
            batch_threshold: 批处理阈值
            speed_factor: 语速因子
            streaming_mode: 是否流式
            media_type: 媒体类型
            repetition_penalty: 重复惩罚
            sample_steps: 采样步数
            super_sampling: 超采样

        Returns:
            音频数据（bytes）

        Raises:
            Exception: 如果 TTS 失败
        """
        if not self._initialized:
            self.initialize()

        params = self._build_params(
            text=text,
            ref_audio_path=ref_audio_path,
            text_lang=text_lang,
            prompt_text=prompt_text,
            prompt_lang=prompt_lang,
            top_k=top_k,
            top_p=top_p,
            temperature=temperature,
            text_split_method=text_split_method,
            batch_size=batch_size,
            batch_threshold=batch_threshold,
            speed_factor=speed_factor,
            streaming_mode=streaming_mode,
            media_type=media_type,
            repetition_penalty=repetition_penalty,
            sample_steps=sample_steps,
            super_sampling=super_sampling,
        )

        response = requests.get(
            f"{self.base_url}/tts",
            params=params,
            timeout=self._timeout[1],
        )

        if response.status_code != 200:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"TTS 请求失败: {error_msg}")

        self.logger.debug(f"TTS 合成完成: {len(response.content)} 字节")
        return response.content

    def tts_stream(
        self,
        text: str,
        ref_audio_path: Optional[str] = None,
        text_lang: Optional[str] = None,
        prompt_text: Optional[str] = None,
        prompt_lang: Optional[str] = None,
        top_k: Optional[int] = None,
        top_p: Optional[float] = None,
        temperature: Optional[float] = None,
        text_split_method: Optional[str] = None,
        batch_size: Optional[int] = None,
        batch_threshold: Optional[float] = None,
        speed_factor: Optional[float] = None,
        media_type: str = "wav",
        repetition_penalty: Optional[float] = None,
        sample_steps: Optional[int] = None,
        super_sampling: Optional[bool] = None,
    ) -> Iterator[bytes]:
        """
        流式文本转语音

        Args:
            text: 输入文本
            ref_audio_path: 参考音频路径
            text_lang: 文本语言
            prompt_text: 提示文本
            prompt_lang: 提示语言
            top_k: Top-K 采样
            top_p: Top-P 采样
            temperature: 温度参数
            text_split_method: 文本分割方法
            batch_size: 批处理大小
            batch_threshold: 批处理阈值
            speed_factor: 语速因子
            media_type: 媒体类型
            repetition_penalty: 重复惩罚
            sample_steps: 采样步数
            super_sampling: 超采样

        Yields:
            音频数据块（bytes）

        Raises:
            Exception: 如果 TTS 失败
        """
        if not self._initialized:
            self.initialize()

        params = self._build_params(
            text=text,
            ref_audio_path=ref_audio_path,
            text_lang=text_lang,
            prompt_text=prompt_text,
            prompt_lang=prompt_lang,
            top_k=top_k,
            top_p=top_p,
            temperature=temperature,
            text_split_method=text_split_method,
            batch_size=batch_size,
            batch_threshold=batch_threshold,
            speed_factor=speed_factor,
            streaming_mode=True,
            media_type=media_type,
            repetition_penalty=repetition_penalty,
            sample_steps=sample_steps,
            super_sampling=super_sampling,
        )

        response = requests.get(
            f"{self.base_url}/tts",
            params=params,
            stream=True,
            timeout=self._timeout,
            headers={"Connection": "keep-alive"},
        )

        if response.status_code != 200:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"流式 TTS 请求失败: {error_msg}")

        self.logger.debug(f"开始流式 TTS: {text[:50]}...")
        return response.iter_content(chunk_size=4096)

    def check_connection(self) -> bool:
        """
        检查与 GPT-SoVITS 服务器的连接

        Returns:
            是否连接成功
        """
        try:
            response = requests.get(f"{self.base_url}/", timeout=3)
            is_connected = response.status_code == 200
            if is_connected:
                self.logger.debug("GPT-SoVITS 服务器连接正常")
            else:
                self.logger.warning(f"GPT-SoVITS 服务器响应异常: {response.status_code}")
            return is_connected
        except Exception as e:
            self.logger.error(f"检查 GPT-SoVITS 连接失败: {e}")
            return False
