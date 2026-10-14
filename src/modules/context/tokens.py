"""上下文 token 计量：按文本缓存分词结果，热路径不重复分词。

预算判断发生在每次请求装配期。长直播的历史有几千条记录，每窗全量分词一遍
是纯浪费：记录一经写入就不再改变，同一段文本的 token 数只需算一次。缓存按
文本本身做键（Python 字符串自带哈希缓存，查表是常数开销），按最近使用逐出。

口径与 ``context_meter`` 一致（tiktoken cl100k，离线退化字符启发式）。对中文
它通常比 DeepSeek / MiniMax 的真实分词偏多，用作预算判断时偏保守——检查点
略早触发，不会撞窗口。图像引用按单张固定估值计。
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Iterable, Mapping

from src.modules.llm.context_meter import count_text_tokens

__all__ = ["TokenCounter", "IMAGE_TOKEN_ESTIMATE"]

# 每条消息的协议包装开销（与 context_meter 同口径）
_TOKENS_PER_MESSAGE = 4

# 单张图像的保守估值；真实成本以 API usage 为准，这里只服务预算判断
IMAGE_TOKEN_ESTIMATE = 1000

# 缓存条数：足够覆盖一场长直播的全部记录与工具声明
_DEFAULT_CACHE_SIZE = 8192


class TokenCounter:
    """带缓存的上下文 token 计量器（单个 Agent 持有一个实例，非线程安全）。"""

    def __init__(self, *, cache_size: int = _DEFAULT_CACHE_SIZE) -> None:
        self._cache_size = cache_size
        self._cache: "OrderedDict[str, int]" = OrderedDict()

    def text(self, text: str) -> int:
        """一段文本的 token 数；同一文本只分词一次。"""
        if not text:
            return 0
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached
        tokens = count_text_tokens(text)
        self._cache[text] = tokens
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return tokens

    def message(self, message: Mapping[str, Any]) -> int:
        """一条 OpenAI 形态消息：正文（文本或分段）+ 工具调用 + 包装开销。

        以下划线开头的键是 Agent 内部标记（模型看不到），不计入。
        """
        total = _TOKENS_PER_MESSAGE + self._content(message.get("content"))
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += self.text(json.dumps(tool_calls, ensure_ascii=False, sort_keys=True, default=str))
        return total

    def messages(self, messages: Iterable[Mapping[str, Any]]) -> int:
        """一组消息的 token 总数。"""
        return sum(self.message(message) for message in messages)

    def tools(self, tools: Iterable[Mapping[str, Any]]) -> int:
        """工具声明的 token 总数：工具声明每次请求都随行，必须计入预算。"""
        return sum(self.text(json.dumps(tool, ensure_ascii=False, sort_keys=True, default=str)) for tool in tools)

    def _content(self, content: Any) -> int:
        """消息正文：字符串直接计；分段列表逐段计，图像引用按固定估值。"""
        if content is None:
            return 0
        if isinstance(content, str):
            return self.text(content)
        if not isinstance(content, list):
            return self.text(str(content))
        total = 0
        for part in content:
            if isinstance(part, str):
                total += self.text(part)
            elif isinstance(part, Mapping):
                kind = part.get("type")
                if kind == "text":
                    total += self.text(str(part.get("text", "")))
                elif kind in ("image_ref", "image_url"):
                    total += IMAGE_TOKEN_ESTIMATE
                else:
                    total += self.text(json.dumps(part, ensure_ascii=False, sort_keys=True, default=str))
        return total
