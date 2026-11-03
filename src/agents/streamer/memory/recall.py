"""回想工具 ``memory_recall``：按关键词把她记得的事找回来。

旧的记忆工具按固定键取数（往前第几场的回顾、某位观众的印象、本场备忘录），要求主播
先知道该问哪一类；观众的问题通常只给得出一个词。本工具按关键词查四个来源，把内容连同
"什么时候、谁说的"一起摆出来：

- 她已播出的台词（``live_chat`` 的 assistant 行；``dropped_at_ms`` 非空的不算说过）
- 场次档案行（``archive_lines`` 的标题与原话都参与匹配）
- 不针对具体观众的承诺（``commitments``；未了结与已了结都给，判误记的 ``void`` 不给）
- 本场观众弹幕（``live_chat`` 的 viewer 行；不论 scope 都只查本场）

匹配口径：每个关键词一个"包含"判断（``live_chat`` 走 SQL ``LIKE '%词%'``，档案与承诺
在代码里做同一件事），一行要同时命中全部关键词（多词取交集）。结果按时间排序取前 8 条，
每条带位置（本场写"本场 HH:MM"，跨场写"上一场"或"之前"）与说话人（"你""观众"）。

不按名字查人：本场以外的结果里出现的已知观众昵称（``viewers`` 当前名与
``viewer_names`` 曾用名，2 字以上）一律替换成"某位观众"——跨场结果留下昵称等于把
ta 的发言和身份带到下一场；本场结果不替换。关键词恰好是某个已知昵称时直接丢掉该词，
并在抬头说明，因为主播会拿观众名字当关键词试探"你还记得 ta 吗"。

档案与承诺没有现成的检索方法：档案按场次逐场读回后在代码里做关键词交集（每场至多
8 行，按排序方向读到够 8 条即停），承诺一次分页读全（账本行数少）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Optional, Sequence, Tuple

from src.modules.logging import get_logger
from src.modules.storage.privacy import MIN_MASK_NAME_LEN, mask_names_in_text
from src.modules.storage.repos.viewers import is_placeholder_name
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs
from src.modules.utils.time_utils import now_ms

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
# 承诺账本分页读的大小：账本行数少，读全才能按时间挑出最新或最老的一批
_COMMITMENT_PAGE_SIZE = 200

SCOPE_ALL = "all"
SCOPE_THIS_STREAM = "this_stream"
ORDER_NEWEST = "newest"
ORDER_OLDEST = "oldest"

# 跨场结果里观众昵称统一换成这个代称（本场结果不替换）
CROSS_SESSION_MASK_LABEL = "某位观众"

MEMORY_RECALL_SPEC = ToolSpec(
    name="recall",
    description=(
        "回想过去的事：你说过的话、每场的档案、答应过的事、本场弹幕，按关键词全文查找"
        "（几个关键词用空格分开，全部命中的才算）。问「上次那个事」「之前提到过什么」"
        "「我答应过什么」时用。不按观众名字查人——不知道某位观众是谁就问她本人。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "关键词，多个用空格分开（如「挖矿 钻石」）；词越多结果越少",
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
                "description": "newest 从最近的开始取；oldest 从最早的开始取",
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


def _split_keywords(query: str) -> List[str]:
    """把 query 按空白切成关键词（去重、保序；个数不设限）。"""
    keywords: List[str] = []
    for part in (query or "").split():
        if part not in keywords:
            keywords.append(part)
    return keywords


def _matches_all_keywords(text: str, keywords: Sequence[str]) -> bool:
    """档案与承诺的"每词子串、取交集"判定（与 live_chat 的 LIKE 一样不分英文大小写）。"""
    haystack = (text or "").casefold()
    return all(keyword.casefold() in haystack for keyword in keywords)


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
    """``memory_recall`` 的业务实现：查四个来源、合并排序、渲染成给模型看的一段文本。

    构造参数全由宿主注入：四个仓储（台词与弹幕、承诺、档案）、可选的隐私服务与观众仓储
    （跨场遮名、昵称关键词识别）、进行中场次回调（决定"本场"是第几场）与毫秒时钟（耗时日志）。
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
    ) -> None:
        self._chat_repo = chat_repo
        self._commitments_repo = commitments_repo
        self._archive_repo = archive_repo
        self._privacy_service = privacy_service
        self._viewer_repo = viewer_repo
        self._current_session_provider = current_session_provider
        self._clock = clock if clock is not None else now_ms

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
        logger.debug(
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
        )

        hits: List[_RecallHit] = []
        hits.extend(await self._speech_hits(context))
        hits.extend(await self._danmaku_hits(context))
        hits.extend(await self._archive_hits(context, archive_bounds))
        hits.extend(await self._commitment_hits(context))
        # 稳定排序：同一时刻的先后按来源收集顺序（台词 → 弹幕 → 档案 → 承诺）
        hits.sort(key=lambda hit: hit.timestamp_ms, reverse=newest_first)
        hits = hits[:MAX_RESULTS]

        lines = [self._header(raw_query, scope, order, len(hits))]
        if notice:
            lines.append(notice)
        if not hits:
            lines.append("（没找到相关的记忆，换个关键词再试试）")
        else:
            lines.extend(hit.text for hit in hits)
        return "\n".join(lines)

    async def _speech_hits(self, context: _RecallContext) -> List[_RecallHit]:
        """她已播出的台词；scope=this_stream 时只查本场。"""
        rows = await self._chat_repo.search_recall_speech(
            keywords=list(context.keywords),
            limit=MAX_RESULTS,
            live_session_id=context.current_session_id if context.scope == SCOPE_THIS_STREAM else None,
            newest_first=context.newest_first,
        )
        return [
            _RecallHit(
                timestamp_ms=int(row.timestamp_ms),
                text=(
                    f"- {self._when(row.live_session_id, row.timestamp_ms, context)} 你说："
                    f"「{self._mask(row.content, row.live_session_id, context)}」"
                ),
            )
            for row in rows
        ]

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

    async def _archive_hits(
        self,
        context: _RecallContext,
        session_bounds: Sequence[Tuple[int, int]],
    ) -> List[_RecallHit]:
        """场次档案行：按场次逐场读回后在代码里做关键词交集。

        档案表没有检索方法，只能按场次读；按排序方向从新到旧（或从旧到新）逐场扫，
        收集到够 8 条就停——更晚的场次不可能再排进前 8 条。
        """
        sessions: List[Tuple[int, int]] = list(session_bounds)
        if context.scope == SCOPE_THIS_STREAM:
            sessions = [item for item in sessions if item[0] == context.current_session_id]
        elif not context.newest_first:
            sessions = list(reversed(sessions))

        hits: List[_RecallHit] = []
        for session_id, last_ms in sessions:
            try:
                lines = await self._archive_repo.list_lines(live_session_id=session_id)
            except Exception as exc:  # noqa: BLE001 - 单场读失败只跳过这一场，不废掉整次回想
                logger.warning(
                    f"回想工具读取场次 {session_id} 的档案行失败（跳过这一场）: {type(exc).__name__}: {exc}",
                    exc=True,
                )
                continue
            for line in lines:
                if not _matches_all_keywords(f"{line.text}\n{line.quote}", context.keywords):
                    continue
                hits.append(self._archive_hit(line, session_id, int(last_ms), context))
            if len(hits) >= MAX_RESULTS:
                break
        return hits

    def _archive_hit(self, line: Any, session_id: int, timestamp_ms: int, context: _RecallContext) -> _RecallHit:
        """一行档案渲染成「位置 + 你说（档案「标题」）+ 她的原话」。"""
        when = self._when(session_id, timestamp_ms, context)
        title = self._mask((line.text or "").strip(), session_id, context)
        quote = self._mask((line.quote or "").strip(), session_id, context)
        body = f"- {when} 你说（档案「{title}」）"
        if quote:
            body += f"：「{quote}」"
        return _RecallHit(timestamp_ms=timestamp_ms, text=body)

    async def _commitment_hits(self, context: _RecallContext) -> List[_RecallHit]:
        """不针对具体观众的承诺；分页读全后按关键词过滤，未了结与已了结都给。"""
        matched: List[Any] = []
        offset = 0
        while True:
            page = await self._commitments_repo.list_commitments(
                statuses=["open", "closed"], limit=_COMMITMENT_PAGE_SIZE, offset=offset
            )
            if not page:
                break
            for row in page:
                # 对某位观众的承诺不在这里给：原话可能夹着 ta 的私事
                if row.viewer_id is not None:
                    continue
                haystack = "\n".join([row.what or "", row.quote or "", row.close_quote or ""])
                if _matches_all_keywords(haystack, context.keywords):
                    matched.append(row)
            offset += len(page)
            if len(page) < _COMMITMENT_PAGE_SIZE:
                break
        matched.sort(key=lambda row: (int(row.created_at_ms), int(row.id)), reverse=context.newest_first)
        return [self._commitment_hit(row, context) for row in matched[:MAX_RESULTS]]

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
