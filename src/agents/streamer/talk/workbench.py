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

__all__ = [
    "MAX_ROUNDS_PER_BEAT",
    "MAX_WORKBENCH_CHARS",
    "TANGENT_MAX_FOLLOWUP_ROUNDS",
    "TANGENT_TTL_MS",
    "BeatStatus",
    "TalkWorkbench",
]

# 同一个话头最多连讲几轮就该收束：再多就成了车轱辘话
MAX_ROUNDS_PER_BEAT = 3
# 参考段渲染上限（字）：工作台只是给她看个进度，不能挤占弹幕与情境
MAX_WORKBENCH_CHARS = 300
# 边玩边说档观众引出话头的余温存活上限（毫秒）：超时没轮到空闲主动窗就自然翻篇，不在几分钟后翻旧话
TANGENT_TTL_MS = 90_000
# 边玩边说档无观众继续追问时最多顺延几个主动轮（首答 1 轮 + 余温 1 轮 = 共 2 拍即自动收束回手头游戏）
TANGENT_MAX_FOLLOWUP_ROUNDS = 1
# 被打断尾句的保留长度：只留大意，够她想起来说到哪了
_TAIL_MAX_CHARS = 60
# 观众临时话头短名与摘要的保留上限：给渲染引导语与被打断尾句留足预算，避免截断关键说明
_TANGENT_TAG_MAX_CHARS = 16
_TANGENT_SUMMARY_MAX_CHARS = 90
# 提问箱/问答类提纲要点特征词：这类要点本身就是靠抽话题卡展开，卡片聊完时同步推进该要点
_CARD_BEAT_HINTS = ("提问", "问题", "棉花糖", "话题", "问答")

BeatStatus = Literal["pending", "talking", "done"]


@dataclass
class _CardTopic:
    """当前在聊的话题卡（选卡开话头后，直到聊满轮数或换卡/换环节）。"""

    card_id: int
    title: str
    rounds: int = 0


@dataclass
class _ViewerTangent:
    """边玩边说档由观众弹幕或付费留言引出的临时话头余温（纯内存态，换环节、急事或超时即清）。"""

    topic_tag: str
    summary: str
    started_at_ms: int
    followup_rounds: int = 0


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
        self._viewer_tangent: Optional[_ViewerTangent] = None

    # ------------------------------------------------------------------
    # 环节同步
    # ------------------------------------------------------------------

    def sync_segment(self, segment_id: Optional[str], beats: List[str]) -> None:
        """每个决策回合开头同步当前环节：换了环节（或提纲被导演改了）就重置进度。

        同一环节、同一份提纲重复调用不动进度，所以可以放心每回合都调。
        """
        clean = [b for b in beats if b.strip()]
        if segment_id == self._segment_id and clean == self._beats:
            return
        self._segment_id = segment_id
        self._beats = clean
        self._status = ["pending"] * len(clean)
        self._rounds = [0] * len(clean)
        # 换环节后上一个环节的话题卡、尾句与观众临时话头余温都不再相关
        self._card = None
        self._interrupted_tail = ""
        self.clear_viewer_tangent()

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
                index = self._current_index()
                if index is not None and any(hint in self._beats[index] for hint in _CARD_BEAT_HINTS):
                    # 提问箱/话题卡专属环节的要点本来就是抽卡回答：卡片收尾时同步勾掉这一条提纲要点
                    self._status[index] = "done"
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
    # 边玩边说档：观众引出话头的 1 拍短余温
    # ------------------------------------------------------------------

    def start_viewer_tangent(self, topic_tag: str, summary: str, *, at_ms: int) -> None:
        """边玩边说档回应观众弹幕或付费留言开启、延续话头时记录余温；观众继续追问时刷新时刻并重置计数。"""
        clean_tag = " ".join((topic_tag or "").split())[:_TANGENT_TAG_MAX_CHARS]
        clean_summary = " ".join((summary or "").split())[:_TANGENT_SUMMARY_MAX_CHARS]
        if not clean_tag and not clean_summary:
            return
        self._viewer_tangent = _ViewerTangent(
            topic_tag=clean_tag,
            summary=clean_summary,
            started_at_ms=int(at_ms),
            followup_rounds=0,
        )

    def clear_viewer_tangent(self) -> None:
        """清空观众临时话头余温（环节切换、游戏突发急事或主播显式声明话头收尾时调用）。"""
        self._viewer_tangent = None

    def consume_viewer_tangent_round(self) -> None:
        """边玩边说档无观众弹幕的主动轮结束后推进计数：无追问时最多顺延 1 个主动轮即自动清空收束回游戏。"""
        if self._viewer_tangent is None:
            return
        self._viewer_tangent.followup_rounds += 1
        if self._viewer_tangent.followup_rounds >= TANGENT_MAX_FOLLOWUP_ROUNDS:
            self._viewer_tangent = None

    def has_active_tangent(self, *, now_ms: int) -> bool:
        """判定当前是否存在未过期且未用尽顺延轮数的观众话头余温；已过期或已用尽时顺便清空。"""
        tangent = self._viewer_tangent
        if tangent is None:
            return False
        elapsed_ms = int(now_ms) - tangent.started_at_ms
        if elapsed_ms >= TANGENT_TTL_MS or tangent.followup_rounds >= TANGENT_MAX_FOLLOWUP_ROUNDS:
            self._viewer_tangent = None
            return False
        return True

    def render_play_window(self, *, now_ms: int, has_viewer_batch: bool, urgent_game: bool = False) -> str:
        """渲染边玩边说档无弹幕窗口的【刚聊开的话头】余温段与被打断尾句；游戏急事到达时立即清空余温。"""
        if urgent_game:
            # 游戏出现待定夺或突发遭遇急事时立即掐断闲聊余温，先处理眼下游戏处境
            self.clear_viewer_tangent()
            return ""
        if has_viewer_batch:
            return ""
        lines: List[str] = []
        if self.has_active_tangent(now_ms=now_ms) and self._viewer_tangent is not None:
            tangent = self._viewer_tangent
            if tangent.topic_tag and tangent.summary:
                topic_head = f"「{tangent.topic_tag}」——{tangent.summary}"
            elif tangent.topic_tag:
                topic_head = f"「{tangent.topic_tag}」"
            else:
                topic_head = tangent.summary
            lines.append(f"刚才由观众弹幕聊起：{topic_head}")
            lines.append(
                "眼下游戏无急事，本轮可顺着多聊一两句本场未说过的延伸看法或设定喜好，聊完这一轮自然收束回手头游戏；"
                "设定档案与记忆未记载的亲身经历不编；若无延伸内容可直接看手头游戏或沉默。"
            )
        if self._interrupted_tail:
            lines.append(f"上一轮被打断没说完：{self._interrupted_tail}")
        if not lines:
            return ""
        text = "【刚聊开的话头】\n" + "\n".join(lines)
        if len(text) > MAX_WORKBENCH_CHARS:
            text = text[: MAX_WORKBENCH_CHARS - 1] + "…"
        return text

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
