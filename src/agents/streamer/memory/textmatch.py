"""记忆检索与被动想起用的文本处理：归一化、两字切分、触发词匹配（子串加拼音层）。

全是本地纯函数，不调模型、不读库，同一段文本永远得到同一个结果。

- **归一化**（:func:`normalize`）：全角转半角、转小写、去掉 B 站表情弹幕的文字形态（如 ``[doge]``），
  再去掉空格、标点和 emoji。弹幕里"倒 立 洗 头[doge]"和触发词"倒立洗头"归一化后才能直接比。
- **两字切分**（:func:`bigram_tokens`）：全文索引用。SQLite 自带的 trigram 对两个字的中文词完全
  查不到，所以照开源项目 Hermes 的做法自己切：先按字母、数字的连续段切开，段里的汉字连续段
  切成相互重叠的两个字（"倒立洗头" → 倒立、立洗、洗头），只有一个字的汉字段保留这一个字，
  非汉字段原样保留；切好的词用空格连起来写进全文表，由 FTS5 自带的 unicode61 分词器建索引。
- **单个汉字的查询词**（:func:`needs_like_fallback`）：查询"炸TNT"或只查一个"猫"时，"炸""猫"是
  单独一个字的汉字段，而原文"继续炸tnt""黑猫"切出来是"继续""续炸""tnt""黑猫"，索引里没有
  单独的"炸""猫"，按词查会漏；调用方遇到这种查询要退回 LIKE 子串扫描。
- **触发词匹配**（:class:`KeywordMatcher`）：被动想起的规则一路。归一化后做子串匹配；三个字以上、
  全是汉字的触发词另加拼音层——同音（"到立洗头"算提到"倒立洗头"）和拼音首字母（弹幕里单独
  打出的"dlxt"）。两个字的触发词只做精确子串，不做同音：离线测下来误触发几乎全来自两字词的
  同音（"倒立"撞上"道理"，"联机"撞上"连击"）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Generic, Hashable, List, Literal, Mapping, Optional, Sequence, Tuple, TypeVar

from pypinyin import Style, lazy_pinyin

# 汉字（含扩展区与兼容区）、日文假名、韩文音节：这些文字连写不带空格，按两字切分
_CJK_CHARS = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\U00020000-\U0003134f"
_CJK_RUN = re.compile(f"[{_CJK_CHARS}]+")
_ALL_CJK = re.compile(f"[{_CJK_CHARS}]+$")
# B 站表情弹幕的文字形态：方括号里一小段字，如 [doge]、[笑哭]、[热词系列_知识增加]
_EMOTE = re.compile(r"\[[^\[\]\s]{1,16}\]")
# 弹幕里单独打出的一串字母（拼音首字母要和它整串相等，不算子串）
_LETTER_RUN = re.compile(r"[a-z]+")

# 拼音层只给三个字以上、全是汉字的触发词
PINYIN_MIN_CHARS = 3
# 一个字的触发词不收：单字子串匹配几乎每条弹幕都会撞上
KEYWORD_MIN_CHARS = 2

MatchHow = Literal["exact", "homophone", "initials"]
K = TypeVar("K", bound=Hashable)


def _fold(text: str) -> str:
    """全角转半角、转小写、去掉表情弹幕的文字形态；空格标点还在，供切分时当分隔。"""
    return _EMOTE.sub(" ", unicodedata.normalize("NFKC", text or "").lower())


def normalize(text: str) -> str:
    """匹配前统一文本：全角转半角、转小写、去表情文字，再去掉空格、标点和 emoji（只留字母、数字、文字）。"""
    return "".join(ch for ch in _fold(text) if ch.isalnum())


def _alnum_runs(text: str) -> List[str]:
    """按 unicode61 的口径切出字母、数字的连续段（空格、标点、emoji 都是分隔）。"""
    runs: List[str] = []
    current: List[str] = []
    for ch in _fold(text):
        if ch.isalnum():
            current.append(ch)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


def _split_run(run: str) -> List[Tuple[str, bool]]:
    """把一段字母数字连续段拆成汉字段与非汉字段，按原顺序；第二项表示是不是汉字段。"""
    parts: List[Tuple[str, bool]] = []
    cursor = 0
    for match in _CJK_RUN.finditer(run):
        if match.start() > cursor:
            parts.append((run[cursor : match.start()], False))
        parts.append((match.group(), True))
        cursor = match.end()
    if cursor < len(run):
        parts.append((run[cursor:], False))
    return parts


def bigram_tokens(text: str) -> List[str]:
    """两字切分：汉字段切成重叠的两字，单字汉字段保留一字，非汉字段原样；按出现顺序，不去重。"""
    tokens: List[str] = []
    for run in _alnum_runs(text):
        for part, is_cjk in _split_run(run):
            if not is_cjk or len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
    return tokens


def index_text(text: str) -> str:
    """写进全文表 ``tokens`` 列的文本：两字切分后用空格连起来。"""
    return " ".join(bigram_tokens(text))


def fts_or_query(text: str) -> Optional[str]:
    """把一段查询文本变成 FTS5 的"或"查询（每个词加引号，去重保序）；切不出词时返回 None。

    检索按 BM25 排序，共有的词越多、越稀有，分数越高；引号让 FTS5 把每个词当字面量，
    查询里出现 OR、NOT 这类词也不会被当成运算符。
    """
    unique = list(dict.fromkeys(bigram_tokens(text)))
    if not unique:
        return None
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in unique)


def needs_like_fallback(text: str) -> bool:
    """查询里有只一个字的汉字段（"炸TNT"的"炸"、单独查的"猫"）时为 True。

    这个字在原文里几乎总和前后的汉字切进两字词，索引里没有它单独一项，按词查必漏，
    调用方要改用 LIKE 子串扫描兜底。
    """
    return any(is_cjk and len(part) == 1 for run in _alnum_runs(text) for part, is_cjk in _split_run(run))


@dataclass(frozen=True)
class KeywordHit(Generic[K]):
    """一处触发词命中：哪个条目、哪个触发词、怎么命中的（精确子串 / 同音 / 拼音首字母）。"""

    key: K
    keyword: str
    how: MatchHow


@dataclass(frozen=True)
class _Keyword(Generic[K]):
    """预处理好的一个触发词：原文、归一化后的样子，以及拼音层用的音节与首字母（两字词没有）。"""

    key: K
    raw: str
    normalized: str
    syllables: Tuple[str, ...]
    initials: str


class KeywordMatcher(Generic[K]):
    """一组条目的触发词匹配器：构造时预处理触发词，之后每条文本逐条匹配。

    条目键由调用方定（档案行、承诺、老梗各用自己的编号）。同一个条目的同一个触发词在一条
    文本里只报一次，精确子串优先于同音、同音优先于首字母。
    """

    def __init__(self, entries: Mapping[K, Sequence[str]]) -> None:
        self._keywords: List[_Keyword[K]] = []
        seen: set[Tuple[K, str]] = set()
        for key, keywords in entries.items():
            for raw in keywords:
                normalized = normalize(raw)
                # 一个字的触发词撞得太多，不收；同一条目里归一化后相同的只留一个
                if len(normalized) < KEYWORD_MIN_CHARS or (key, normalized) in seen:
                    continue
                seen.add((key, normalized))
                pinyin_layer = len(normalized) >= PINYIN_MIN_CHARS and bool(_ALL_CJK.match(normalized))
                self._keywords.append(
                    _Keyword(
                        key=key,
                        raw=raw,
                        normalized=normalized,
                        syllables=tuple(lazy_pinyin(normalized)) if pinyin_layer else (),
                        initials="".join(lazy_pinyin(normalized, style=Style.FIRST_LETTER)) if pinyin_layer else "",
                    )
                )
        self._any_pinyin = any(keyword.syllables for keyword in self._keywords)

    def match(self, text: str) -> List[KeywordHit[K]]:
        """一条文本里提到了哪些触发词（按构造时的顺序）。"""
        normalized = normalize(text)
        if not normalized:
            return []
        # 文本的拼音只在有拼音层触发词时才算：两字词为主的条目集合省掉这一步
        syllables: Tuple[str, ...] = tuple(lazy_pinyin(normalized)) if self._any_pinyin else ()
        letter_runs = set(_LETTER_RUN.findall(normalized)) if self._any_pinyin else set()
        hits: List[KeywordHit[K]] = []
        for keyword in self._keywords:
            how = self._how(keyword, normalized, syllables, letter_runs)
            if how is not None:
                hits.append(KeywordHit(key=keyword.key, keyword=keyword.raw, how=how))
        return hits

    @staticmethod
    def _how(
        keyword: _Keyword[K], normalized: str, syllables: Tuple[str, ...], letter_runs: set[str]
    ) -> Optional[MatchHow]:
        """先看精确子串；三个字以上的再看同音（音节连续相等）与首字母（单独一串字母整串相等）。"""
        if keyword.normalized in normalized:
            return "exact"
        if not keyword.syllables:
            return None
        if _contains_run(syllables, keyword.syllables):
            return "homophone"
        if keyword.initials in letter_runs:
            return "initials"
        return None


def _contains_run(haystack: Tuple[str, ...], needle: Tuple[str, ...]) -> bool:
    """音节序列里是否连续出现另一串音节。"""
    width = len(needle)
    return any(haystack[index : index + width] == needle for index in range(len(haystack) - width + 1))


__all__ = [
    "KEYWORD_MIN_CHARS",
    "PINYIN_MIN_CHARS",
    "KeywordHit",
    "KeywordMatcher",
    "MatchHow",
    "bigram_tokens",
    "fts_or_query",
    "index_text",
    "needs_like_fallback",
    "normalize",
]
