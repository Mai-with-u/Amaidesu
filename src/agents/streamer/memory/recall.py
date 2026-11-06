"""回想工具 ``memory_recall``：按关键词把她记得的事找回来。

旧的记忆工具按固定键取数（往前第几场的回顾、某位观众的印象、本场备忘录），要求主播
先知道该问哪一类；观众的问题通常只给得出一个词。本工具按关键词查五个来源，把内容连同
"什么时候、谁说的"一起摆出来：

- 她已播出的台词（``live_chat`` 的 assistant 行；``dropped_at_ms`` 非空的不算说过）
- 场次档案行（``archive_lines`` 的标题与原话都参与匹配）
- 不针对具体观众的承诺（``commitments``；未了结与已了结都给，判误记的 ``void`` 不给）
- 游戏里做到哪一步（``game_events`` 里的里程碑与阶段汇报；游戏聊天、肢体动作、安全阀不收）
- 本场观众弹幕（``live_chat`` 的 viewer 行；不论 scope 都只查本场）

匹配口径：台词、档案、承诺、游戏进展四类走混合检索（``search``：关键词按两字切分 + BM25，配置了嵌入
服务再加语义一路，按名次融合），按相关度取最相关的 8 条，不要求每个词都出现；本场观众弹幕
不进任何持久索引，照旧在本场里按"每个词都包含"查，补剩下的位置。选好的结果按时间排给她看，
每条带位置（本场写"本场 HH:MM"，跨场写"上一场"或"之前"）与说话人（"你""观众"）。索引由后台
任务补算，刚播出不满 30 秒的台词还查不到——那几句本来就在她眼前的对话里。

不按名字查人：本场以外的结果里出现的已知观众昵称（``viewers`` 当前名与
``viewer_names`` 曾用名，2 字以上）一律替换成"某位观众"——跨场结果留下昵称等于把
ta 的发言和身份带到下一场；本场结果不替换。关键词恰好是某个已知昵称时直接丢掉该词，
并在抬头说明，因为主播会拿观众名字当关键词试探"你还记得 ta 吗"。

分存档的游戏：档案行、承诺与游戏进展挂了游戏的，逐条注明是在哪个存档里（是不是眼下这个）；她过去
说过的话，那场只在别的存档里玩过时注明——翻出"屋顶盖好了"时看得出那是别的世界里的事
（判定见 ``saves``）。

"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from src.modules.logging import get_logger
from src.modules.storage.privacy import MIN_MASK_NAME_LEN, mask_names_in_text
from src.modules.storage.repos.memory_index import INDEXED_GAME_EVENT_TYPES, IndexSource
from src.modules.storage.repos.viewers import is_placeholder_name
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs
from src.modules.types.game_save import GameSave
from src.modules.utils.time_utils import now_ms

from .saves import place_note, session_note
from .search import MemorySearch

__all__ = [
    "MEMORY_RECALL_PROVIDER_NAME",
    "MEMORY_RECALL_SPEC",
    "MEMORY_RECALL_TOOL_NAME",
    "ORDER_NEWEST",
    "ORDER_OLDEST",
    "SCOPE_ALL",
    "SCOPE_THIS_STREAM",
    "MemoryRecallService",
    "build_memory_recall_provider",
]

logger = get_logger("MemoryRecallService")

# 提供者短名与工具全名：ToolSpec.full_name = <provider>_<name>，故 spec.name 取 "recall"
MEMORY_RECALL_PROVIDER_NAME = "memory"
MEMORY_RECALL_TOOL_NAME = "memory_recall"
# 工具目录分类：与旧记忆工具同组（工具页按它归组、随主播 Agent 启用）
MEMORY_TOOL_CATEGORY = "memory"

# 一次回想最多给几条
MAX_RESULTS = 8
# 混合检索多取一些候选：对某位观众的承诺等会在还原时被筛掉，剩下的才取前 8 条
_SEARCH_POOL = 24
# 回想工具是她主动调的，查询向量没算好时可以等一小会儿（被动想起不等）
RECALL_VECTOR_WAIT_MS = 1_500
# 走混合检索的来源：游戏进展要有事件仓储才还原得出来，没接时不查
_RECALL_SOURCES: Tuple[IndexSource, ...] = ("speech", "archive", "commitment")
_RECALL_SOURCES_WITH_EVENTS: Tuple[IndexSource, ...] = (*_RECALL_SOURCES, "event")
# 游戏进展在结果里怎么称呼（只收这两类，见索引仓储的白名单）
_EVENT_LABELS = {"milestone": "里程碑", "report": "阶段汇报"}

SCOPE_ALL = "all"
SCOPE_THIS_STREAM = "this_stream"
ORDER_NEWEST = "newest"
ORDER_OLDEST = "oldest"

# 跨场结果里观众昵称统一换成这个代称（本场结果不替换）
CROSS_SESSION_MASK_LABEL = "某位观众"

MEMORY_RECALL_SPEC = ToolSpec(
    name="recall",
    description=(
        "回想过去的事：你说过的话、每场的档案、答应过的事、游戏里做到哪一步、本场弹幕，按关键词找出最相关的几条"
        "（几个关键词用空格分开，不要求每个词都出现，越相关越靠前选）。问「上次那个事」"
        "「之前提到过什么」「我答应过什么」时用。不按观众名字查人——不知道某位观众是谁就问她本人。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "关键词，多个用空格分开（如「挖矿 钻石」）；按相关度选，词给得准结果更准",
            },
            "scope": {
                "type": "string",
                "enum": [SCOPE_ALL, SCOPE_THIS_STREAM],
                "default": SCOPE_ALL,
                "description": "all 查全部历史；this_stream 只查本场（含本场你说过的话与观众弹幕）",
            },
            "order": {
                "type": "string",
                "enum": [ORDER_NEWEST, ORDER_OLDEST],
                "default": ORDER_NEWEST,
                "description": "选出的结果怎么排：newest 最近的在前；oldest 最早的在前",
            },
        },
        "required": ["query"],
    },
    kind="sync",
    result_event="",
    provider=MEMORY_RECALL_PROVIDER_NAME,
)


@dataclass(frozen=True)
class _RecallHit:
    """一条回想结果：排序用的时刻 + 已渲染好的一行正文。"""

    timestamp_ms: int
    text: str


@dataclass(frozen=True)
class _RecallContext:
    """一次回想的查询上下文：关键词、排序方向、本场与上一场、遮名用的昵称表。"""

    keywords: Sequence[str]
    scope: str
    newest_first: bool
    current_session_id: Optional[int]
    previous_session_id: Optional[int]
    names: Sequence[str]
    # 分存档的游戏眼下所在的存档：结果按它注明是不是眼下这个存档
    current_saves: Mapping[str, GameSave]


def _split_keywords(query: str) -> List[str]:
    """把 query 按空白切成关键词（去重、保序；个数不设限）。"""
    keywords: List[str] = []
    for part in (query or "").split():
        if part not in keywords:
            keywords.append(part)
    return keywords


def _clock_text(timestamp_ms: int) -> str:
    """毫秒时刻 → 本地 HH:MM（位置词只用到时分）。"""
    return datetime.fromtimestamp(max(0, int(timestamp_ms)) / 1000).strftime("%H:%M")


def _normalize_scope(scope: Optional[str]) -> str:
    """scope 归一：只认 this_stream，其余（含空值与未知值）按 all 处理。"""
    value = (scope or "").strip().lower()
    if value == SCOPE_THIS_STREAM:
        return SCOPE_THIS_STREAM
    if value and value != SCOPE_ALL:
        logger.debug(f"memory_recall 收到未知 scope={scope!r}，按 {SCOPE_ALL} 处理")
    return SCOPE_ALL


def _normalize_order(order: Optional[str]) -> str:
    """order 归一：只认 oldest，其余（含空值与未知值）按 newest 处理。"""
    value = (order or "").strip().lower()
    if value == ORDER_OLDEST:
        return ORDER_OLDEST
    if value and value != ORDER_NEWEST:
        logger.debug(f"memory_recall 收到未知 order={order!r}，按 {ORDER_NEWEST} 处理")
    return ORDER_NEWEST


class MemoryRecallService:
    """``memory_recall`` 的业务实现：查五个来源、合并排序、渲染成给模型看的一段文本。

    构造参数全由宿主注入：三个仓储（台词与弹幕、承诺、档案）、混合检索（没有时台词、档案、
    承诺、游戏进展都查不到，只剩本场弹幕）、可选的隐私服务与观众仓储（跨场遮名、昵称关键词识别）、
    进行中场次回调（决定"本场"是第几场）与毫秒时钟（耗时日志）。
    可选的游戏事件仓储还原游戏进展、给台词注明那场的存档，眼下存档回调给结果注明存档；
    缺了事件仓储就不查游戏进展、不注那场的存档。
    仓储都按鸭子类型取用，只依赖它们各自读面上的方法。
    """

    def __init__(
        self,
        chat_repo: Any,
        commitments_repo: Any,
        archive_repo: Any,
        privacy_service: Any = None,
        viewer_repo: Any = None,
        current_session_provider: Optional[Callable[[], Optional[int]]] = None,
        clock: Optional[Callable[[], int]] = None,
        event_repo: Any = None,
        saves_provider: Optional[Callable[[], Mapping[str, GameSave]]] = None,
        search: Optional[MemorySearch] = None,
    ) -> None:
        self._chat_repo = chat_repo
        self._search = search
        self._commitments_repo = commitments_repo
        self._archive_repo = archive_repo
        self._privacy_service = privacy_service
        self._viewer_repo = viewer_repo
        self._current_session_provider = current_session_provider
        self._clock = clock if clock is not None else now_ms
        self._event_repo = event_repo
        self._saves_provider = saves_provider

    async def recall(self, query: str, scope: str = SCOPE_ALL, order: str = ORDER_NEWEST) -> str:
        """执行一次回想，返回给模型看的文本；查询异常不抛，转成「没查到（原因）」。"""
        scope_value = _normalize_scope(scope)
        order_value = _normalize_order(order)
        started_ms = int(self._clock())
        try:
            text = await self._recall(query, scope_value, order_value)
        except Exception as exc:  # noqa: BLE001 - 工具边界：查询失败转成给模型的说明，不反噬决策轮
            logger.error(
                f"memory_recall 查询失败（query={query!r}, scope={scope_value}, order={order_value}）:"
                f" {type(exc).__name__}: {exc}",
                exc=True,
            )
            return f"没查到（{str(exc).strip() or type(exc).__name__}）"
        logger.info(
            f"memory_recall 完成：query={query!r} scope={scope_value} order={order_value}"
            f" 耗时 {int(self._clock()) - started_ms}ms"
        )
        return text

    async def _recall(self, query: str, scope: str, order: str) -> str:
        """查询主体：解析关键词 → 查四个来源 → 排序取前 8 条 → 渲染抬头与正文。"""
        newest_first = order != ORDER_OLDEST
        raw_query = (query or "").strip()
        keywords = _split_keywords(raw_query)
        if not keywords:
            return "（想查什么？给一个或多个关键词，用空格分开，比如「挖矿 钻石」）"

        current_session_id = self._current_session()
        if scope == SCOPE_THIS_STREAM and current_session_id is None:
            return (
                f"{self._header(raw_query, scope, order, 0)}\n"
                "（现在不在直播，没有「本场」可查；scope 用 all 可以查全部历史）"
            )

        names = await self._load_known_names()
        known = set(names)
        ignored = [keyword for keyword in keywords if keyword in known]
        effective = [keyword for keyword in keywords if keyword not in known]
        notice = f"（回想工具不按观众名字查，已忽略 {len(ignored)} 个名字）" if ignored else ""
        if not effective:
            return "\n".join(
                line
                for line in (
                    self._header(raw_query, scope, order, 0),
                    notice,
                    "（没有可用的关键词了，换成跟事情有关的关键词再试试）",
                )
                if line
            )

        session_bounds = await self._chat_repo.list_chat_session_bounds()
        # 档案按"有档案行的场次"检索：保留期清掉早年弹幕之后，档案仍然查得到
        try:
            archive_bounds = await self._archive_repo.list_archived_session_bounds()
        except Exception as exc:  # noqa: BLE001 - 档案读不到只少这一路结果，节奏照常
            logger.warning(f"回想工具读档案场次失败（本次跳过档案）: {type(exc).__name__}: {exc}", exc=True)
            archive_bounds = []
        context = _RecallContext(
            keywords=effective,
            scope=scope,
            newest_first=newest_first,
            current_session_id=current_session_id,
            previous_session_id=self._previous_session(session_bounds, current_session_id),
            names=names,
            current_saves=self._current_saves(),
        )

        # 检索结果按相关度排在前面，本场弹幕补剩下的位置；选好的再按时间排给她看
        hits = (await self._indexed_hits(" ".join(effective), context, archive_bounds))[:MAX_RESULTS]
        hits.extend((await self._danmaku_hits(context))[: MAX_RESULTS - len(hits)])
        hits.sort(key=lambda hit: hit.timestamp_ms, reverse=newest_first)

        lines = [self._header(raw_query, scope, order, len(hits))]
        if notice:
            lines.append(notice)
        if not hits:
            lines.append("（没找到相关的记忆，换个关键词再试试）")
        else:
            lines.extend(hit.text for hit in hits)
        return "\n".join(lines)

    async def _indexed_hits(
        self, query: str, context: _RecallContext, archive_bounds: Sequence[Tuple[int, int]]
    ) -> List[_RecallHit]:
        """台词、档案行、承诺、游戏进展四类：按混合检索的相关度取，还原成行后渲染（按相关度先后）。

        检索只给主键，这里按主键把行读回来再核一遍：没送到观众面前的台词、对某位观众的承诺
        （原话可能夹着 ta 的私事）、判了误记的承诺、白名单以外的游戏事件都不给。scope=this_stream
        时只查本场（承诺按登记它的场次算）。档案行的时刻取写下它的那一场最后一条明细的时刻。
        """
        if self._search is None:
            return []
        sources = _RECALL_SOURCES_WITH_EVENTS if self._event_repo is not None else _RECALL_SOURCES
        found = await self._search.search(
            query,
            sources=sources,
            live_session_id=context.current_session_id if context.scope == SCOPE_THIS_STREAM else None,
            limit=_SEARCH_POOL,
            wait_vector_ms=RECALL_VECTOR_WAIT_MS,
        )
        ids: Dict[str, List[int]] = {source: [] for source in sources}
        for hit in found:
            ids[hit.source].append(hit.source_id)
        speech = {int(row.id): row for row in await self._chat_repo.list_speech_by_ids(chat_ids=ids["speech"])}
        lines = {int(line.id): line for line in await self._archive_repo.list_lines_by_ids(line_ids=ids["archive"])}
        commitments: Dict[int, Any] = {}
        for commitment_id in ids["commitment"]:
            row = await self._commitments_repo.get_commitment(commitment_id=commitment_id)
            if row is not None and row.viewer_id is None and row.status in ("open", "closed"):
                commitments[commitment_id] = row
        events = await self._events_by_ids(ids.get("event", []))
        notes = await self._session_notes([row.live_session_id for row in speech.values()], context)
        last_ms = dict(archive_bounds)
        hits: List[_RecallHit] = []
        for hit in found:
            if hit.source == "speech" and hit.source_id in speech:
                hits.append(self._speech_hit(speech[hit.source_id], notes, context))
            elif hit.source == "archive" and hit.source_id in lines:
                line = lines[hit.source_id]
                session_id = int(line.live_session_id)
                hits.append(self._archive_hit(line, session_id, int(last_ms.get(session_id, 0)), context))
            elif hit.source == "commitment" and hit.source_id in commitments:
                hits.append(self._commitment_hit(commitments[hit.source_id], context))
            elif hit.source == "event" and hit.source_id in events:
                hits.append(self._event_hit(events[hit.source_id], context))
        return hits

    async def _events_by_ids(self, event_ids: Sequence[int]) -> Dict[int, Any]:
        """按主键读回游戏事件，只留里程碑与阶段汇报（索引已按白名单收，这里再核一遍）。"""
        if self._event_repo is None or not event_ids:
            return {}
        rows = await self._event_repo.list_events_by_ids(event_ids=event_ids)
        return {int(row.id): row for row in rows if row.event_type in INDEXED_GAME_EVENT_TYPES}

    def _event_hit(self, row: Any, context: _RecallContext) -> _RecallHit:
        """一条游戏进展渲染成「位置 + 游戏里（里程碑/阶段汇报）+ 正文」，挂了存档的注明是哪个存档。"""
        session_id = int(row.live_session_id)
        text = (
            f"- {self._when(session_id, row.timestamp_ms, context)} 游戏里（{_EVENT_LABELS[row.event_type]}）："
            f"{self._mask((row.message or '').strip(), session_id, context)}"
        )
        note = place_note(row.game, row.save_id, row.save_name, context.current_saves)
        return _RecallHit(timestamp_ms=int(row.timestamp_ms), text=f"{text}（{note}）" if note else text)

    def _speech_hit(self, row: Any, notes: Mapping[int, str], context: _RecallContext) -> _RecallHit:
        """一句台词渲染成「位置 + 你说：原话」，那场只玩过别的存档时补一句注。"""
        note = notes.get(row.live_session_id, "")
        text = (
            f"- {self._when(row.live_session_id, row.timestamp_ms, context)} 你说："
            f"「{self._mask(row.content, row.live_session_id, context)}」"
        )
        return _RecallHit(timestamp_ms=int(row.timestamp_ms), text=f"{text}（{note}）" if note else text)

    async def _session_notes(self, session_ids: Sequence[Optional[int]], context: _RecallContext) -> Dict[int, str]:
        """过去几场里，眼下分存档的游戏只在别的存档里玩过的，各给一句注（本场不注）。"""
        notes: Dict[int, str] = {}
        if self._event_repo is None or not context.current_saves:
            return notes
        for session_id in dict.fromkeys(item for item in session_ids if item is not None):
            if session_id == context.current_session_id:
                continue
            try:
                spans = await self._event_repo.list_session_save_spans(live_session_id=int(session_id))
            except Exception as exc:  # noqa: BLE001 - 查不到那场的存档只少一句注，回想照常
                logger.warning(f"回想工具读场次 {session_id} 的存档段失败（不注存档）: {exc}", exc=exc)
                continue
            note = session_note(spans, context.current_saves)
            if note:
                notes[int(session_id)] = note
        return notes

    async def _danmaku_hits(self, context: _RecallContext) -> List[_RecallHit]:
        """本场观众弹幕；没在直播时没有本场，直接跳过。"""
        current_session_id = context.current_session_id
        if current_session_id is None:
            return []
        rows = await self._chat_repo.search_recall_danmaku(
            keywords=list(context.keywords),
            live_session_id=current_session_id,
            limit=MAX_RESULTS,
            newest_first=context.newest_first,
        )
        return [
            _RecallHit(
                timestamp_ms=int(row.timestamp_ms),
                text=(
                    f"- {self._when(current_session_id, row.timestamp_ms, context)} 观众说："
                    f"「{self._mask(row.content, current_session_id, context)}」"
                ),
            )
            for row in rows
        ]

    def _archive_hit(self, line: Any, session_id: int, timestamp_ms: int, context: _RecallContext) -> _RecallHit:
        """一行档案渲染成「位置 + 你说（档案「标题」）+ 她的原话」。"""
        when = self._when(session_id, timestamp_ms, context)
        title = self._mask((line.text or "").strip(), session_id, context)
        quote = self._mask((line.quote or "").strip(), session_id, context)
        body = f"- {when} 你说（档案「{title}」）"
        if quote:
            body += f"：「{quote}」"
        note = place_note(line.game, line.save_id, line.save_name, context.current_saves)
        if note:
            body += f"（{note}）"
        return _RecallHit(timestamp_ms=timestamp_ms, text=body)

    def _commitment_hit(self, row: Any, context: _RecallContext) -> _RecallHit:
        """一条承诺渲染成「位置 + 你答应过（状态）+ 标题与原话 / 了结原话」。"""
        session_id = int(row.created_session_id) if row.created_session_id is not None else None
        timestamp_ms = int(row.created_at_ms)
        when = self._when(session_id, timestamp_ms, context)
        status = "已了结" if row.status == "closed" else "未了结"
        what = self._mask((row.what or "").strip(), session_id, context)
        quote = self._mask((row.quote or "").strip(), session_id, context)
        body = f"- {when} 你答应过（{status}）{what}"
        if quote:
            body += f"：原话「{quote}」"
        close_quote = self._mask((row.close_quote or "").strip(), session_id, context)
        if row.status == "closed" and close_quote:
            body += f"；了结时你说「{close_quote}」"
        note = place_note(row.game, row.save_id, row.save_name, context.current_saves)
        if note:
            body += f"（{note}）"
        return _RecallHit(timestamp_ms=timestamp_ms, text=body)

    def _when(self, session_id: Optional[int], timestamp_ms: int, context: _RecallContext) -> str:
        """位置词：本场写「本场 HH:MM」，上一场写「上一场」，其余（含无场次的承诺）写「之前」。"""
        if (
            session_id is not None
            and context.current_session_id is not None
            and session_id == context.current_session_id
        ):
            return f"本场 {_clock_text(timestamp_ms)}"
        if (
            session_id is not None
            and context.previous_session_id is not None
            and session_id == context.previous_session_id
        ):
            return "上一场"
        return "之前"

    def _mask(self, text: str, session_id: Optional[int], context: _RecallContext) -> str:
        """跨场结果把已知观众昵称换成「某位观众」；本场结果原样返回。

        跨场结果再过一道本场遮名集合（``PrivacyService.mask_speech``）：本场发过
        "忘了我"的观众，名字不该经回想工具回流进决策。
        """
        if not text:
            return text
        if (
            session_id is not None
            and context.current_session_id is not None
            and session_id == context.current_session_id
        ):
            return text
        masked = mask_names_in_text(text, context.names, replacement=CROSS_SESSION_MASK_LABEL)
        if self._privacy_service is not None:
            masked = self._privacy_service.mask_speech(masked)
        return masked

    def _header(self, query: str, scope: str, order: str, count: int) -> str:
        """一行工具抬头：关键词、范围、顺序、条数。"""
        scope_text = "本场" if scope == SCOPE_THIS_STREAM else "全部"
        order_text = "最新在前" if order != ORDER_OLDEST else "最早在前"
        return f"【回想】关键词「{query}」｜范围：{scope_text}｜顺序：{order_text}｜{count} 条"

    def _previous_session(
        self,
        session_bounds: Sequence[Tuple[int, int]],
        current_session_id: Optional[int],
    ) -> Optional[int]:
        """上一场 = 按场内最后一条明细排下来、紧跟本场的那个场次；没在直播时取最近一场。"""
        for session_id, _last_ms in session_bounds:
            if session_id != current_session_id:
                return session_id
        return None

    async def _load_known_names(self) -> List[str]:
        """全部可用作遮名与昵称识别的观众昵称：2 字以上、非占位名。

        读不到观众表时按"没有已知昵称"降级：本次不遮名、也不识别昵称关键词，
        回想本身照常进行（遮名是辅助能力，不该让整次查询失败）。
        """
        if self._viewer_repo is None:
            return []
        try:
            raw_names = await self._viewer_repo.list_known_names()
        except Exception as exc:  # noqa: BLE001 - 遮名辅助能力降级，不反噬回想本身
            logger.warning(
                f"回想工具读取观众昵称失败（本次不遮名、也不识别昵称关键词）: {type(exc).__name__}: {exc}",
                exc=True,
            )
            return []
        names: List[str] = []
        for raw in raw_names:
            name = (raw or "").strip()
            if len(name) >= MIN_MASK_NAME_LEN and not is_placeholder_name(name):
                names.append(name)
        return names

    def _current_saves(self) -> Mapping[str, GameSave]:
        """分存档的游戏眼下所在的存档；没注入回调或回调出错时按"都不分存档"处理（不加注）。"""
        if self._saves_provider is None:
            return {}
        try:
            return dict(self._saves_provider())
        except Exception as exc:  # noqa: BLE001 - 读不到存档只少几句注，不反噬回想本身
            logger.warning(f"回想工具读取眼下存档失败（本次不注存档）: {exc}", exc=exc)
            return {}

    def _current_session(self) -> Optional[int]:
        """进行中场次的主键；没注入回调、回调返回 None 或抛异常时都按未开播处理。"""
        if self._current_session_provider is None:
            return None
        try:
            value = self._current_session_provider()
        except Exception as exc:  # noqa: BLE001 - 回调异常按未开播处理，不反噬回想本身
            logger.warning(f"回想工具读取进行中场次失败（按未开播处理）: {type(exc).__name__}: {exc}", exc=True)
            return None
        return int(value) if value is not None else None


def build_memory_recall_provider(service: MemoryRecallService) -> ToolProvider:
    """组装回想工具 Provider（provider="memory"，工具全名 ``memory_recall``）。

    简单工具正典路径：ToolSpec + 普通 async 函数（``as_tool_impl`` 包装）+
    ``make_provider_from_specs`` 组装，装配处一行 ``register_provider``。
    """

    async def _recall(invocation: ToolInvocation) -> str:
        args = invocation.arguments or {}
        return await service.recall(
            query=str(args.get("query", "") or ""),
            scope=str(args.get("scope", SCOPE_ALL) or SCOPE_ALL),
            order=str(args.get("order", ORDER_NEWEST) or ORDER_NEWEST),
        )

    return make_provider_from_specs(
        MEMORY_RECALL_PROVIDER_NAME,
        [(MEMORY_RECALL_SPEC, as_tool_impl(MEMORY_RECALL_SPEC.full_name, _recall))],
        category=MEMORY_TOOL_CATEGORY,
    )
