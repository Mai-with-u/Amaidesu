"""发言重合度比对 —— 台词级重复检测的清洗与包含率计算（纯函数，零分词依赖）。

## 定位
reply 工具在生成台词**之后**、返回工具结果之前，用本模块把成品台词与
最近几条已说出口的发言做字符 bigram 包含率比对：大面积重合说明本条
没有观众没听过的内容，作为工具错误打回（ReAct 工具回执的天然机制）。
生成前的话题摘要预检查已退役——摘要自带背景词、换皮即可绕过；检查
对象换成成品台词后，背景词稀释在结构上消失。

## 比对口径（零依赖：不做分词，只做字符级 n-gram 集合运算）
- **n = 2（字符 bigram）**：bigram 对语序变化有一定容忍，换皮复述
  （同义改写、加语气词）在 bigram 层仍大面积重合。
- **数值归一**：阿拉伯数字（含全宽/小数）与中文数字串在比对前统一替换
  为占位符——数值变化不构成新内容，数字换写法不得让换皮复述漏检。
- **引注剥除（比对前清洗）**：比对语料先剥含出处标记或 ASCII 标识的
  括号引注段、再剔 ASCII 字母词元；只用于闸的比对，不改台词原文。
  在数值归一之前调用——占位符"N"本身是 ASCII 字母，顺序颠倒会被误删。
- **重叠率 = |candidate ∩ known| / |candidate|（包含率）**：分母用新台词
  自身的 bigram 数——衡量的"新台词里有多少成分为旧话"，不受已知语料
  体量膨胀影响。
- **判重阈值 0.5**：场次 31 的 31 条真实发言回放（窗口取最近 10 条）中，
  全量分布 p90 为 0.41、上限 0.42，唯一已知复读对落在 0.53——0.5 恰好
  只拦复读不误伤正常发言。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

__all__ = [
    "NGRAM_SIZE",
    "SPEECH_DUPLICATE_RATIO",
    "SPEECH_GATE_WINDOW",
    "SpeechGate",
    "SpeechGateVerdict",
    "char_bigrams",
    "speech_overlap_ratio",
    "strip_citations",
]


#: 字符 n-gram 的 n（bigram，理由见模块 docstring）
NGRAM_SIZE: int = 2

#: 判重阈值：包含率达到即视为重复发言（场次 31 真实发言回放定标，依据见模块 docstring）
SPEECH_DUPLICATE_RATIO: float = 0.5

#: 参与比对的最近发言条数默认值（可在主播配置里覆盖；回放口径：窗口 10 时
#: 正常发言与复读分层最清晰，窗口 5 漏检、窗口 20 误伤面扩大）
SPEECH_GATE_WINDOW: int = 10

#: 数值归一模式：阿拉伯数字串（含全宽与小数）或中文数字串，统一替换为占位符
_NUMBER_PATTERN = re.compile(r"[0-9０-９]+(?:[.．][0-9０-９]+)*|[零一二两三四五六七八九十百千万亿]+")

#: 数值归一后的占位符
_NUMBER_PLACEHOLDER = "N"

#: 括号引注段：中英文括号包住的一段（不含嵌套）
_CITATION_PAREN_PATTERN = re.compile(r"（[^（）]*）|\([^()]*\)")

#: 引注段判定标记：括号内含这些字样或含 ASCII 字母数字（内部标识/坐标）即视为出处引注
_CITATION_MARKERS = ("见", "记录", "确认", "回顾", "参考", "来自", "出处")
_CITATION_ASCII = re.compile(r"[A-Za-z0-9_]")

#: 残余的 ASCII 字母词元（reflex/craft 等内部标识）：数字不在其列，
#: 留给数值归一统一压成占位符
_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z_]+")


def strip_citations(text: str) -> str:
    """剥除文本里的出处引注，返回只含自然语言的比对语料。

    依次去掉：含引注标记或 ASCII 字母数字的括号段（如"（见 reflex_finished）"
    "（-86,66,50）"），以及残余的 ASCII 字母词元。只用于闸的比对语料，
    不改原文；在数值归一之前调用——占位符"N"本身是 ASCII 字母，
    顺序颠倒会被误删。
    """
    cleaned = text or ""
    for paren in _CITATION_PAREN_PATTERN.findall(cleaned):
        inner = paren[1:-1]
        if any(marker in inner for marker in _CITATION_MARKERS) or _CITATION_ASCII.search(inner):
            cleaned = cleaned.replace(paren, " ")
    return _ASCII_TOKEN_PATTERN.sub(" ", cleaned)


def _normalize_numbers(text: str) -> str:
    """把文本中的数值串替换为统一占位符（数值变化不构成新内容）。"""
    return _NUMBER_PATTERN.sub(_NUMBER_PLACEHOLDER, text or "")


def _gate_bigrams(text: str) -> set[str]:
    """闸的比对语料 bigram：引注剥除 → 数值归一 → 字符 bigram（两侧对称的唯一入口）。"""
    return char_bigrams(_normalize_numbers(strip_citations(text)))


def char_bigrams(text: str) -> set[str]:
    """文本的字符 bigram 集合（去空白；长度不足 n 时返回空集——无可比对成分）。"""
    chars = [c for c in (text or "") if not c.isspace()]
    return {"".join(chars[i : i + NGRAM_SIZE]) for i in range(len(chars) - NGRAM_SIZE + 1)}


def speech_overlap_ratio(speech: str, recent_speeches: Sequence[str]) -> float:
    """成品台词对最近发言语料的 bigram 包含率（0.0~1.0）。

    台词或语料任一侧没有可比对成分（过短/全空白）时返回 0.0——
    无从比对即放行，交由提示词纪律约束。
    """
    candidate = _gate_bigrams(speech)
    if not candidate:
        return 0.0
    known: set[str] = set()
    for text in recent_speeches:
        known |= _gate_bigrams(text)
    if not known:
        return 0.0
    return len(candidate & known) / len(candidate)


@dataclass(frozen=True, slots=True)
class SpeechGateVerdict:
    """一句台词的重合度闸判定结果（ratio 附带供打回话术与观测）。"""

    passed: bool
    ratio: float


class SpeechGate:
    """台词重合度闸：成品台词对最近已说发言的 bigram 包含率检查。

    判定口径见模块头部；窗口与阈值由构造注入（配置同源，Agent 构造
    一份给 reply 工具与嘴共享）。语料传旧→新的完整列表，窗口截取在
    闸内完成；调用方各自处置打回——reply 工具打回并引导重试，嘴直接
    丢弃。
    """

    def __init__(self, *, window: int = SPEECH_GATE_WINDOW, ratio: float = SPEECH_DUPLICATE_RATIO) -> None:
        self._window = max(0, int(window))
        self._ratio = float(ratio)

    def verdict(self, speech: str, recent_speeches: Sequence[str]) -> SpeechGateVerdict:
        """判定一句台词是否与最近发言大面积重复（窗口 0 = 不检查恒放行）。"""
        corpus = list(recent_speeches)[-self._window :] if self._window > 0 else []
        ratio = speech_overlap_ratio(speech, corpus)
        return SpeechGateVerdict(passed=ratio < self._ratio, ratio=ratio)
