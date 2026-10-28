"""台词出口守卫：一句台词能不能出声的裁决规则单点收拢。

三检齐备——长度预算（截到预算内最后一个完整句子）、敏感词净化（替换或
整条丢弃）、重合度闸（对最近已说发言的 bigram 包含率，判定委托
:class:`SpeechGate`）。词汇定义与判定分支集中在本模块单一实现；
各调用点按自身语义组合取用：

- Replyer：长度预算先走 LLM 重说（表达策略，留在 Replyer 内部），
  截短兜底用 :func:`fit_to_budget`；出口只做敏感词净化（无重合度闸）。
- Mouth：三检按"截短 → 净化 → 重合度"顺序全跑，任一不过整句丢弃。
- reply 工具：只用重合度闸（打回重试的回执机制留在工具内）。

各检既可整体取用（:func:`guard`），也可单独取用（:func:`apply_word_filter`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .topic_gate import SpeechGate

__all__ = [
    "GuardVerdict",
    "WordFilter",
    "apply_word_filter",
    "fit_to_budget",
    "guard",
]


# ============================================================================
# 长度预算
# ============================================================================

# 一个完整句子：至少一个非标点字，到句末标点（或全文结尾）为止；开头的"……"跟着后面的字算同一句。
# 截短时只在完整句子之后下刀，不把一句话截成半截
_SENTENCE = re.compile(r"[。！？!?…~～]*[^。！？!?…~～]+(?:[。！？!?…~～]+|$)")


def fit_to_budget(speech: str, budget: int) -> str:
    """把台词截到长度预算内：去掉分段换行，从头累加完整句子，再加一句就超预算时停下。

    第一句本身就超预算时整句保留——宁可长一点，也不把一句话截成半截。
    """
    text = re.sub(r"\s*\n+\s*", "", speech).strip()
    if len(text) <= budget:
        return text
    kept = ""
    for sentence in _SENTENCE.findall(text):
        if kept and len(kept) + len(sentence) > budget:
            break
        kept += sentence
    # 全是标点（切不出句子）时原样返回，不把台词截成空串
    return kept or text


# ============================================================================
# 敏感词净化
# ============================================================================


class WordFilter:
    """敏感词过滤器（"嘴"端净化职责）。

    配置项：
    - ``enabled``（bool）：总开关
    - ``words``（List[str]）：敏感词表
    - ``replacement``（str）：替换字符（默认 ``***``）
    - ``case_sensitive``（bool）：是否大小写敏感
    - ``drop_on_match``（bool）：命中时是否整条丢弃（True → 返回 None）

    使用示例：
        >>> flt = WordFilter(words=["脏话A", "脏话B"], replacement="***", drop_on_match=False)
        >>> cleaned, dropped = flt.filter("这是一条脏话A测试")
        >>> cleaned
        '这是一条***测试'
        >>> dropped
        True
    """

    def __init__(
        self,
        *,
        words: Optional[List[str]] = None,
        replacement: str = "***",
        case_sensitive: bool = False,
        drop_on_match: bool = False,
        enabled: bool = True,
    ) -> None:
        """初始化敏感词过滤器。

        Args:
            words: 敏感词列表；None/空时过滤器不命中任何词（仅保留接口）
            replacement: 替换字符（默认 ``"***"``）
            case_sensitive: 是否大小写敏感（默认 False，更宽容）
            drop_on_match: 命中时是否整条丢弃（默认 False 仅替换）
            enabled: 总开关（默认 True；False 时 filter() 直接返回原文 + dropped=False）
        """
        self.enabled = enabled
        self.words = list(words) if words else []
        self.replacement = replacement
        self.case_sensitive = case_sensitive
        self.drop_on_match = drop_on_match

    def filter(self, text: str) -> Tuple[str, bool]:
        """过滤敏感词。

        Args:
            text: 待过滤的原始文本

        Returns:
            (cleaned_text, dropped) 元组：
            - ``dropped`` = True 表示命中了敏感词（drop_on_match=True 时 caller 应丢弃整条）。
            - ``cleaned_text`` = 替换后的文本（drop_on_match=False 时使用）。
        """
        if not self.enabled or not self.words or not text:
            return text, False

        # 大小写策略：构造搜索用的统一小写副本，命中后用原始大小写替换
        words_search = self.words if self.case_sensitive else [w.lower() for w in self.words]

        cleaned = text
        lowered = text if self.case_sensitive else text.lower()
        dropped = False

        for original_word, search_word in zip(self.words, words_search, strict=False):
            if not search_word:
                continue
            # 简易包含匹配（不区分词边界）
            if search_word in lowered:
                dropped = True
                # 替换（不区分大小写策略下用 case-insensitive replace）
                if self.case_sensitive:
                    cleaned = cleaned.replace(original_word, self.replacement)
                else:
                    # 用正则实现大小写不敏感的全局替换
                    pattern = re.compile(re.escape(original_word), re.IGNORECASE)
                    cleaned = pattern.sub(self.replacement, cleaned)
                    lowered = cleaned.lower()

        return cleaned, dropped


def apply_word_filter(text: str, word_filter: Optional[WordFilter]) -> Optional[str]:
    """对一句台词做敏感词净化，返回净化后台词；``drop_on_match=True`` 且命中时返回 None。

    ``word_filter`` 为 None 时不净化、原样放行。
    """
    if word_filter is None:
        return text
    cleaned, dropped = word_filter.filter(text)
    if dropped and word_filter.drop_on_match:
        return None
    return cleaned


# ============================================================================
# 三检整体取用（出口守卫）
# ============================================================================


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """一句台词过出口守卫的结果。

    ``text`` 为放行的台词（可能经长度截短与敏感词替换）；未过守卫时为 None。
    ``reason`` 说明裁决去向：``passed`` / ``word_filter``（敏感词整条丢弃）/
    ``duplicate``（与最近发言大面积重合）。``ratio`` 仅 duplicate 时有意义，
    供打回话术与观测。
    """

    text: Optional[str]
    reason: str
    ratio: float = 0.0


def guard(
    text: str,
    recent_speeches: Sequence[str],
    *,
    speech_max_chars: int,
    word_filter: Optional[WordFilter],
    gate: SpeechGate,
) -> GuardVerdict:
    """一句台词的完整出口裁决：长度预算 → 敏感词 → 重合度，任一不过整句不放行。

    长度预算 ``speech_max_chars`` 为 0 时不截短；``word_filter`` 为 None 时
    不净化；重合度判定委托注入的 :class:`SpeechGate`（窗口与阈值同源配置）。
    """
    if speech_max_chars > 0:
        text = fit_to_budget(text, speech_max_chars)
    filtered = apply_word_filter(text, word_filter)
    if filtered is None:
        return GuardVerdict(text=None, reason="word_filter")
    text = filtered
    verdict = gate.verdict(text, recent_speeches)
    if not verdict.passed:
        return GuardVerdict(text=None, reason="duplicate", ratio=verdict.ratio)
    return GuardVerdict(text=text, reason="passed")
