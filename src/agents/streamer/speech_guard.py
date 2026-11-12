"""台词出口守卫：一句台词能不能出声的裁决规则单点收拢。

两检齐备——长度预算（截到预算内最后一个完整句子）、敏感词净化（替换或
整条丢弃）。词汇定义与判定分支集中在本模块单一实现；
各调用点按自身语义组合取用：

- Replyer：长度预算先走 LLM 重说（表达策略，留在 Replyer 内部），
  截短兜底用 :func:`fit_to_budget`；出口只做敏感词净化。
- Mouth：按"截短 → 净化"顺序全跑，不过关整句丢弃。
- 记忆编号（档#N、约#N）：reply 工具交接前洗交接稿，Replyer 生成后洗台词。

各检既可整体取用（:func:`guard`），也可单独取用（:func:`apply_word_filter`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

__all__ = [
    "GuardVerdict",
    "VERBATIM_MIN_TOKENS",
    "VERBATIM_RATIO",
    "VerbatimRepeat",
    "WordFilter",
    "apply_word_filter",
    "find_verbatim_repeat",
    "fit_to_budget",
    "guard",
    "strip_memory_numbers",
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
# 记忆编号
# ============================================================================

# 档案行与承诺的编号（[档#N]、档#N、约#N）是房管在简报里点名用的记号，观众听不懂。
# 带方括号的连同括号里跟着的注记（如"·上一场"）一起去掉；没括号的只去编号本身
_BRACKETED_MEMORY_NUMBER = re.compile(r"[\[【]\s*[档约]\s*[#＃]\s*\d+[^\]】\n]{0,12}[\]】]")
_BARE_MEMORY_NUMBER = re.compile(r"[档约]\s*[#＃]\s*\d+")
# 去掉编号后剩下的空括号（"（档#3）"→"（）"）
_EMPTY_PARENS = re.compile(r"[（(]\s*[）)]")


def strip_memory_numbers(text: str) -> Tuple[str, int]:
    """去掉文本里的记忆编号，返回（去掉后的文本，去掉了几处）；没有编号时原样返回、计数为 0。

    交给说话侧之前先洗一遍交接稿，说话侧生成台词后再洗一遍：编号念出来是"档井号四十一"，
    观众听不懂，也暴露了后台的记账方式。
    """
    cleaned, bracketed = _BRACKETED_MEMORY_NUMBER.subn("", text)
    cleaned, bare = _BARE_MEMORY_NUMBER.subn("", cleaned)
    count = bracketed + bare
    if not count:
        return text, 0
    cleaned = _EMPTY_PARENS.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, count


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
# 近逐字复读
# ============================================================================

#: 参与比对的最短长度（按"字"算：一个汉字算一个，一串连着的英文数字算一个）。
#: 短句天然会撞（"真是哭笑不得"六个字隔几分钟又说一遍不算复读），够长才谈得上"把那句话又说了一遍"
VERBATIM_MIN_TOKENS: int = 10

#: 判定为近逐字的重合比例下限：新句子的相邻两字组合里，有这么大比例在更早的某一句里原样出现。
#: 四场直播 1067 句台词逐句对本场更早的全部台词回放：唯一的真复读（30 字隔 157 秒重讲）是 1.00，
#: 其余最高 0.67（只是共用了"西边那几棵橡树"这样一个短语），0.7 到 1.0 之间是空的；
#: 取 0.9 时只拦到那一句，误拦为零
VERBATIM_RATIO: float = 0.9

# 一个"字"：一串连着的英文数字整体算一个（玩家名、观众名十几个字母，按字母算会把
# "谢啦 + 名字"这种只是点了同一个人的句子算成九成重合），其余每个文字各算一个；标点与空白不算
_TOKEN = re.compile(r"[a-z0-9]+|[^\W_]")
# 断句：句末标点或换行。逗号不断——"嘴上说不坐船，手倒是很诚实"是一句话
_SENTENCE_BREAK = re.compile(r"[。！？!?…~～；;\n]+")


@dataclass(frozen=True, slots=True)
class VerbatimRepeat:
    """命中的一处近逐字复读：新台词里的哪一句、和更早的哪一条几乎一样、重合比例。"""

    sentence: str
    earlier: str
    ratio: float


def _tokens(text: str) -> List[str]:
    return _TOKEN.findall((text or "").lower())


def _pairs(tokens: Sequence[str]) -> Set[Tuple[str, str]]:
    """相邻两字的组合集合：语序打乱就对不上，只有照原样说才会大面积重合。"""
    return set(zip(tokens, tokens[1:], strict=False))


def _spoken_sentences(text: str) -> List[str]:
    return [part.strip() for part in _SENTENCE_BREAK.split(text or "") if part.strip()]


def find_verbatim_repeat(speech: str, earlier_lines: Sequence[str]) -> Optional[VerbatimRepeat]:
    """这句新台词是不是把更早说过的某句话几乎原样又说了一遍；是就返回重合最高的那一处，否则 None。

    只拦近逐字，不管"换个说法再讲"——那要看内容有没有新东西，归决策环节的增量检验；字面比对
    测不出换说法的复读，中等阈值又会误拦一半正常台词，所以这里阈值取到只剩"照原样重说"。

    比对两层，都是"新的被旧的包含"这个方向（新句里的相邻两字组合有多少在旧句里原样出现）：

    - 整条对整条：新台词整条够长，且几乎整个出现在更早的某一条里；
    - 逐句对逐句：新台词里够长的一句，几乎原样出现在更早某一条的某一句里——她以后连着讲几句
      成一段时，段里夹一句旧话照样拦得到，而整段的重合比例会被别的句子稀释。

    新台词自己的几句之间不互相比：同一段话里的承接句本来就会接着上一句的词往下说。
    ``earlier_lines`` 由调用方给（旧→新），同一次生成拆出来先说出口的几句不要放进来。
    """
    whole = _tokens(speech)
    candidates: List[Tuple[str, Set[Tuple[str, str]], bool]] = []
    sentences = _spoken_sentences(speech)
    if len(sentences) > 1 and len(whole) >= VERBATIM_MIN_TOKENS:
        # 多句成段时先保留整段对整段：两句短话合起来原样复述过一整段时也能认出
        candidates.append((speech.strip(), _pairs(whole), True))
    for sentence in sentences:
        tokens = _tokens(sentence)
        if len(tokens) >= VERBATIM_MIN_TOKENS:
            candidates.append((sentence, _pairs(tokens), False))
    if not candidates:
        return None
    best: Optional[VerbatimRepeat] = None
    for earlier in earlier_lines:
        earlier_whole = _pairs(_tokens(earlier))
        if not earlier_whole:
            continue
        earlier_sentences = [_pairs(_tokens(sentence)) for sentence in _spoken_sentences(earlier)]
        for text, pairs, is_whole in candidates:
            # 整条只和整条比；单句和旧台词的每一句比（旧台词只有一句时就是它整条）
            references = [earlier_whole] if is_whole else earlier_sentences
            ratio = max((len(pairs & reference) / len(pairs) for reference in references if reference), default=0.0)
            # 同样高的取更晚说的那条：打回时给她看的是离现在最近的那次
            if ratio >= VERBATIM_RATIO and (best is None or ratio >= best.ratio):
                best = VerbatimRepeat(sentence=text, earlier=earlier.strip(), ratio=ratio)
    return best


# ============================================================================
# 两检整体取用（出口守卫）
# ============================================================================


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """一句台词过出口守卫的结果。

    ``text`` 为放行的台词（可能经长度截短与敏感词替换）；未过守卫时为 None。
    ``reason`` 说明裁决去向：``passed`` / ``word_filter``（敏感词整条丢弃）。
    """

    text: Optional[str]
    reason: str


def guard(
    text: str,
    *,
    speech_max_chars: int,
    word_filter: Optional[WordFilter],
) -> GuardVerdict:
    """一句台词的完整出口裁决：长度预算 → 敏感词，任一不过整句不放行。

    长度预算 ``speech_max_chars`` 为 0 时不截短；``word_filter`` 为 None 时
    不净化。
    """
    if speech_max_chars > 0:
        text = fit_to_budget(text, speech_max_chars)
    filtered = apply_word_filter(text, word_filter)
    if filtered is None:
        return GuardVerdict(text=None, reason="word_filter")
    return GuardVerdict(text=filtered, reason="passed")
