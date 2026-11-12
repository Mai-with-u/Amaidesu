"""话头工作台：跟踪当前环节提纲要点聊到哪了、当前话头聊了几轮、被打断没说完的尾句。

主播在杂谈环节按流程单的提纲要点（beats）一个点一个点往下聊；每个点通常要经过
"起个头 → 展开细节 → 吐槽或抛梗"两三轮才算聊透。工作台是主播 Agent 的内部件
（代码直连，不进工具表），由决策执行器在她每说完一轮话后喂进结果：

- 有提纲的环节：要点状态 ``未聊`` → ``正在聊（第 k 轮）`` → ``已聊完``；模型在
  ``streamer_reply`` 里声明 ``beat_done`` 或同一点连讲满上限轮数时推进到下一个未聊要点。
- 选用话题卡开话头时：话题卡成为当前在聊话头，同样按轮数收束，不改提纲进度。
- 高优弹幕插队把她没说完的后半段丢掉时，记下尾句大意，下一轮由她决定要不要顺嘴补完。

渲染出的【当前话头与本节提纲】段进决策参考段，限 300 字；同时给流程单状态机提供
"剩余未聊要点"查询，防早退护栏据此拦住提纲没聊完就切环节的 next。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

__all__ = ["MAX_ROUNDS_PER_BEAT", "MAX_WORKBENCH_CHARS", "BeatStatus", "TalkWorkbench"]

# 同一个话头最多连讲几轮就该收束：再多就成了车轱辘话
MAX_ROUNDS_PER_BEAT = 3
# 参考段渲染上限（字）：工作台只是给她看个进度，不能挤占弹幕与情境
MAX_WORKBENCH_CHARS = 300
# 被打断尾句的保留长度：只留大意，够她想起来说到哪了
_TAIL_MAX_CHARS = 60

BeatStatus = Literal["pending", "talking", "done"]


@dataclass
class _CardTopic:
    """当前在聊的话题卡（选卡开话头后，直到聊满轮数或换卡/换环节）。"""

    card_id: int
    title: str
    rounds: int = 0


class TalkWorkbench:
    """单场内存态的话头进度表；非线程安全，仅在主播 Agent 的事件循环内使用。"""

    def __init__(self, *, max_rounds_per_beat: int = MAX_ROUNDS_PER_BEAT) -> None:
        self._max_rounds = max(1, int(max_rounds_per_beat))
        self._segment_id: Optional[str] = None
        self._beats: List[str] = []
        self._status: List[BeatStatus] = []
        self._rounds: List[int] = []
        self._card: Optional[_CardTopic] = None
        self._interrupted_tail: str = ""

    # ------------------------------------------------------------------
    # 环节同步
    # ------------------------------------------------------------------

    def sync_segment(self, segment_id: Optional[str], beats: List[str]) -> None:
        """每个决策窗开头同步当前环节：换了环节（或提纲被导演改了）就重置进度。

        同一环节、同一份提纲重复调用不动进度，所以可以放心每窗都调。
        """
        clean = [b for b in beats if b.strip()]
        if segment_id == self._segment_id and clean == self._beats:
            return
        self._segment_id = segment_id
        self._beats = clean
        self._status = ["pending"] * len(clean)
        self._rounds = [0] * len(clean)
        # 换环节后上一个环节的话题卡与尾句都不再相关
        self._card = None
        self._interrupted_tail = ""

    def remaining_beats(self, segment_id: str, beats: List[str]) -> List[str]:
        """流程单防早退护栏的查询回调：返回该环节还没聊完的要点（含正在聊的那一点）。

        工作台还没同步到这个环节时按"全都没聊"回答——宁可多拦一次，也不放她早退。
        """
        if segment_id != self._segment_id or [b for b in beats if b.strip()] != self._beats:
            return [b for b in beats if b.strip()]
        return [beat for beat, status in zip(self._beats, self._status, strict=True) if status != "done"]

    # ------------------------------------------------------------------
    # 每轮说完后的推进
    # ------------------------------------------------------------------

    def start_card(self, card_id: int, title: str) -> None:
        """她选用一张话题卡开话头：这张卡成为当前在聊话头（替换上一张卡）。"""
        self._card = _CardTopic(card_id=int(card_id), title=title.strip())

    def record_round(self, *, beat_done: bool = False) -> None:
        """她在杂谈档说完一轮话后调用：给当前话头记一轮，必要时收束并推进。

        话题卡在聊时轮数记在卡上（卡聊满或她声明收束就放下这张卡，提纲进度不动）；
        否则记在当前提纲要点上——声明 ``beat_done`` 或连讲满上限轮数即标已聊完，
        下一轮自动落到下一个未聊要点。
        """
        if self._card is not None:
            self._card.rounds += 1
            if beat_done or self._card.rounds >= self._max_rounds:
                self._card = None
            return
        index = self._current_index()
        if index is None:
            return
        self._status[index] = "talking"
        self._rounds[index] += 1
        if beat_done or self._rounds[index] >= self._max_rounds:
            self._status[index] = "done"

    def note_interrupted_tail(self, text: str) -> None:
        """高优插队丢掉她没说完的后半段时调用：记下尾句大意，供下一轮决定要不要补完。"""
        tail = " ".join(text.split())
        if tail:
            self._interrupted_tail = tail[:_TAIL_MAX_CHARS]

    def consume_interrupted_tail(self) -> str:
        """取走被打断的尾句（取一次就清空：只提醒她一次，不让旧话一直挂着）。"""
        tail, self._interrupted_tail = self._interrupted_tail, ""
        return tail

    # ------------------------------------------------------------------
    # 查询与渲染
    # ------------------------------------------------------------------

    def has_open_beat(self) -> bool:
        """当前环节是否还有没聊完的提纲要点（话题匣据此判断要不要预摆话题卡）。"""
        return any(status != "done" for status in self._status)

    def has_active_card(self) -> bool:
        """当前是否正在聊一张话题卡。"""
        return self._card is not None

    def beat_statuses(self) -> List[BeatStatus]:
        """各提纲要点的状态副本（按提纲顺序）。"""
        return list(self._status)

    def render(self) -> str:
        """渲染【当前话头与本节提纲】段；没有提纲、没有在聊的卡、也没有尾句时返回空串。"""
        lines: List[str] = []
        if self._card is not None:
            lines.append(f"正在聊话题卡#{self._card.card_id}「{self._card.title}」（已讲 {self._card.rounds} 轮）")
        index = self._current_index()
        if index is not None and self._card is None:
            rounds = self._rounds[index]
            progress = f"已讲 {rounds} 轮" if rounds else "还没开口"
            lines.append(f"当前要点：{self._beats[index]}（{progress}，最多 {self._max_rounds} 轮）")
        if self._beats:
            done = [b for b, s in zip(self._beats, self._status, strict=True) if s == "done"]
            rest = [
                b for i, (b, s) in enumerate(zip(self._beats, self._status, strict=True)) if s != "done" and i != index
            ]
            if done:
                lines.append("已聊完：" + "；".join(done))
            if rest:
                lines.append("后面还剩：" + "；".join(rest))
        if self._interrupted_tail:
            lines.append(f"上一轮被打断没说完：{self._interrupted_tail}")
        if not lines:
            return ""
        text = "【当前话头与本节提纲】\n" + "\n".join(lines)
        if len(text) > MAX_WORKBENCH_CHARS:
            text = text[: MAX_WORKBENCH_CHARS - 1] + "…"
        return text

    def _current_index(self) -> Optional[int]:
        """当前在聊（或下一个要聊）的要点下标：先找正在聊的，没有就找第一个未聊的。"""
        for i, status in enumerate(self._status):
            if status == "talking":
                return i
        for i, status in enumerate(self._status):
            if status == "pending":
                return i
        return None
