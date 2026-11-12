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

from typing import Literal, Optional

#: 需要运营介入才能恢复的致命故障类别：余额耗尽（402）、鉴权失败（401/403）、端点持续连不上
FatalFaultKind = Literal["balance", "auth", "unreachable"]

__all__ = [
    "FatalError",
    "FatalFaultKind",
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
    """临时性失败：限流 / 服务端故障 / 网络连接失败 / 空响应，可重试。

    ``is_connection_error`` 标识底层端点连接本身未建立（DNS/TCP/TLS 不可达）；
    Engine 仍按可重试策略在同模型上重试，只有当该用途档全部候选模型走完重试后仍全因连接不通倒下时，
    才将其记为"连不上"致命故障（区别于 429 限流、5xx 临时故障或空响应）。
    """

    default_message = "临时性失败，可重试"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        original: Optional[BaseException] = None,
        is_connection_error: bool = False,
    ) -> None:
        super().__init__(message, original=original)
        self.is_connection_error = is_connection_error


class FatalError(LLMError):
    """不可恢复失败：请求参数错误 / 鉴权失败等，重试无意义。

    ``status_code`` 携带触发本错误的 HTTP 状态码（来源是服务端状态拒绝时）；
    非状态拒绝路径（未识别的 SDK 异常等）为 None。
    """

    default_message = "不可恢复失败，重试无意义"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        original: Optional[BaseException] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message, original=original)
        self.status_code = status_code


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
