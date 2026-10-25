"""主播素材发布器：身体事件原文 → game.attention_required，长文先概述。

必须传 Mod 原话给主播：白话速览的分句改写器会整段丢弃排查类分句
（实测 30 句输入只剩一句"有件事没办成"），排查细节全灭。超过阈值的长
自由文本（need_unhandled 的逐来源排查说明、目标失败枚举等）先用
summary 档压成保信息摘要再发，载荷两栏——概述进 ``message``，原文留档
进 ``scene``。

发布是后台任务（不阻塞事件主流程）：持引用防被垃圾回收，完成即移除。
概述拿不到（LLM 异常 / 响应不可用 / 未注入）静默降级为首句斩尾并记
debug 日志——素材宁可降级也不能不发。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from src.modules.logging import get_logger

__all__ = ["AttentionMaterialPublisher"]

# 长自由文本事件的概述阈值：发给主播素材前超过它先用 summary 档压成保信息摘要，
# 短文直通（进展类事件中位几十字符，天然分开）
_SUMMARY_THRESHOLD_CHARS = 300
# 概述降级的首句斩尾上限：只留第一句，再多截断（斩尾是兜底，不弃用）
_SUMMARY_FALLBACK_MAX_CHARS = 120
# 概述走 summary 档（低成本，每场几次调用）
_SUMMARY_PROFILE = "summary"

# 概述提示词：只压缩不评价——防多出一个自由发挥的转述者
_SUMMARY_COMPRESS_INSTRUCTION = (
    "把下面这段 Minecraft 游戏事件长文压缩成一段保信息摘要：保留目标、试过的办法、"
    "失败或缺的东西、数量与下一步指向等全部事实细节；只压缩不评价——不加评论、"
    "不臆测、不改变任何事实。直接输出摘要正文。"
)

logger = get_logger("AttentionMaterial")

# 素材出口：(message, scene=None) → 发一条 game.attention_required
EmitMaterial = Callable[..., Awaitable[None]]
# LLM 取值回调：每次概述时现取（Agent 侧替换 LLM 后概述随之走新实例）
LlmGetter = Callable[[], Optional[Any]]


def _first_sentence_truncation(text: str) -> str:
    """概述的降级形态：只留第一句，句子本身太长再硬截断。"""
    stripped = str(text or "").strip()
    for separator in ("。", "！", "？", "!?", "\n"):
        index = stripped.find(separator)
        if index >= 0:
            stripped = stripped[: index + len(separator)]
            break
    if len(stripped) > _SUMMARY_FALLBACK_MAX_CHARS:
        stripped = stripped[:_SUMMARY_FALLBACK_MAX_CHARS]
    return stripped


class AttentionMaterialPublisher:
    """身体事件原文 → 主播素材。非线程安全；仅在 Agent 事件循环内使用。"""

    def __init__(self, *, llm_getter: LlmGetter, emit: EmitMaterial) -> None:
        self._llm_getter = llm_getter
        self._emit = emit
        # 在跑的发布后台任务：持引用防被垃圾回收，完成即移除
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, message: str) -> None:
        """排一条素材发布：短文直通，长文先概述；异步执行不阻塞调用方。"""
        if len(message) <= _SUMMARY_THRESHOLD_CHARS:
            task = asyncio.create_task(self._emit(message))
        else:
            task = asyncio.create_task(self._emit_with_summary(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _emit_with_summary(self, message: str) -> None:
        """长文的两栏载荷：压缩概述进素材，原文留档；概述拿不到就降级首句斩尾。"""
        summary = await self._summarize(message)
        await self._emit(summary, scene=f"原文留档：{message}")

    async def _summarize(self, message: str) -> str:
        """长自由文本 → 保信息摘要（summary 档）；失败降级首句斩尾（debug 记原因）。"""
        llm = self._llm_getter()
        if llm is None:
            return _first_sentence_truncation(message)
        try:
            response = await llm.generate(
                [{"role": "user", "content": f"{_SUMMARY_COMPRESS_INSTRUCTION}\n\n{message}"}],
                profile=_SUMMARY_PROFILE,
            )
        except Exception as exc:  # noqa: BLE001 - 概述失败静默降级，不影响事件主流程
            logger.debug(f"长文概述调用失败，降级首句斩尾: {type(exc).__name__}: {exc}", exc=True)
            return _first_sentence_truncation(message)
        summary = (response.content or "").strip()
        if response.success and summary and not response.tool_calls:
            return summary
        logger.debug(
            f"长文概述响应不可用（success={response.success}，带工具调用={bool(response.tool_calls)}），降级首句斩尾"
        )
        return _first_sentence_truncation(message)
