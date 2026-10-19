"""主播备忘录：会话内的工作笔记（进程内状态，不入库）。

定位：主播中期记忆的载体——模型经 ``streamer_notebook`` 工具主动写，主播 Agent
每轮把它注入决策与表达两层上下文；收播时全文作为回顾生成原料之一折叠进下场
回顾，随后清空。与游戏侧 ``minecraft_notebook`` 同构：单条自由文本、全量覆盖
写（无参调用即读回全文）、内容纪律是"沉淀结论，不沉淀流水"。

生命周期：进程内、场次作用域——``live.ended`` 时主播 Agent 取走全文快照后清空；
不写存储表，重启即空（跨场记忆由场次回顾承担，备忘录只管本场）。
"""

from __future__ import annotations

from src.modules.logging import get_logger

__all__ = ["DEFAULT_NOTEBOOK_MAX_CHARS", "StreamerNotebook"]

logger = get_logger("StreamerNotebook")

# 容量上限默认值（字符）。备忘录是覆盖写的结论集而非流水，量级天然小；
# 每轮全量注入决策与表达两层，2000 字符约一千多 token，落在尾部参考消息里
# 可接受。超限截断保头部（写入方自行组织的新全文，头部通常是最新整理结果）。
DEFAULT_NOTEBOOK_MAX_CHARS = 2000


class StreamerNotebook:
    """本场备忘录状态：单条自由文本，全量覆盖写，容量按字符数封顶。"""

    def __init__(self, *, max_chars: int = DEFAULT_NOTEBOOK_MAX_CHARS) -> None:
        self._max_chars: int = max(100, int(max_chars))
        self._content: str = ""

    def read(self) -> str:
        """读回备忘录全文（空 = 空串，由调用方决定空段省略或占位）。"""
        return self._content

    def set(self, content: str) -> str:
        """全量覆盖写；超上限截断并告警，返回实际保存的全文（写回执即确认）。"""
        text = str(content)
        if len(text) > self._max_chars:
            logger.warning(f"备忘录写入超上限，已截断：{len(text)} -> {self._max_chars} 字符")
            text = text[: self._max_chars]
        self._content = text
        return self._content

    def clear(self) -> None:
        """清空（场次结束时的生命周期钩子；快照由调用方先行取走）。"""
        self._content = ""
