"""
基于阿里云 DashScope Fun-ASR 的实时识别封装。

参考文档: https://help.aliyun.com/zh/model-studio/fun-asr-realtime-python-sdk

说明:
- 通过 Recognition.start()/send_audio_frame()/stop() 完成流式识别
- 提供简单回调: on_sentence(text, is_final), on_error(msg)
"""

from typing import Optional, Callable

from http import HTTPStatus

try:
    from dashscope.audio.asr import Recognition, RecognitionCallback
except Exception:  # pragma: no cover
    Recognition = None  # type: ignore
    RecognitionCallback = object  # type: ignore


OnSentenceCallback = Callable[[str, bool], None]
OnErrorCallback = Callable[[str], None]


class _Callback(RecognitionCallback):
    def __init__(self, on_sentence: Optional[OnSentenceCallback], on_error: Optional[OnErrorCallback]):
        self.on_sentence = on_sentence
        self.on_error = on_error

    def on_open(self) -> None:
        pass

    def on_event(self, result) -> None:
        try:
            if result and self.on_sentence:
                sentence = result.get_sentence_dict()
                text = sentence.get("text", "") if isinstance(sentence, dict) else result.get_sentence()
                is_final = result.is_sentence_end(sentence) if hasattr(result, "is_sentence_end") else False
                if text:
                    self.on_sentence(text, bool(is_final))
        except Exception as e:  # pragma: no cover
            if self.on_error:
                self.on_error(f"callback error: {e}")

    def on_complete(self) -> None:
        if self.on_sentence:
            self.on_sentence("", True)

    def on_error(self, error: str) -> None:  # type: ignore[override]
        if self.on_error:
            self.on_error(str(error))


class FunASRRecognizer:
    def __init__(
        self,
        model: str = "fun-asr-realtime",
        sample_rate: int = 16000,
        audio_format: str = "pcm",
        on_sentence: Optional[OnSentenceCallback] = None,
        on_error: Optional[OnErrorCallback] = None,
    ) -> None:
        if Recognition is None:
            raise RuntimeError("dashscope 未安装或不可用。请先安装并配置 DASHSCOPE_API_KEY 环境变量。")

        self.sample_rate = sample_rate
        self.audio_format = audio_format
        self._callback = _Callback(on_sentence, on_error)

        self._recognition = Recognition(
            model=model,
            format=audio_format,
            sample_rate=sample_rate,
            callback=self._callback,
        )

    def start(self) -> None:
        self._recognition.start()

    def send_audio_frame(self, data: bytes) -> None:
        self._recognition.send_audio_frame(data)

    def stop(self) -> None:
        self._recognition.stop()

    # 同步识别本地文件（可选）
    def transcribe_file(self, path: str) -> str:
        result = self._recognition.call(path)
        if getattr(result, "status_code", None) == HTTPStatus.OK:
            return result.get_sentence()
        raise RuntimeError(getattr(result, "message", "recognition failed"))


