"""主播时间线：本场直播记录的只追加投影，超预算时按章压成前情提要。

Planner 与 Replyer 每个决策窗都要带上本场对话。对话事实源仍是 ``live_chat``
（加上没落库的调试对话），时间线只是它进 LLM 前的投影，负责两件事：

- **只追加**：记录按主播第一次看到的先后进入时间线，进去后不再挪动、不再改写。
  相邻两个决策窗的历史前缀逐字一致，服务端前缀缓存才能命中。
- **按章压缩**：历史涨过提前整理线时，后台用 summary 档位把最老的一段写成一章
  前情提要；涨到预算时直接换上，决策不用停下来等。章节只追加，两次换章之间前缀
  不变；摘要迟迟出不来、历史涨到预算两倍时，才同步把最老一段换成占位说明兜底。

投影出的条目与 ``live_chat`` 历史行同形（role / content / sender_name /
message_type / message_id / timestamp_ms），canonical 映射原样可用；前情提要是
``message_type = "chapter"`` 的 user 条目。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from src.agents.streamer import canonical
from src.modules.context import BackgroundCheckpoint, ContextBudgetConfig, PrefixGuard, TokenCounter, Watermarks
from src.modules.context import fold_count, resolve_watermarks
from src.modules.logging import get_logger

__all__ = ["CHAPTER_MESSAGE_TYPE", "StreamerTimeline", "TimelineTurn"]

logger = get_logger("StreamerTimeline")

#: 前情提要条目的消息类型（canonical 按原文渲染，不加昵称前缀与消息 ID）
CHAPTER_MESSAGE_TYPE = "chapter"

#: 前情提要的系统提示词模板
_CHAPTER_TEMPLATE = "chapter_system"

# 最近这么多条记录永远保持原文：主播正在接的话不能被压成摘要
_PROTECTED_RECENT = 20

# 历史涨到预算的这么多倍、后台摘要还没出来时，同步用占位说明兜底
_HARD_FOLD_FACTOR = 2.0


@dataclass(frozen=True)
class TimelineTurn:
    """时间线条目（与 live_chat 历史行同形的鸭子类型）。"""

    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""
    timestamp_ms: int = 0


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _turn_key(turn: Any) -> str:
    """条目去重键：有消息 ID 用 ID，否则用角色 + 发生时刻 + 正文（同一行跨窗读出来完全一致）。"""
    message_id = _text(getattr(turn, "message_id", ""))
    if message_id:
        return f"id:{message_id}"
    role = _text(getattr(turn, "role", "")) or "user"
    timestamp = getattr(turn, "timestamp_ms", 0)
    return f"{role}:{timestamp if isinstance(timestamp, int) else 0}:{_text(getattr(turn, 'content', ''))}"


def _clock(timestamp_ms: int) -> str:
    """毫秒时刻 → 本地 HH:MM；没有时刻返回空串。"""
    if timestamp_ms <= 0:
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%H:%M")


class StreamerTimeline:
    """本场直播记录的只追加投影 + 按章压缩（单个 StreamerAgent 持有，非线程安全）。"""

    def __init__(
        self,
        *,
        budget: ContextBudgetConfig,
        llm_service: Any,
        prompt_service: Any,
        profiles: Sequence[str],
        summary_profile: str,
    ) -> None:
        """
        Args:
            budget: 历史预算策略（``[agents.streamer.context]``）。
            llm_service: 查模型窗口与生成前情提要。
            prompt_service: 渲染前情提要的系统提示词。
            profiles: 消费时间线的档位（planner / replyer）；预算按其中最小的窗口换算。
            summary_profile: 生成前情提要的档位。
        """
        self._budget = budget
        self._llm = llm_service
        self._prompt = prompt_service
        self._profiles = tuple(profiles)
        self._summary_profile = summary_profile
        self._counter = TokenCounter()
        self._entries: List[Any] = []
        self._entry_tokens: List[int] = []
        self._keys: set[str] = set()
        self._chapters: List[TimelineTurn] = []
        # 工作层起点：之前的条目已写进前情提要
        self._boundary = 0
        self._pending: BackgroundCheckpoint[str] = BackgroundCheckpoint("主播时间线")
        self._guard = PrefixGuard("主播时间线")
        # 时间线所属的直播场次：读到的场次变了（换场没收到开播事件时）就从空白重新长
        self._scope: Any = None

    async def reset(self) -> None:
        """场次开始或结束：时间线随对话记忆一起清空，在跑的整理作废。"""
        await self._pending.cancel()
        self._entries.clear()
        self._entry_tokens.clear()
        self._keys.clear()
        self._chapters.clear()
        self._boundary = 0
        self._guard.expect_rewrite("新场次")

    async def project(self, turns: Sequence[Any], *, scope: Any = None) -> List[Any]:
        """读入本窗的完整对话，返回进 LLM 的历史：前情提要各章 + 工作层原文。

        新出现的记录按先后追加到尾部；后台已写好的一章在此换上；越过提前整理线时
        发起下一章的后台整理。``scope`` 是对话所属的场次，变了就先清空重来。
        """
        if scope != self._scope:
            await self.reset()
            self._scope = scope
        self._append_unseen(turns)
        marks = self._watermarks()
        self._apply_ready_chapter()
        if self._history_tokens() > marks.budget * _HARD_FOLD_FACTOR:
            await self._hard_fold(marks)
        if self._history_tokens() > marks.prepare and not self._pending.busy:
            self._start_chapter(marks)
        view = [*self._chapters, *self._entries[self._boundary :]]
        self._guard.observe([canonical.turn_to_message(turn) for turn in view])
        return view

    def _append_unseen(self, turns: Sequence[Any]) -> None:
        """只追加没见过的记录；已在时间线里的不挪位置、不改写。"""
        for turn in turns:
            key = _turn_key(turn)
            if key in self._keys:
                continue
            self._keys.add(key)
            self._entries.append(turn)
            self._entry_tokens.append(self._counter.message(canonical.turn_to_message(turn)))

    def _watermarks(self) -> Watermarks:
        """按消费档位里最小的已配置窗口换算预算；都没配置时按保守窗口。"""
        windows: List[int] = []
        getter = getattr(self._llm, "get_profile_context_window", None)
        if callable(getter):
            for profile in self._profiles:
                try:
                    value = getter(profile)
                except Exception as exc:  # noqa: BLE001 - 窗口查不到按未配置处理，决策不中断
                    logger.warning(f"查询 {profile} 上下文窗口失败，按未配置处理：{exc}")
                    continue
                if isinstance(value, int) and value > 0:
                    windows.append(value)
        return resolve_watermarks(self._budget, min(windows) if windows else 0)

    def _chapter_tokens(self) -> int:
        return sum(self._counter.message(canonical.turn_to_message(chapter)) for chapter in self._chapters)

    def _history_tokens(self) -> int:
        return self._chapter_tokens() + sum(self._entry_tokens[self._boundary :])

    def _fold_end(self, marks: Watermarks) -> int:
        """压到回落线需要把工作层压到哪一条为止（不含）；最近的记录始终保留原文。"""
        keep = max(0, marks.target - self._chapter_tokens())
        count = fold_count(
            self._entry_tokens[self._boundary :],
            keep_tokens=keep,
            protect_last=min(_PROTECTED_RECENT, len(self._entries) - self._boundary),
        )
        return self._boundary + count

    def _start_chapter(self, marks: Watermarks) -> None:
        """后台把工作层最老的一段写成下一章；没有可压的段时不发起。"""
        end = self._fold_end(marks)
        if end <= self._boundary:
            return
        segment = list(self._entries[self._boundary : end])
        previous = self._chapters[-1].content if self._chapters else ""
        self._pending.start(self._write_chapter(segment, previous), start=self._boundary, end=end)
        logger.info(f"主播时间线开始后台整理：第 {self._boundary + 1}–{end} 条（共 {len(self._entries)} 条）")

    def _apply_ready_chapter(self) -> None:
        """后台写好的一章换上：工作层起点移到这一章覆盖的末尾。"""
        meta: Dict[str, Any] = self._pending.meta
        text = self._pending.take()
        if text is None or meta.get("start") != self._boundary:
            return
        self._append_chapter(text, self._boundary, int(meta["end"]))

    async def _hard_fold(self, marks: Watermarks) -> None:
        """后台摘要迟迟出不来、历史涨到预算两倍时，同步把最老一段换成占位说明，避免撞窗口。"""
        end = self._fold_end(marks)
        if end <= self._boundary:
            return
        await self._pending.cancel()
        count = end - self._boundary
        note = f"（这一段共 {count} 条记录超出预算、来不及整理，已从上下文移走；观众相关的事可以查记忆）"
        self._append_chapter(note, self._boundary, end)
        logger.warning(f"主播时间线超出预算两倍且摘要未就绪，已用占位说明移走 {count} 条记录")

    def _append_chapter(self, text: str, start: int, end: int) -> None:
        """追加一章并移动工作层起点；这是两次检查点之间唯一改写历史前缀的地方。"""
        first = getattr(self._entries[start], "timestamp_ms", 0)
        last = getattr(self._entries[end - 1], "timestamp_ms", 0)
        span = "–".join(part for part in (_clock(first), _clock(last)) if part)
        title = f"[前情提要·第 {len(self._chapters) + 1} 章{' ' + span if span else ''}]"
        self._chapters.append(
            TimelineTurn(role="user", content=f"{title}\n{text.strip()}", message_type=CHAPTER_MESSAGE_TYPE)
        )
        self._boundary = end
        self._guard.expect_rewrite("换上前情提要")

    async def _write_chapter(self, segment: List[Any], previous: str) -> str:
        """调 summary 档位把一段记录写成前情提要；失败抛错，由后台准备器记日志后下次重试。"""
        record = canonical.to_text_view([canonical.turn_to_message(turn) for turn in segment])
        prompt = f"[上一章前情提要，仅供衔接]\n{previous}\n\n" if previous else ""
        prompt += f"[这一段的直播记录]\n{record}"
        response = await self._llm.generate(
            prompt,
            profile=self._summary_profile,
            system=self._prompt.render(_CHAPTER_TEMPLATE),
        )
        content: Optional[str] = getattr(response, "content", None)
        if not getattr(response, "success", False) or not isinstance(content, str) or not content.strip():
            raise ValueError(f"前情提要生成失败：{getattr(response, 'error', None) or '返回为空'}")
        return content
