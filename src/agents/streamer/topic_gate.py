"""话题重复闸 —— reply 出题前的框架级重复检测（纯函数，零分词依赖）。

## 定位
Planner 在执行 ``streamer_reply`` 调用前，用本模块对新 ``topic_summary``
做字符 n-gram 重叠检测：与最近几轮已聊话题及说出口的台词大面积重合、
且本窗又没有新词背书时，把 reply 当工具错误打回（ReAct 工具回执的
天然机制），引导 planner 换新角度或选择本轮沉默。

## 检测口径（零依赖：不做分词，只做字符级 n-gram 集合运算）
- **n = 2（字符 bigram）**：话题摘要通常只有 8~25 个字，trigram 在这个
  长度上过于稀疏会漏检；bigram 对语序变化有一定容忍，换皮复述（同义
  改写、加语气词）在 bigram 层仍大面积重合。
- **数值归一**：阿拉伯数字（含全宽/小数）与中文数字串在比对前统一替换
  为占位符——数值变化不构成新角度（血量从五点掉到 2.8 与"掉血"是同一
  件事），数字换写法不得让换皮复述漏检。
- **重叠率 = |candidate ∩ known| / |candidate|（包含率）**：分母用新话题
  自身的 bigram 数——衡量的"新话题里有多少成分为旧话"，不受已知语料
  体量膨胀影响。
- **判重阈值 0.5**：新话题一半以上的二字组合都出现在最近发言里，对
  10 字话题即 5 个重合 bigram，足以滤掉换皮复述；低于此值视为已有
  实质新角度，放行。
- **新实体豁免**：candidate 的 bigram 若出现在本窗新语料（弹幕批/游戏
  聊天/游戏叙事）但不在最近发言语料里，说明话题带进了聊天里没聊过的
  新词（新物品/新生物/新事件/新观众名）——即便与旧话题字面重合也放行。
  旧叙事条目混进语料无害：真被说过的词已在最近发言语料里，不会被判成
  "新"。数值归一的占位符 bigram（如"N只""N点"）不是内容词，不参与
  新实体豁免——游戏叙事里的新数字不得充当新角度证据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = ["NGRAM_SIZE", "DUPLICATE_RATIO", "RECENT_LIMIT", "GateVerdict", "char_bigrams", "check_topic"]


#: 字符 n-gram 的 n（bigram，理由见模块 docstring）
NGRAM_SIZE: int = 2

#: 判重阈值：包含率达到即视为重复话题
DUPLICATE_RATIO: float = 0.5

#: 参与比对的最近发言条数（复盘口径：43 条发言里 85% 集中在 10-30 秒间隔，
#: 5 条已覆盖最近几分钟的连续输出窗口）
RECENT_LIMIT: int = 5

#: 数值归一模式：阿拉伯数字串（含全宽与小数）或中文数字串，统一替换为占位符
_NUMBER_PATTERN = re.compile(r"[0-9０-９]+(?:[.．][0-9０-９]+)*|[零一二两三四五六七八九十百千万亿]+")

#: 数值归一后的占位符（含它的 bigram 不参与新实体豁免）
_NUMBER_PLACEHOLDER = "N"


def _normalize_numbers(text: str) -> str:
    """把文本中的数值串替换为统一占位符（数值变化不构成新角度）。"""
    return _NUMBER_PATTERN.sub(_NUMBER_PLACEHOLDER, text or "")


@dataclass(frozen=True)
class GateVerdict:
    """一次重复检测的裁决：是否放行、是否判重、包含率与给 planner 的回执话术。"""

    allowed: bool
    duplicate: bool
    overlap_ratio: float
    reason: str


def char_bigrams(text: str) -> set[str]:
    """文本的字符 bigram 集合（去空白；长度不足 n 时返回空集——无可比对成分）。"""
    chars = [c for c in (text or "") if not c.isspace()]
    return {"".join(chars[i : i + NGRAM_SIZE]) for i in range(len(chars) - NGRAM_SIZE + 1)}


def check_topic(
    topic_summary: str,
    *,
    recent_topics: Sequence[str],
    recent_speeches: Sequence[str],
    fresh_texts: Iterable[str] = (),
) -> GateVerdict:
    """判定新话题是否与最近发言重复。

    Args:
        topic_summary: reply 调用提交的新话题摘要。
        recent_topics: 最近几轮已聊话题（旧→新，调用方截取）。
        recent_speeches: 与上述各轮对应的说出口的台词。
        fresh_texts: 本窗新语料（弹幕/游戏聊天/游戏叙事），新实体豁免词表来源。

    Returns:
        GateVerdict：空白短话题、包含率未达阈值、或命中新实体豁免时放行。
    """
    candidate = char_bigrams(_normalize_numbers(topic_summary))
    if not candidate:
        # 过短（单字）没有可比对成分，交由提示词纪律约束而非框架拦下
        return GateVerdict(allowed=True, duplicate=False, overlap_ratio=0.0, reason="")

    known: set[str] = set()
    for text in [*recent_topics[-RECENT_LIMIT:], *recent_speeches[-RECENT_LIMIT:]]:
        known |= char_bigrams(_normalize_numbers(text))
    if not known:
        return GateVerdict(allowed=True, duplicate=False, overlap_ratio=0.0, reason="")

    hit = candidate & known
    ratio = len(hit) / len(candidate)
    if ratio < DUPLICATE_RATIO:
        return GateVerdict(allowed=True, duplicate=False, overlap_ratio=ratio, reason="")

    fresh: set[str] = set()
    for text in fresh_texts:
        fresh |= char_bigrams(_normalize_numbers(text))
    # 新词 = 本窗语料里有、最近发言里没有的成分；本窗语料与最近发言的重合部分
    # （如旧叙事条目被讲过）不构成新角度证据；数值占位符 bigram 不是内容词，
    # 游戏叙事里的新数字不得充当新角度证据
    new_words = {bigram for bigram in ((candidate & fresh) - known) if _NUMBER_PLACEHOLDER not in bigram}
    if new_words:
        sample = "、".join(sorted(new_words)[:5])
        return GateVerdict(
            allowed=True,
            duplicate=True,
            overlap_ratio=ratio,
            reason=f"与最近发言字面重合但带本窗新词（{sample}），视为新角度放行",
        )

    return GateVerdict(
        allowed=False,
        duplicate=True,
        overlap_ratio=ratio,
        reason=(
            f"话题与最近发言重复（重合率 {ratio:.0%}）：请换实质性新角度"
            "（新事实/新进展/新互动对象）重新出题，或选择本轮沉默（不再调用任何工具）"
        ),
    )


def rejection_reason(is_retry: bool) -> str:
    """打回回执的引导话术：首次打回给改题机会，再次拒绝则引导本轮沉默。"""
    base = "话题与最近发言重复：请换新角度或选择本轮沉默"
    if is_retry:
        return base + "（已再次重复，本轮请保持沉默：不再调用任何工具，直接结束）"
    return base
