"""主播时间线：本场直播记录的只追加投影，超预算时按章压成前情提要。

Planner 与 Replyer 每个决策窗都要带上本场对话。对话事实源仍是 ``live_chat``
（加上没落库的调试对话），时间线只是它进 LLM 前的投影，负责两件事：

- **只追加**：记录按主播第一次看到的先后进入时间线，进去后不再挪动、不再改写。
  相邻两个决策窗的历史前缀逐字一致，服务端前缀缓存才能命中。
- **按章压缩**：历史涨过提前整理线时，后台用 summary 档位把最老的一段写成一章
  前情提要；涨到预算时直接换上，决策不用停下来等。章节只追加，两次换章之间前缀
  不变；摘要迟迟出不来、历史涨到预算两倍时，才同步把最老一段换成占位说明兜底。
- **章节层有上限**：前情提要各章合计超过预算的 30% 时，最老的章移出视野，只在
  章节层最前面的【前情目录】里留一行"时段 + 这一章第一句"。目录行也算进同一个
  上限，放不下时从最老的行丢起。章节层因此不会越积越多、把长场挤成一串占位说明；
  目录只在有章移出时才变，两次移出之间历史前缀照样逐字不变。

投影出的条目与 ``live_chat`` 历史行同形（role / content / sender_name /
message_type / message_id / timestamp_ms），canonical 映射原样可用；前情提要与
前情目录是 ``message_type = "chapter"`` 的 user 条目，游戏里程碑与主播自己在游戏里
开始做的事是 ``message_type = "game"`` 的 user 条目（行首带绝对时刻，写入后不再变化）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.agents.streamer import canonical
from src.modules.context import BackgroundCheckpoint, ContextBudgetConfig, PrefixGuard, TokenCounter, Watermarks
from src.modules.context import fold_count, resolve_watermarks
from src.modules.llm.capabilities import ProfileContextWindowLookup, validate_llm_capability
from src.modules.logging import get_logger

__all__ = [
    "CHAPTER_MESSAGE_TYPE",
    "GAME_MESSAGE_TYPE",
    "GameEventBatch",
    "StreamerTimeline",
    "TimelineTurn",
    "note_turn",
]

logger = get_logger("StreamerTimeline")

#: 前情提要条目的消息类型（canonical 按原文渲染，不加昵称前缀与消息 ID）
CHAPTER_MESSAGE_TYPE = "chapter"

#: 游戏记录条目的消息类型：游戏里程碑、主播在游戏里开始做的事（同样按原文渲染）
GAME_MESSAGE_TYPE = "game"

#: 前情提要的系统提示词模板
_CHAPTER_TEMPLATE = "chapter_system"

# 最近这么多条记录永远保持原文：主播正在接的话不能被压成摘要
_PROTECTED_RECENT = 20

# 历史涨到预算的这么多倍、后台摘要还没出来时，同步用占位说明兜底
_HARD_FOLD_FACTOR = 2.0

# 章节层（前情目录 + 仍在视野里的各章）最多占时间线预算的这一比例。
# 换章时工作层被压到"回落线减去章节层"（回落线默认是预算的 0.4 倍）：章节层压在 0.3 倍
# 以内，换章后工作层至少还有约 0.1 倍的原文空间，历史总量能回到提前整理线以下；没有这道
# 上限，热闹长场里章节越积越多，换章越来越频繁，最后涨到两倍预算、每窗都退化成占位说明。
# 0.3 倍在默认 32k 预算下约 9.6k token，放得下十几章前情提要，平常长度的场整场碰不到。
_CHAPTER_LAYER_RATIO = 0.3

# 前情目录每行"这段讲了什么"最多这么多字：一句话足够她认出"这事开场讲过"、知道有这么
# 回事，再长就成了第二份摘要；目录行和章节共用同一个上限，行越短能留下的段落越多。
_CATALOG_GIST_CHARS = 30

# 前情目录消息的抬头：告诉她这些段落已不在眼前，细节记不清就坦白、或问问弹幕。
# 不指向 memory_recall：回想工具按关键词查，翻不到"这一段讲了什么"，指过去只会白调一次工具。
_CATALOG_HEADER = "【前情目录】（这些段落已移出上下文；细节记不清就坦白，或问问弹幕）"

# 被占位说明顶替的那一段没有正文可摘第一句，目录行只写这句
_UNWRITTEN_GIST = "这一段没来得及整理"

# 章节写出来了、但正文里摘不出一句有内容的话（比如只有标点和记号）时，目录行写这句，
# 保证每一行都是"[时段] 内容"，不会退化成只剩时段
_NO_GIST = "这一段没写出概要"

# 切第一句用的句末标点（含换行）；逗号、冒号不算句末，避免把半句话当概要
_SENTENCE_END = re.compile(r"[。！？!?；;\n]")

# 整句里的 markdown 强调与行内代码记号：模型偶尔会把关键词加粗，目录行是纯文本，
# 成对的记号只去一半会留下孤零零的右半边，所以整句统一去掉。下划线不动：道具名、
# 工具名里的下划线是名字的一部分
_EMPHASIS_MARKS = re.compile(r"[*`]+")

# 第一句开头的列表符号、标题符号与空白：模型偶尔不按要求写成列表，目录行里不留这些记号
_LEADING_MARKS = "-•·#>　 \t"

# 第一句开头的序号："1." "1、" "1)" "（1）" "一、" 一类；"2.5 倍"这种小数不算序号
_LEADING_ORDINAL = re.compile(
    r"^(?:[（(][0-9一二三四五六七八九十]{1,3}[)）]|[0-9]{1,3}(?:[、．)）]|\.(?!\d))|[一二三四五六七八九十]{1,3}[、.．])"
)


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


def _catalog_gist(body: str) -> str:
    """一章正文的第一句，截到目录行字数上限（不加省略号，行长严格不超上限）。

    章节提示词要求第一句就概括这段聊了什么、做了什么，这里只机械地切出来，不再调模型。
    先按句末标点或换行切开，再逐段清理：去掉整句里的加粗/代码记号，去掉开头的列表符号、
    标题符号和序号，清理后不为空的第一段就是概要；某一段清理后什么都不剩（比如单独
    一行 ``1.`` 或 ``**``），接着看下一段。所有段都是空的时返回固定兜底文字，目录行
    因此永远有内容。
    """
    for piece in _SENTENCE_END.split(body):
        sentence = _EMPHASIS_MARKS.sub("", piece).strip().lstrip(_LEADING_MARKS)
        sentence = _LEADING_ORDINAL.sub("", sentence, count=1).strip().lstrip(_LEADING_MARKS)
        if sentence:
            return sentence[:_CATALOG_GIST_CHARS].rstrip()
    return _NO_GIST


@dataclass(frozen=True)
class _Chapter:
    """一章前情提要：在视野里时整章进历史，移出视野后只在前情目录里留 ``catalog_line`` 一行。"""

    turn: TimelineTurn
    catalog_line: str
    # 整章作为一条消息的 token 数（写入后不变，换章与上限判断时不再重算）
    tokens: int


def note_turn(label: str, body: str, timestamp_ms: int) -> TimelineTurn:
    """一条游戏记录：``[HH:MM 标签] 正文``。

    用绝对时刻而不是"几分钟前"：条目进了时间线就不再改写，相对时间每窗都会变，
    只能放在参考段里。
    """
    clock = _clock(timestamp_ms)
    head = f"{clock} {label}" if clock else label
    return TimelineTurn(
        role="user", content=f"[{head}] {body}", message_type=GAME_MESSAGE_TYPE, timestamp_ms=timestamp_ms
    )


@dataclass(frozen=True)
class GameEventBatch:
    """一个决策窗新入账的游戏事件批次（决策窗边界批量追加进时间线的那一批）。

    - ``events``：本批全部时间线行原文（含私聊行）；本轮新鲜语料从这里取，
      不再从历史回放里提取。
    - ``fresh_chat``：本批里游戏聊天的非私聊行原话，随本轮说话交给表达侧。
    - ``private_chat``：私聊行。私聊不进时间线（表达侧与历史都看不到），
      只在本批交给决策层，且下一窗不再携带。
    - ``facts_text``：本批状态跃迁事实块（账本从结构化条目渲染，带原钟点标记）。
    - ``timeline_turns``：本批待追加进时间线的条目（决策窗边界由持有方批量落账）。
    """

    events: Tuple[str, ...] = ()
    fresh_chat: Tuple[str, ...] = ()
    private_chat: Tuple[str, ...] = ()
    facts_text: str = ""
    timeline_turns: Tuple[TimelineTurn, ...] = ()


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
        # llm_service 另需 async generate（前情提要生成用）；窗口查询契约在装配期校验，
        # 注入了缺方法的对象时启动即报错，而不是运行期静默按窗口 0 换算预算
        validate_llm_capability(llm_service, ProfileContextWindowLookup, parameter="llm_service")
        self._llm = llm_service
        self._prompt = prompt_service
        self._profiles = tuple(profiles)
        self._summary_profile = summary_profile
        self._counter = TokenCounter()
        self._entries: List[Any] = []
        self._entry_tokens: List[int] = []
        self._keys: set[str] = set()
        # 本场写过的全部章节（移出视野的也留着：编号接着数，下一章仍拿最后一章衔接）
        self._chapters: List[_Chapter] = []
        # 章节层的两道分界（都是 _chapters 的下标）：[:_catalog_from] 的目录行已因放不下丢弃，
        # [_catalog_from:_visible_from] 已移出视野、只在前情目录里留一行，[_visible_from:] 整章进历史
        self._catalog_from = 0
        self._visible_from = 0
        # 前情目录消息只在分界移动时重建：两次移出之间同一个对象原样复用，历史前缀逐字不变
        self._catalog: Optional[TimelineTurn] = None
        self._catalog_tokens = 0
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
        self._catalog_from = 0
        self._visible_from = 0
        self._catalog = None
        self._catalog_tokens = 0
        self._boundary = 0
        self._guard.expect_rewrite("新场次")

    async def project(self, turns: Sequence[Any], *, scope: Any = None) -> List[Any]:
        """读入本窗的完整对话，返回进 LLM 的历史：前情目录 + 视野里的各章 + 工作层原文。

        每个决策窗依次：追加新出现的记录 → 换上后台已写好的一章 → 章节层超上限就把最老的章
        移进前情目录 → 摘要迟迟不来、历史涨到两倍预算时用占位说明兜底（兜底章同样受上限约束）
        → 越过提前整理线时发起下一章的后台整理。上限要在兜底判断之前收紧：章节层压回去之后
        历史通常已回到预算内，就不必再用占位说明丢掉一段原文。``scope`` 是对话所属的场次，
        变了就先清空重来。
        """
        if scope != self._scope:
            await self.reset()
            self._scope = scope
        self._append_unseen(turns)
        marks = self._watermarks()
        self._apply_ready_chapter()
        self._limit_chapter_layer(marks)
        if self._history_tokens() > marks.budget * _HARD_FOLD_FACTOR:
            await self._hard_fold(marks)
            self._limit_chapter_layer(marks)
        if self._history_tokens() > marks.prepare and not self._pending.busy:
            self._start_chapter(marks)
        view = [*self._chapter_layer(), *self._entries[self._boundary :]]
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
        for profile in self._profiles:
            try:
                value = self._llm.get_profile_context_window(profile)
            except Exception as exc:  # noqa: BLE001 - 窗口查不到按未配置处理，决策不中断
                logger.warning(f"查询 {profile} 上下文窗口失败，按未配置处理：{exc}")
                continue
            if isinstance(value, int) and value > 0:
                windows.append(value)
        return resolve_watermarks(self._budget, min(windows) if windows else 0)

    def _chapter_layer(self) -> List[TimelineTurn]:
        """章节层：前情目录（有移出的章时）在最前，后面是仍在视野里的各章。"""
        catalog = [self._catalog] if self._catalog is not None else []
        return [*catalog, *(chapter.turn for chapter in self._chapters[self._visible_from :])]

    def _chapter_layer_tokens(self) -> int:
        """章节层占的 token：前情目录 + 视野里的各章（移出视野的整章不再计入）。"""
        return self._catalog_tokens + sum(chapter.tokens for chapter in self._chapters[self._visible_from :])

    def _history_tokens(self) -> int:
        return self._chapter_layer_tokens() + sum(self._entry_tokens[self._boundary :])

    def _fold_end(self, marks: Watermarks) -> int:
        """压到回落线需要把工作层压到哪一条为止（不含）；最近的记录始终保留原文。"""
        keep = max(0, marks.target - self._chapter_layer_tokens())
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
        # 衔接用的上一章不管是否还在视野里都交给写手：移出视野只影响 Planner 看到什么
        previous = self._chapters[-1].turn.content if self._chapters else ""
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
        self._append_chapter(note, self._boundary, end, unwritten=True)
        logger.warning(f"主播时间线超出预算两倍且摘要未就绪，已用占位说明移走 {count} 条记录")

    def _append_chapter(self, text: str, start: int, end: int, *, unwritten: bool = False) -> None:
        """追加一章并移动工作层起点；与章节移出视野一起，是两次检查点之间仅有的改写历史前缀的地方。

        同时备好这一章将来移出视野时留在前情目录里的那一行：时段与章节标题里的一致，
        内容是正文第一句（清理记号后摘不出话时写固定兜底文字）；``unwritten`` 表示这一章
        是占位说明（摘要没来得及写），没有可摘的正文，目录行只写"这一段没来得及整理"。
        时间线条目都没有时刻时，目录行用章号代替时段，好歹留下先后顺序。
        """
        number = len(self._chapters) + 1
        first = getattr(self._entries[start], "timestamp_ms", 0)
        last = getattr(self._entries[end - 1], "timestamp_ms", 0)
        span = "–".join(part for part in (_clock(first), _clock(last)) if part)
        title = f"[前情提要·第 {number} 章{' ' + span if span else ''}]"
        body = text.strip()
        turn = TimelineTurn(role="user", content=f"{title}\n{body}", message_type=CHAPTER_MESSAGE_TYPE)
        gist = _UNWRITTEN_GIST if unwritten else _catalog_gist(body)
        anchor = span or f"第 {number} 章"
        self._chapters.append(
            _Chapter(
                turn=turn,
                catalog_line=f"[{anchor}] {gist}",
                tokens=self._counter.message(canonical.turn_to_message(turn)),
            )
        )
        self._boundary = end
        self._guard.expect_rewrite("换上前情提要")

    def _limit_chapter_layer(self, marks: Watermarks) -> None:
        """章节层超上限时，把最老的章移出视野、在前情目录留一行；目录放不下时丢最老的行。

        她看到目录就知道开场那几段讲过什么，话题绕回来时不会对同一批人重讲、编出前后矛盾
        的版本；被问到细节而眼前没有时，按抬头的提示坦白记不清或问弹幕，不凭目录一行编细节。

        触发：刚换上一章（或占位说明）、或模型窗口变小让上限跟着降低之后，前情目录加视野里
        各章合计超过预算的 30%。每一步只做一件事，做完重新比较，直到放得下：

        - 视野里不止一章：把最老的一章移出视野，目录多一行；
        - 只剩最新一章，它自己放得下上限、只是被目录挤占：丢目录最老的一行。最新一章连着
          工作层，答应过谁什么多半写在里面，比几小时前那一行目录更要紧，所以宁可丢旧目录行
          也先留住它；
        - 只剩最新一章，且它独自就超上限（预算极小，或章节写手没守住字数）：它怎么都留不住，
          先把它移出、只留目录行，再按需丢最老的目录行。如果先丢目录行就是白丢：目录丢光了
          这一章照样要走，结果每次换章后目录只剩一行、视野里一章都没有。

        移出的章只是不再出现在 project() 的返回里，内存数据不删；上限放宽时也不移回，免得
        前缀来回变。目录只在这里变，所以两次移出之间历史前缀逐字不变；一旦变了，就预告
        前缀改写，免得前缀守卫误报。
        """
        limit = int(marks.budget * _CHAPTER_LAYER_RATIO)
        moved = dropped = 0
        while self._chapter_layer_tokens() > limit:
            visible = len(self._chapters) - self._visible_from
            # 最新一章单独就超上限时先移出它（没有目录行时层里只有这一章，同样走这一支）
            if visible > 1 or (visible == 1 and self._chapters[-1].tokens > limit):
                self._visible_from += 1
                moved += 1
            elif self._catalog_from < self._visible_from:
                self._catalog_from += 1
                dropped += 1
            else:
                # 视野里只剩放得下的章、目录也没有行了，层本该已在上限内；兜底防死循环
                break
            self._rebuild_catalog()
        if not moved and not dropped:
            return
        self._guard.expect_rewrite("前情提要移出视野")
        logger.info(
            f"主播时间线章节层超过上限 {limit} token：{moved} 章移进前情目录，丢弃最老的 {dropped} 行目录"
            f"（目录现有 {self._visible_from - self._catalog_from} 行，视野里还有 "
            f"{len(self._chapters) - self._visible_from} 章）"
        )

    def _rebuild_catalog(self) -> None:
        """按两道分界重拼前情目录消息：抬头一行，之后每个移出视野的章一行，最老的在上。"""
        lines = [chapter.catalog_line for chapter in self._chapters[self._catalog_from : self._visible_from]]
        if not lines:
            # 目录行全丢光（或还没有章移出）时整条消息不出现，不留只有抬头的空目录
            self._catalog = None
            self._catalog_tokens = 0
            return
        self._catalog = TimelineTurn(
            role="user", content="\n".join([_CATALOG_HEADER, *lines]), message_type=CHAPTER_MESSAGE_TYPE
        )
        self._catalog_tokens = self._counter.message(canonical.turn_to_message(self._catalog))

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
        content: Optional[str] = response.content
        if not response.ok or not isinstance(content, str) or not content.strip():
            raise ValueError(f"前情提要生成失败：{response.error or '返回为空'}")
        return content
