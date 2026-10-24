"""LLM 错误分类体系。

Client 把底层失败（SDK 异常 / 空响应等）翻译为这里的分类异常后抛出；
Engine 按分类做表驱动的重试决策，只消费分类、不对异常做二次判定。

四类语义：

- ``RetryableError``：临时性失败（限流 429 / 服务端 5xx / 网络连接失败 /
  空响应），同一模型上有上限重试
- ``FatalError``：不可恢复失败（参数错误 400 / 鉴权失败 401 等），
  重试无意义，直接切下一个模型
- ``LLMTimeoutError``：单次请求超时，切下一个模型
- ``LLMInterruptedError``：调用被外部主动中断，整体中止并向调用方传播
"""

from __future__ import annotations

from typing import Optional

__all__ = [
    "FatalError",
    "LLMError",
    "LLMInterruptedError",
    "LLMTimeoutError",
    "RetryableError",
    "UnsendableRequestError",
]


class LLMError(Exception):
    """LLM 调用分类异常基类：携带原因描述与原始异常。"""

    default_message: str = "LLM 调用失败"

    def __init__(self, message: Optional[str] = None, *, original: Optional[BaseException] = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.original = original

    def __str__(self) -> str:
        return self.message


class RetryableError(LLMError):
    """临时性失败：限流 / 服务端故障 / 网络连接失败 / 空响应，可重试。"""

    default_message = "临时性失败，可重试"


class FatalError(LLMError):
    """不可恢复失败：请求参数错误 / 鉴权失败等，重试无意义。"""

    default_message = "不可恢复失败，重试无意义"


class UnsendableRequestError(ValueError):
    """请求在装配期就被拒：内容本身发不出去（文本里夹着长串 base64），原样重发必然再被拒。

    在发出之前抛出，不经过故障切换；调用方要么改掉请求里的内容再发，要么放弃这份请求。
    继承 ValueError：它本质是调用方给了一份不合法的输入。
    """


class LLMTimeoutError(LLMError):
    """单次请求超时（含客户端读超时与 SDK 传输超时）。"""

    default_message = "LLM 请求超时"


class LLMInterruptedError(LLMError):
    """调用被外部主动中断，应整体中止并向上传播。"""

    default_message = "LLM 调用被中断"
