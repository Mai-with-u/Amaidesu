"""字幕后端 Backend 协议（结构类型契约）

字幕基础设施包对外暴露的核心类型契约。任何实现了 ``show`` / ``clear``
两个方法的类都被视为满足契约，无需显式继承本协议——这是 Python
结构类型系统的 duck-typed 风格。

设计约束：

- **不做流式**：``show`` 一次性推送全量文本，不接受 chunk / token 粒度
  调用。打字机观感由前端模板动画承担（前端用整段文本做字符级渐进
  渲染），后端不负责流式。
- **不抽基类**：不同后端执行模型差异大，强抽基类 = 抽象泄漏；
  只用结构类型协议。
- **运行时校验仅用于 fail-fast**：``@runtime_checkable`` 让
  ``isinstance`` 在装配边界兜底，常规调用路径仍走静态类型收窄。
- **错误传播交由调用方决定**：单后端的异常由 ``SubtitleService`` 在
  并行广播时捕获并记 ERROR（隔离故障，不拖垮其他后端）；协议本身
  不规定 ``show`` / ``clear`` 失败时的抛错语义。

调用入口 ``SubtitleService.show(text, utterance_id, reply_quote)`` / ``clear()``
向已注册的全部 Backend 并行广播。

成员（与 ``SubtitleService`` 调用契约一一对应）：

- ``async def show(text, utterance_id=None, reply_quote=None) -> None`` 推送一次字幕
- ``async def clear() -> None`` 清空字幕

参考模式：TTS 包的 ``TTSProvider``。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ReplyQuote(BaseModel):
    """本句字幕在回复的那条观众发言（昵称 + 原文）。

    由决策层在解析回复目标时从本轮弹幕批顺带取出，沿
    ``SubtitleService.show`` 链显式传参到各展示位；无回复目标或解析
    不中时整份为 None（宁缺毋假，不显示引用行）。
    """

    user_name: str = Field(description="被回复弹幕的发送者昵称")
    text: str = Field(description="被回复弹幕的原文")


@runtime_checkable
class SubtitleBackend(Protocol):
    """字幕后端协议（结构类型，``@runtime_checkable``）。

    任何实现了 ``show`` / ``clear`` 的类都被视为满足契约，无需显式
    继承本协议。装配层在 Backend 注册时（``SubtitleService.register_backend``
    内部）用 ``isinstance(backend, SubtitleBackend)`` 兜底校验；
    ``@runtime_checkable`` 只检查成员存在性，不校验类型签名——避免
    给鸭子类型增加运行期负担。
    """

    async def show(
        self,
        text: str,
        utterance_id: Optional[str] = None,
        reply_quote: Optional[ReplyQuote] = None,
    ) -> None:
        """一次性推送全量字幕文本。

        Args:
            text: 要显示的字幕内容。后端应整体替换当前显示（不接受
                chunk / token 流式）。
            utterance_id: 一次发声实例的唯一 ID；非 None 时供编排层
                关联 ``tts.utterance`` / ``streamer.speech`` 事件使用
                （前后端可借此对齐"哪段语音对应哪段字幕"）；None 时
                表示手动 / 直调场景，无 utterance 上下文。
            reply_quote: 本句在回复的那条观众发言（昵称 + 原文）；
                None 表示无回复目标（主动发言或未解析到），展示位不
                渲染引用行。

        错误语义：实现可在内部抛任意异常，``SubtitleService`` 会捕获
        隔离并记 ERROR（不影响其他 Backend）。
        """
        ...

    async def clear(self) -> None:
        """清空当前字幕显示。

        错误语义：同 ``show``——异常被 ``SubtitleService`` 隔离。
        """
        ...


__all__ = ["SubtitleBackend"]
