"""
GPT-SoVITS TTS 客户端（api_v2 方言）

对接 GPT-SoVITS api_v2.py 的 ``GET /tts`` 接口：参考音频与采样参数
逐请求携带（v1 的"服务端启动参数绑定默认参考音频"模式已不适用），
合成参数（top_k/temperature/speed 等）在 v2 下真实生效。

- 支持同步和流式 TTS（流式 = 首块 WAV 头 + 裸 PCM 块序列）
- 预设管理
"""

import re
from typing import Any, Dict, Iterator, NoReturn, Optional

import requests

from src.modules.logging import get_logger


class GPTSoVITSServiceError(Exception):
    """GPT-SoVITS 服务不可达 / 请求失败。

    requests 的连接类异常字符串携带完整 URL 与底层连接池细节，直接抛出
    会把日志刷成数百行 traceback；本异常只保留对排查有用的简短信息
    （服务地址 + 失败类别），原始异常经 ``__cause__`` 保留供调试器查看。
    """


class GPTSoVITSClient:
    """
    GPT-SoVITS TTS 客户端

    封装 GPT-SoVITS api_v2 调用逻辑，提供同步和流式 TTS 功能。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9880) -> None:
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

        # 参考音频配置（api_v2 每请求必填，由 provider 从配置注入）
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
        self.logger.info(f"GPTSoVITSClient 初始化完成: {self.base_url}（api_v2）")

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
        设置参考音频和对应的提示文本（每请求携带）

        Args:
            audio_path: 参考音频文件路径（相对 GPT-SoVITS 根目录）
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

    def set_refer_audio_remote(self, ref_audio_path: str, prompt_text: str = "", prompt_language: str = "zh") -> None:
        """
        通过 api_v2 的 /set_refer_audio 接口远程预热参考音频。

        v2 端点只收路径（服务端做预处理缓存）；提示文本在 v2 下
        逐请求携带，不再有"远程设置默认提示文本"的概念。

        Args:
            ref_audio_path: 参考音频路径(相对于 GPT-SoVITS 根目录)
            prompt_text: 兼容旧签名的未用参数
            prompt_language: 兼容旧签名的未用参数

        Raises:
            Exception: 如果设置失败
        """
        response = requests.get(
            f"{self.base_url}/set_refer_audio",
            params={"refer_audio_path": ref_audio_path},
            timeout=self._timeout[0],
        )
        if response.status_code != 200:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"远程设置参考音频失败: {error_msg}")
        # 同步到本地（v2 端点不收 prompt，只记录路径；prompt 仍逐请求提供）
        self._ref_audio_path = ref_audio_path
        if prompt_text:
            self._prompt_text = prompt_text
        self.logger.info(f"远程参考音频已预热: {ref_audio_path}")

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
        streaming_mode: int = 0,
        media_type: str = "wav",
        repetition_penalty: Optional[float] = None,
        sample_steps: Optional[int] = None,
        super_sampling: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """构建 api_v2 ``/tts`` 请求参数。

        api_v2 语义：参考音频三键（ref_audio_path/prompt_text/prompt_lang）
        每请求必填；采样与语速参数真实生效，None 值不携带、由服务端默认兜底。
        streaming_mode 为 api_v2 档位（0=关闭 1=按句 2=token级 3=token级定长chunk）。
        """
        # v1 的"省略三键用服务端默认"模式在 v2 不存在：ref_audio_path 必填
        effective_ref = ref_audio_path or self._ref_audio_path
        if not effective_ref:
            raise ValueError(
                "ref_audio_path 不能为空：api_v2 每请求必填参考音频，"
                "请在配置 [tts.gptsovits] 填写 ref_audio_path 与 prompt_text"
            )
        effective_prompt = prompt_text if prompt_text is not None else self._prompt_text

        params: Dict[str, Any] = {
            "text": text,
            "text_lang": self._detect_language(text, text_lang or "zh"),
            "ref_audio_path": effective_ref,
            "prompt_lang": prompt_lang or "zh",
            "streaming_mode": streaming_mode,
            "media_type": media_type,
        }
        if effective_prompt:
            params["prompt_text"] = effective_prompt

        # 可选采样参数：显式给出才携带（服务端对各值域有默认与校验）
        optional_args = {
            "top_k": top_k,
            "top_p": top_p,
            "temperature": temperature,
            "text_split_method": text_split_method,
            "batch_size": batch_size,
            "batch_threshold": batch_threshold,
            "speed_factor": speed_factor,
            "repetition_penalty": repetition_penalty,
            "sample_steps": sample_steps,
            "super_sampling": super_sampling,
        }
        for key, value in optional_args.items():
            if value is not None:
                params[key] = value
        return params

    def _raise_service_error(self, e: requests.exceptions.RequestException) -> NoReturn:
        """把 requests 连接类异常转译为简短业务异常（服务未启动时避免日志刷屏）"""
        if isinstance(e, requests.exceptions.ConnectionError):
            raise GPTSoVITSServiceError(f"GPT-SoVITS 服务不可达 ({self.base_url})，请确认服务已启动") from e
        if isinstance(e, requests.exceptions.Timeout):
            raise GPTSoVITSServiceError(f"GPT-SoVITS 服务请求超时 ({self.base_url})") from e
        raise GPTSoVITSServiceError(f"GPT-SoVITS 服务请求失败 ({type(e).__name__})") from e

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
        streaming_mode: int = 0,
        media_type: str = "wav",
        repetition_penalty: Optional[float] = None,
        sample_steps: Optional[int] = None,
        super_sampling: Optional[bool] = None,
    ) -> bytes:
        """同步文本转语音（一次性返回完整音频）"""
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

        try:
            response = requests.get(
                f"{self.base_url}/tts",
                params=params,
                timeout=self._timeout[1],
            )
        except requests.exceptions.RequestException as e:
            self._raise_service_error(e)

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
        streaming_mode: int = 1,
    ) -> Iterator[bytes]:
        """流式文本转语音。

        streaming_mode 传 api_v2 档位（1=按句，2/3=token级）。媒体形态均为
        wav：首块 44 字节 WAV 头 + 后续裸 PCM——``decode_wav_chunk`` 的既有
        分支持此形态。
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

        try:
            response = requests.get(
                f"{self.base_url}/tts",
                params=params,
                stream=True,
                timeout=self._timeout,
                headers={"Connection": "keep-alive"},
            )
        except requests.exceptions.RequestException as e:
            self._raise_service_error(e)

        if response.status_code != 200:
            error_msg = response.json().get("message", "Unknown error")
            raise Exception(f"流式 TTS 请求失败: {error_msg}")

        self.logger.debug(f"开始流式 TTS: {text[:50]}...")
        return response.iter_content(chunk_size=4096)

    def check_connection(self) -> bool:
        """检查与 GPT-SoVITS 服务器的连接。

        api_v2 没有专门探活端点：无参 ``GET /tts`` 会被应用层参数校验
        以 4xx/5xx 拒绝——但只要收到任何 HTTP 响应就证明服务进程可达，
        仅连接类异常（拒连/超时）判不可达。
        """
        try:
            response = requests.get(f"{self.base_url}/tts", timeout=3)
            self.logger.debug(f"GPT-SoVITS 服务器可达（HTTP {response.status_code}）")
            return True
        except Exception as e:
            self.logger.error(f"检查 GPT-SoVITS 连接失败: {e}")
            return False
