"""回想工具 ``memory_recall``：按关键词把她记得的事找回来。

旧的记忆工具按固定键取数（往前第几场的回顾、某位观众的印象、本场备忘录），要求主播
先知道该问哪一类；观众的问题通常只给得出一个词。本工具按关键词查多个来源，把内容连同
"什么时候、谁说的"一起摆出来：

- 她已播出的台词（``live_chat`` 的 assistant 行；``dropped_at_ms`` 非空的不算说过，同场前后 30 秒紧邻台词自动连成一段）
- 场次档案行（``archive_lines`` 的标题与原话都参与匹配）
- 不针对具体观众的承诺（``commitments``；未了结与已了结都给，判误记的 ``void`` 不给）
- 游戏里做到哪一步（``game_events`` 里的里程碑与阶段汇报；游戏聊天、肢体动作、安全阀不收）
- 本场观众弹幕（``live_chat`` 的 viewer 行；不论 scope 都只查本场）
- 游戏社交事实（``game_social_facts`` 的现行行；接了社交事实仓储才查）
- 当前存档工作笔记（``game_work_docs`` 里眼下所在存档的笔记与未完待办；别的存档绝不串入）

匹配口径：台词、档案、承诺、游戏进展与社交事实走混合检索（``search``：关键词按两字切分剥离虚词 + BM25，
配置了嵌入服务再加高于余弦底线的语义一路，按名次融合并对同分档做微幅新近度偏置），按相关度取最相关的 8 条，
不要求每个词都出现；同一句台词既进了台词索引、又作为档案行或公开承诺的原话证据被同时检索到时，在更靠前的名次上
保留带标题、完成状态或存档注记的档案行/承诺，跳过重复的裸台词；同场前后紧邻台词已被完整拼进正文时也不再重复占坑；
跨场或同场反复念叨的近似原样复读台词单次最多保留 2 条；若整次查询没有任何强相关命中、只剩字面弱擦边，最多给 2 条
并在抬头下提醒拿不准就直说记不清。本场观众弹幕不进任何持久索引，照旧在本场里按"每个词都包含"查，补剩下的位置。

不按名字查人：本场以外的结果里出现的已知观众昵称（``viewers`` 当前名与
``viewer_names`` 曾用名，2 字以上）一律替换成"某位观众"——跨场结果留下昵称等于把
ta 的发言和身份带到下一场；本场结果不替换。关键词恰好是某个已知昵称时直接丢掉该词，
并在抬头说明，因为主播会拿观众名字当关键词试探"你还记得 ta 吗"。

分存档的游戏：档案行、承诺与游戏进展挂了游戏的，逐条注明是在哪个存档里（是不是眼下这个）；她过去
说过的话，那场只在别的存档里玩过时注明——翻出"屋顶盖好了"时看得出那是别的世界里的事
（判定见 ``saves``）。

"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.storage.privacy import MIN_MASK_NAME_LEN, mask_names_in_text
from src.modules.storage.repos.memory_index import (
    INDEXED_GAME_EVENT_TYPES,
    SPEECH_NEIGHBOR_WINDOW_MS,
    IndexSource,
)
from src.modules.storage.repos.viewers import is_placeholder_name
from src.modules.tools.models import ToolInvocation, ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs
from src.modules.types.game_save import GameSave
from src.modules.utils.time_utils import now_ms

from .render import source_notes
from .saves import place_note, session_note
from .search import MemorySearch, SearchHit
from .textmatch import bigram_tokens, content_tokens, is_stopword_token, normalize, recall_query_tokens

__all__ = [
    "LITERAL_ONLY_NOTICE",
    "MAX_NEAR_DUPLICATE_SPEECH",
    "MAX_RESULTS",
    "MEMORY_RECALL_PROVIDER_NAME",
    "MEMORY_RECALL_SPEC",
    "MEMORY_RECALL_TOOL_NAME",
    "NEAR_DUPLICATE_JACCARD",
    "ORDER_NEWEST",
    "ORDER_OLDEST",
    "ORDER_RELEVANT",
    "RECALL_MIN_COSINE",
    "RecallEntry",
    "RecallOutcome",
    "SCOPE_ALL",
    "SCOPE_THIS_STREAM",
    "SPEECH_SNIPPET_MAX_CHARS",
    "WEAK_MATCH_MAX_RESULTS",
    "WEAK_MATCH_NOTICE",
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
# 混合检索多取一些候选：对某位观众的承诺会被筛掉，台词与引用它的档案行/承诺及同场邻句也会合并去重，留足余量才够选满 8 条
_SEARCH_POOL = 32
# 回想工具是她主动调的，查询向量没算好时可以等一小会儿（被动想起不等）
RECALL_VECTOR_WAIT_MS = 1_500
# 回想语义路余弦底线：低于该阈值的低相关向量不参与 RRF 融合投票，避免全库只有 0.25 余弦时仍凑满候选
RECALL_MIN_COSINE = 0.50
# 整次查询仅剩字面弱擦边时至多保留的条数与平实提醒
WEAK_MATCH_MAX_RESULTS = 2
WEAK_MATCH_NOTICE = "（未查到直接吻合的记录，以下仅字面擦边，拿不准就直接说记不清了）"
# 单条台词连同前后 30 秒紧邻台词拼合后的总字数上限（优先完整保留核心命中句）
SPEECH_SNIPPET_MAX_CHARS = 140
# 邻句剩余空间不足以完整放下时，至少还要有这么多字才做截断补充，免得只塞进两三个碎字
_NEIGHBOR_TRUNCATE_MIN_CHARS = 12
# 跨场/同场高度近似复读的同一簇台词单次最多保留几条（留了"不止说了一次"的印象，又不霸占全部 8 个名额）
MAX_NEAR_DUPLICATE_SPEECH = 2
NEAR_DUPLICATE_JACCARD = 0.85
NEAR_DUPLICATE_MIN_TOKENS = 3
# 混合检索也有命中时，当前存档工作笔记优先占的前排配额（混合检索不足 8 条时仍可继续用剩余工作笔记补齐）
_WORK_DOC_PRIMARY_QUOTA = 2

# 走混合检索的来源：精炼账本（档案、承诺）排在裸台词前，平分时优先给出带标题与存档注记的行
_RECALL_SOURCES: Tuple[IndexSource, ...] = ("archive", "commitment", "speech")
_RECALL_SOURCES_WITH_EVENTS: Tuple[IndexSource, ...] = (*_RECALL_SOURCES, "event")
# 社交事实六类在回想结果里的叫法
_SOCIAL_FACT_LABELS = {
    "setting": "环境形态",
    "members": "成员关系",
    "sharing": "共享惯例",
    "works": "进行中的事",
    "her_role": "她的角色",
    "open_question": "待弄清",
}
# 游戏进展在结果里怎么称呼（只收这两类，见索引仓储的白名单）
_EVENT_LABELS = {"milestone": "里程碑", "report": "阶段汇报"}
# 已完结的工作待办状态：只把尚未做完的待办当当前存档线索返回
_DONE_TODO_STATUSES = frozenset({"completed", "done", "cancelled", "canceled"})
# Markdown 标题前缀与列表前缀清理正则（拆解当前存档工作笔记行用）
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
_BRACKET_TITLE_RE = re.compile(r"^【([^】]{1,24})】\s*$")
_BULLET_PREFIX_RE = re.compile(r"^[-*•+]\s+|^\d+[.)、]\s+")
# 仅表示时间跨度或频次的泛化疑问外壳词：保留在 FTS 查询中参与排序，但不单独算具体主题实词
_GENERIC_QUERY_TERMS: frozenset[str] = frozenset(
    (
        "time times long old often days day weeks week months month years year total "
        "started start current currently new past ago different recent recently used using "
        "get got take took make made 时候 多久 几次 多少 几天 几个 最近 开始 一共"
    ).split()
)
_CJK_TOKEN_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")

SCOPE_ALL = "all"
SCOPE_THIS_STREAM = "this_stream"
ORDER_RELEVANT = "relevant"
ORDER_NEWEST = "newest"
ORDER_OLDEST = "oldest"

# 语义一路这一次没用上时写在抬头下面的提醒
LITERAL_ONLY_NOTICE = "（语义检索暂不可用，这次只按字面查，换个说法也许查得到）"

# 跨场结果里观众昵称统一换成这个代称（本场结果不替换）
CROSS_SESSION_MASK_LABEL = "某位观众"

MEMORY_RECALL_SPEC = ToolSpec(
    name="recall",
    description=(
        "回想过去的事：你说过的话、每场的档案、答应过的事、游戏里做到哪一步、当前存档工作笔记、本场弹幕，"
        "按关键词找出最相关的几条（几个关键词用空格分开，不要求每个词都出现，越相关越靠前选）。"
        "问「上次那个事」「之前提到过什么」「我答应过什么」时用。不按观众名字查人——不知道某位观众是谁就问她本人。"
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
                "enum": [ORDER_RELEVANT, ORDER_NEWEST, ORDER_OLDEST],
                "default": ORDER_RELEVANT,
                "description": "选出的结果怎么排：relevant 最相关的在前（默认）；newest 最近的在前；oldest 最早的在前",
            },
        },
        "required": ["query"],
    },
    kind="sync",
    result_event="",
    provider=MEMORY_RECALL_PROVIDER_NAME,
)


@dataclass(frozen=True)
class RecallEntry:
    """回想工具最终选出的一条结果：来源类别、来源行主键、排序用的时刻、渲染文本及已承载的原话主键。"""

    source: str
    source_id: int
    timestamp_ms: int
    text: str
    covered_chat_ids: Tuple[int, ...] = ()


@dataclass(frozen=True)
class RecallOutcome:
    """一次回想的完整产出：给模型看的整段文本、最终入选的条目清单，以及语义一路是否参与。"""

    text: str
    entries: Tuple[RecallEntry, ...]
    vector_used: bool


@dataclass(frozen=True)
class _RecallContext:
    """一次回想的查询上下文：关键词、排序方向、本场与上一场、遮名用的昵称表。"""

    keywords: Sequence[str]
    scope: str
    # 本场弹幕按这个方向取（相关在前时取最近的）
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
    """order 归一：认 newest / oldest，其余（含空值与未知值）按 relevant 处理。"""
    value = (order or "").strip().lower()
    if value in (ORDER_NEWEST, ORDER_OLDEST):
        return value
    if value and value != ORDER_RELEVANT:
        logger.debug(f"memory_recall 收到未知 order={order!r}，按 {ORDER_RELEVANT} 处理")
    return ORDER_RELEVANT


@dataclass(frozen=True)
class _CandidateRecord:
    """混合检索还原后的一条待筛候选：条目本身、原始检索命中信息与参与匹配核对的原话文本。"""

    entry: RecallEntry
    search_hit: SearchHit
    raw_text: str


@dataclass(frozen=True)
class _IndexedOutcome:
    """混合检索这一次的结果：还原好的几条、各路命中数日志说明、语义路开关与是否仅剩字面弱擦边。"""

    hits: List[RecallEntry]
    detail: str
    vector_used: bool
    weak_only: bool = False


def _extract_work_doc_segments(notebook: str, todos: Sequence[Mapping[str, Any]]) -> List[str]:
    """把存档工作文档拆成语义自足的短候选行（非空笔记行带上所属小节名 + 未完成的待办项）。"""
    segments: List[str] = []
    section = ""
    for raw_line in (notebook or "").splitlines():
        stripped = raw_line.strip()
        if not stripped or set(stripped) <= {"-", "=", "*", "_"}:
            continue
        if m := _HEADING_RE.match(stripped):
            section = m.group(2).strip()
        elif m := _BRACKET_TITLE_RE.match(stripped):
            section = m.group(1).strip()
        elif cleaned := _BULLET_PREFIX_RE.sub("", stripped).strip():
            has_sec = not section or cleaned.startswith(f"【{section}】") or section in cleaned[: len(section) + 4]
            segments.append(cleaned if has_sec else f"【{section}】{cleaned}")
    for item in todos or ():
        if isinstance(item, Mapping):
            status = str(item.get("status", "pending") or "pending").strip().lower()
            content = str(item.get("content", "") or "").strip()
            if status not in _DONE_TODO_STATUSES and content:
                segments.append(f"{'进行中待办' if status == 'in_progress' else '待办'}：{content}")
    return segments


def _compose_speech_window(
    core_text: str,
    core_id: int,
    prev_pair: Optional[Tuple[int, str]],
    next_pair: Optional[Tuple[int, str]],
) -> Tuple[str, Tuple[int, ...]]:
    """按 140 字预算拼合同场前后紧邻主播台词，返回（拼合文本, 实际完整覆盖的台词主键元组）。"""
    if len(core_text) >= SPEECH_SNIPPET_MAX_CHARS:
        return core_text, (core_id,)
    prefix, suffix, used = "", "", len(core_text)
    covered_before: List[int] = []
    covered_after: List[int] = []
    if prev_pair and prev_pair[1]:
        prev_id, prev_text = prev_pair
        if used + len(prev_text) + 1 <= SPEECH_SNIPPET_MAX_CHARS:
            prefix, used = f"{prev_text} ", used + len(prev_text) + 1
            covered_before.append(prev_id)
        elif (avail := SPEECH_SNIPPET_MAX_CHARS - used - 2) >= _NEIGHBOR_TRUNCATE_MIN_CHARS:
            prefix, used = f"…{prev_text[-avail:]} ", used + avail + 2
    if next_pair and next_pair[1]:
        next_id, next_text = next_pair
        if used + len(next_text) + 1 <= SPEECH_SNIPPET_MAX_CHARS:
            suffix = f" {next_text}"
            covered_after.append(next_id)
        elif (avail := SPEECH_SNIPPET_MAX_CHARS - used - 2) >= _NEIGHBOR_TRUNCATE_MIN_CHARS:
            suffix = f" {next_text[:avail]}…"
    return f"{prefix}{core_text}{suffix}", tuple([*covered_before, core_id, *covered_after])


def _jaccard_similarity(left: Set[str], right: Set[str]) -> float:
    return len(left & right) / len(left | right) if (left and right) else 0.0


class MemoryRecallService:
    """``memory_recall`` 的业务实现：查各路记忆来源、合并去重排序、渲染成给模型看的一段文本。

    构造参数全由宿主注入：三个基础仓储（台词与弹幕、承诺、档案）、混合检索（没有时台词、档案、
    承诺、游戏进展都查不到，只剩当前存档工作笔记与本场弹幕）、可选的隐私服务与观众仓储（跨场遮名、
    昵称关键词识别）、进行中场次回调（决定"本场"是第几场）与毫秒时钟（耗时日志）。
    可选的游戏事件仓储还原游戏进展并给台词注明那场的存档，可选的工作文档仓储只读查询眼下所在存档的
    工作笔记与未完待办。仓储都按鸭子类型取用，只依赖它们各自读面上的方法。
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
        social_fact_repo: Any = None,
        work_docs_repo: Any = None,
    ) -> None:
        self._chat_repo = chat_repo
        self._social_fact_repo = social_fact_repo
        self._work_docs_repo = work_docs_repo
        self._search = search
        self._commitments_repo = commitments_repo
        self._archive_repo = archive_repo
        self._privacy_service = privacy_service
        self._viewer_repo = viewer_repo
        self._current_session_provider = current_session_provider
        self._clock = clock if clock is not None else now_ms
        self._event_repo = event_repo
        self._saves_provider = saves_provider

    async def recall(self, query: str, scope: str = SCOPE_ALL, order: str = ORDER_RELEVANT) -> str:
        """执行一次回想，返回给模型看的文本；查询异常不抛，转成「没查到（原因）」。"""
        return (await self.recall_detailed(query, scope=scope, order=order)).text

    async def recall_detailed(self, query: str, scope: str = SCOPE_ALL, order: str = ORDER_RELEVANT) -> RecallOutcome:
        """执行一次回想，同时返回渲染文本、最终入选条目与语义路开关（供离线评测与诊断直接核对）。"""
        scope_value = _normalize_scope(scope)
        order_value = _normalize_order(order)
        started_ms = int(self._clock())
        try:
            outcome, detail = await self._recall(query, scope_value, order_value)
        except Exception as exc:  # noqa: BLE001 - 工具边界：查询失败转成给模型的说明，不反噬决策轮
            logger.error(
                f"memory_recall 查询失败（query={query!r}, scope={scope_value}, order={order_value}）:"
                f" {type(exc).__name__}: {exc}",
                exc=True,
            )
            return RecallOutcome(
                text=f"没查到（{str(exc).strip() or type(exc).__name__}）",
                entries=(),
                vector_used=False,
            )
        logger.info(
            f"memory_recall 完成：query={query!r} scope={scope_value} order={order_value}"
            f"{' ' + detail if detail else ''} 耗时 {int(self._clock()) - started_ms}ms"
        )
        return outcome

    async def _recall(self, query: str, scope: str, order: str) -> Tuple[RecallOutcome, str]:
        """查询主体：解析关键词 → 查各路来源 → 取前 8 条排好 → 渲染抬头与正文；另返回给日志的命中数。"""
        newest_first = order != ORDER_OLDEST
        raw_query = (query or "").strip()
        keywords = _split_keywords(raw_query)
        if not keywords:
            return (
                RecallOutcome(
                    text="（想查什么？给一个或多个关键词，用空格分开，比如「挖矿 钻石」）",
                    entries=(),
                    vector_used=False,
                ),
                "",
            )

        current_session_id = self._current_session()
        if scope == SCOPE_THIS_STREAM and current_session_id is None:
            return (
                RecallOutcome(
                    text=(
                        f"{self._header(raw_query, scope, order, 0)}\n"
                        "（现在不在直播，没有「本场」可查；scope 用 all 可以查全部历史）"
                    ),
                    entries=(),
                    vector_used=False,
                ),
                "",
            )

        names = await self._load_known_names()
        known = set(names)
        ignored = [keyword for keyword in keywords if keyword in known]
        effective = [keyword for keyword in keywords if keyword not in known]
        notice = f"（回想工具不按观众名字查，已忽略 {len(ignored)} 个名字）" if ignored else ""
        if not effective:
            return (
                RecallOutcome(
                    text="\n".join(
                        line
                        for line in (
                            self._header(raw_query, scope, order, 0),
                            notice,
                            "（没有可用的关键词了，换成跟事情有关的关键词再试试）",
                        )
                        if line
                    ),
                    entries=(),
                    vector_used=False,
                ),
                "",
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

        effective_query = " ".join(effective)
        indexed = await self._indexed_hits(effective_query, context, archive_bounds)
        work_docs = await self._work_doc_hits(effective_query, context)

        # 合并当前存档工作笔记与混合检索结果：两者都有时工作笔记先给前排核心项，其余给历史记忆，空余再补齐，严守 8 条上限
        hits: List[RecallEntry] = []
        if work_docs and indexed.hits and not indexed.weak_only:
            primary_docs = work_docs[:_WORK_DOC_PRIMARY_QUOTA]
            extra_docs = work_docs[_WORK_DOC_PRIMARY_QUOTA:]
            hits.extend(primary_docs)
            hits.extend(indexed.hits[: MAX_RESULTS - len(hits)])
            if len(hits) < MAX_RESULTS and extra_docs:
                hits.extend(extra_docs[: MAX_RESULTS - len(hits)])
        elif work_docs:
            hits.extend(work_docs[:MAX_RESULTS])
            if len(hits) < MAX_RESULTS and indexed.hits:
                hits.extend(indexed.hits[: MAX_RESULTS - len(hits)])
        else:
            cap = WEAK_MATCH_MAX_RESULTS if indexed.weak_only else MAX_RESULTS
            hits.extend(indexed.hits[:cap])

        danmaku_budget = (
            max(0, WEAK_MATCH_MAX_RESULTS - len(hits))
            if (indexed.weak_only and not work_docs)
            else max(0, MAX_RESULTS - len(hits))
        )
        raw_danmaku = await self._danmaku_hits(context)
        if raw_danmaku:
            # 本场弹幕按每个有效关键词都包含命中时，属于直接字面命中，可补满正常剩余名额
            danmaku = raw_danmaku[: max(0, MAX_RESULTS - len(hits))]
        else:
            danmaku = raw_danmaku[:danmaku_budget]
        hits.extend(danmaku)

        show_weak_notice = bool(hits) and indexed.weak_only and not work_docs and not danmaku
        if order != ORDER_RELEVANT:
            hits.sort(key=lambda hit: hit.timestamp_ms, reverse=newest_first)

        lines = [self._header(raw_query, scope, order, len(hits))]
        if notice:
            lines.append(notice)
        if self._search is not None and not indexed.vector_used:
            lines.append(LITERAL_ONLY_NOTICE)
        if show_weak_notice:
            lines.append(WEAK_MATCH_NOTICE)
        if not hits:
            lines.append("（没找到相关的记忆，换个关键词再试试）")
        else:
            lines.extend(hit.text for hit in hits)
        outcome = RecallOutcome(text="\n".join(lines), entries=tuple(hits), vector_used=indexed.vector_used)
        work_detail = f"、工作笔记 {len(work_docs)}" if work_docs else ""
        return outcome, f"{indexed.detail}{work_detail}、弹幕 {len(danmaku)}，给出 {len(hits)} 条"

    async def _indexed_hits(
        self, query: str, context: _RecallContext, archive_bounds: Sequence[Tuple[int, int]]
    ) -> _IndexedOutcome:
        """台词、档案行、承诺、游戏进展与社交事实：按混合检索相关度取回、合并去重并识别纯字面弱擦边。"""
        if self._search is None:
            return _IndexedOutcome(hits=[], detail="没接混合检索", vector_used=False, weak_only=False)
        sources = _RECALL_SOURCES_WITH_EVENTS if self._event_repo is not None else _RECALL_SOURCES
        if self._social_fact_repo is not None:
            sources = (*sources, "social_fact")
        result = await self._search.search_detailed(
            query,
            sources=sources,
            live_session_id=context.current_session_id if context.scope == SCOPE_THIS_STREAM else None,
            limit=_SEARCH_POOL,
            wait_vector_ms=RECALL_VECTOR_WAIT_MS,
            min_cosine=RECALL_MIN_COSINE,
        )
        found = result.hits
        ids: Dict[str, List[int]] = {source: [] for source in sources}
        for hit in found:
            ids[hit.source].append(hit.source_id)
        speech = {int(row.id): row for row in await self._chat_repo.list_speech_by_ids(chat_ids=ids["speech"])}
        neighbors = await self._load_speech_neighbors(list(speech.keys()))
        lines = {int(line.id): line for line in await self._archive_repo.list_lines_by_ids(line_ids=ids["archive"])}
        commitments: Dict[int, Any] = {}
        for commitment_id in ids["commitment"]:
            row = await self._commitments_repo.get_commitment(commitment_id=commitment_id)
            # 对某位观众、某位玩家的承诺不经回想翻给全场
            if row is not None and not row.has_target and row.status in ("open", "closed"):
                commitments[commitment_id] = row
        # 档案行与承诺关联的证据台词若不在 speech 候选里，也读出供实词核对（防止证据原话含查询词而摘要标题简写漏判）
        ref_ids = (
            *(line.evidence_chat_id for line in lines.values()),
            *(row.evidence_chat_id for row in commitments.values()),
            *(row.close_chat_id for row in commitments.values()),
        )
        extra_ids = [int(r) for r in ref_ids if r is not None and int(r) not in speech]
        extra_rows = await self._chat_repo.list_speech_by_ids(chat_ids=extra_ids) if extra_ids else ()
        evidence_speech = {**speech, **{int(r.id): r for r in extra_rows}}

        events = await self._events_by_ids(ids.get("event", []))
        facts = await self._facts_by_ids(ids.get("social_fact", []))
        notes = await self._session_notes([row.live_session_id for row in speech.values()], context)
        candidates = self._merge_indexed_hits(
            query,
            found,
            speech,
            neighbors,
            evidence_speech,
            lines,
            commitments,
            events,
            facts,
            notes,
            dict(archive_bounds),
            context,
        )
        filtered_hits, weak_only = self._prune_literal_noise(
            query, candidates, used_stopword_fallback=result.used_stopword_fallback
        )
        return _IndexedOutcome(
            hits=filtered_hits,
            detail=_path_counts(found, result.vector_used),
            vector_used=result.vector_used,
            weak_only=weak_only,
        )

    async def _load_speech_neighbors(self, chat_ids: Sequence[int]) -> Dict[int, Tuple[Any, Any]]:
        """批量读取命中台词同场前后 30 秒紧邻台词；测试替身未实现该方法时降级为空字典。"""
        if not chat_ids:
            return {}
        loader = getattr(self._chat_repo, "list_speech_neighbors", None)
        if not callable(loader):
            return {}
        return dict(await loader(chat_ids=chat_ids, window_ms=SPEECH_NEIGHBOR_WINDOW_MS))

    def _merge_indexed_hits(
        self,
        query: str,
        found: Sequence[SearchHit],
        speech: Mapping[int, Any],
        neighbors: Mapping[int, Tuple[Any, Any]],
        evidence_speech: Mapping[int, Any],
        lines: Mapping[int, Any],
        commitments: Mapping[int, Any],
        events: Mapping[int, Any],
        facts: Mapping[int, Any],
        notes: Mapping[int, str],
        last_ms: Mapping[int, int],
        context: _RecallContext,
    ) -> List[_CandidateRecord]:
        """按融合相关度顺序组装结果，消除账本同源重复、邻句滑窗重复与跨场近似复读霸屏。"""
        archive_by_chat: Dict[int, List[int]] = {}
        commitment_by_chat: Dict[int, List[int]] = {}
        for hit in found:
            if hit.source == "archive" and hit.source_id in lines:
                line = lines[hit.source_id]
                if line.evidence_chat_id is not None:
                    archive_by_chat.setdefault(int(line.evidence_chat_id), []).append(int(hit.source_id))
            elif hit.source == "commitment" and hit.source_id in commitments:
                row = commitments[hit.source_id]
                for ref_id in (row.evidence_chat_id, row.close_chat_id):
                    if ref_id is not None:
                        bucket = commitment_by_chat.setdefault(int(ref_id), [])
                        if int(hit.source_id) not in bucket:
                            bucket.append(int(hit.source_id))

        query_terms = set(content_tokens(query)) or set(recall_query_tokens(query))
        ledger_chat_ids: Set[int] = set(archive_by_chat) | set(commitment_by_chat)
        emitted_archives: Set[int] = set()
        emitted_commitments: Set[int] = set()
        covered_chats: Set[int] = set()
        # 近似复读台词簇：[(实词二元组集合, 已入选条数)]
        speech_clusters: List[List[Any]] = []
        records: List[_CandidateRecord] = []

        def _emit_archive(line_id: int, trigger_hit: SearchHit) -> None:
            if line_id in emitted_archives:
                return
            emitted_archives.add(line_id)
            line = lines[line_id]
            ev_text = ""
            if line.evidence_chat_id is not None:
                ev_id = int(line.evidence_chat_id)
                covered_chats.add(ev_id)
                if ev_id in evidence_speech:
                    ev_text = evidence_speech[ev_id].content or ""
            session_id = int(line.live_session_id)
            entry = self._archive_hit(line, session_id, int(last_ms.get(session_id, 0)), context)
            records.append(
                _CandidateRecord(
                    entry=entry,
                    search_hit=trigger_hit,
                    raw_text=f"{line.text or ''}\n{line.quote or ''}\n{ev_text}",
                )
            )

        def _emit_commitment(commitment_id: int, trigger_hit: SearchHit) -> None:
            if commitment_id in emitted_commitments:
                return
            emitted_commitments.add(commitment_id)
            row_c = commitments[commitment_id]
            ev_parts: List[str] = []
            for ref_id in (row_c.evidence_chat_id, row_c.close_chat_id):
                if ref_id is not None:
                    rid = int(ref_id)
                    covered_chats.add(rid)
                    if rid in evidence_speech:
                        ev_parts.append(evidence_speech[rid].content or "")
            entry = self._commitment_hit(row_c, context)
            records.append(
                _CandidateRecord(
                    entry=entry,
                    search_hit=trigger_hit,
                    raw_text="\n".join([row_c.what or "", row_c.quote or "", row_c.close_quote or "", *ev_parts]),
                )
            )

        def _emit_linked_ledger(target_chat_id: int, trigger_hit: SearchHit) -> bool:
            linked_archives = archive_by_chat.get(target_chat_id, [])
            linked_commitments = commitment_by_chat.get(target_chat_id, [])
            if not linked_archives and not linked_commitments:
                return False
            for line_id in linked_archives:
                _emit_archive(line_id, trigger_hit)
            for commitment_id in linked_commitments:
                _emit_commitment(commitment_id, trigger_hit)
            covered_chats.add(target_chat_id)
            return True

        for hit in found:
            if hit.source == "speech" and hit.source_id in speech:
                chat_id = int(hit.source_id)
                if chat_id in covered_chats:
                    continue
                row = speech[chat_id]
                prev_msg, next_msg = neighbors.get(chat_id, (None, None))

                # 识别邻句滑窗纯借词影子：后句自身未提任何查询词、完全靠拼入的前邻句才上榜时，归还前句或跳过回声
                if hit.vector_rank is None and prev_msg is not None and query_terms:
                    prev_id = int(prev_msg.id)
                    curr_own = set(bigram_tokens(row.content or "")) & query_terms
                    prev_own = set(bigram_tokens(prev_msg.content or "")) & query_terms
                    if not curr_own and prev_own:
                        if prev_id in covered_chats or _emit_linked_ledger(prev_id, hit):
                            covered_chats.add(chat_id)
                            continue

                if _emit_linked_ledger(chat_id, hit):
                    continue

                if not self._accept_speech_near_duplicate(row.content or "", speech_clusters):
                    covered_chats.add(chat_id)
                    continue

                # 已被展示过、或本轮自有档案行/承诺单独承载的邻句不再重复拼进当前引号
                excluded_neighbors = covered_chats | ledger_chat_ids
                avail_prev = prev_msg if (prev_msg is not None and int(prev_msg.id) not in excluded_neighbors) else None
                avail_next = next_msg if (next_msg is not None and int(next_msg.id) not in excluded_neighbors) else None
                entry, raw_window = self._speech_hit(row, (avail_prev, avail_next), notes, context)
                covered_chats.update(entry.covered_chat_ids)
                records.append(_CandidateRecord(entry=entry, search_hit=hit, raw_text=raw_window))
            elif hit.source == "archive" and hit.source_id in lines:
                _emit_archive(int(hit.source_id), hit)
            elif hit.source == "commitment" and hit.source_id in commitments:
                _emit_commitment(int(hit.source_id), hit)
            elif hit.source == "event" and hit.source_id in events:
                ev_row = events[hit.source_id]
                entry = self._event_hit(ev_row, context)
                records.append(_CandidateRecord(entry=entry, search_hit=hit, raw_text=ev_row.message or ""))
            elif hit.source == "social_fact" and hit.source_id in facts:
                fact_row = facts[hit.source_id]
                entry = self._social_fact_hit(fact_row, context)
                records.append(
                    _CandidateRecord(
                        entry=entry,
                        search_hit=hit,
                        raw_text=f"{fact_row.text or ''}\n{fact_row.quote or ''}",
                    )
                )
        return records

    @staticmethod
    def _accept_speech_near_duplicate(content: str, clusters: List[List[Any]]) -> bool:
        """检查候选台词是否与更靠前的已选台词近似原样复读（Jaccard >= 0.85）；同一簇最多保留 2 条。"""
        tokens = set(content_tokens(content, strip_trailing_modals=True))
        if len(tokens) < NEAR_DUPLICATE_MIN_TOKENS:
            return True
        for cluster in clusters:
            cluster_tokens: Set[str] = cluster[0]
            count: int = cluster[1]
            if _jaccard_similarity(tokens, cluster_tokens) >= NEAR_DUPLICATE_JACCARD:
                if count >= MAX_NEAR_DUPLICATE_SPEECH:
                    return False
                cluster[1] = count + 1
                return True
        clusters.append([tokens, 1])
        return True

    @staticmethod
    def _prune_literal_noise(
        query: str,
        candidates: Sequence[_CandidateRecord],
        *,
        used_stopword_fallback: bool,
    ) -> Tuple[List[RecallEntry], bool]:
        """裁剪纯字面路径下零实词低分凑数尾行，并判定整次查询是否无任何强相关命中（仅剩字面弱擦边）。"""
        if not candidates:
            return [], False
        has_semantic_or_like = any(
            item.search_hit.vector_rank is not None or item.search_hit.like_rank is not None for item in candidates
        )
        real_terms = set(content_tokens(query))
        specific_terms = real_terms - _GENERIC_QUERY_TERMS
        cjk_specific_terms = {term for term in specific_terms if _CJK_TOKEN_RE.search(term)}

        doc_tokens_list = [set(bigram_tokens(item.raw_text)) for item in candidates]
        real_overlaps = [len(tokens & real_terms) for tokens in doc_tokens_list]
        specific_overlaps = [len(tokens & specific_terms) for tokens in doc_tokens_list]
        cjk_overlaps = [len(tokens & cjk_specific_terms) for tokens in doc_tokens_list]

        top_kw_score = max(
            (item.search_hit.keyword_score or 0.0 for item in candidates if item.search_hit.keyword_score is not None),
            default=0.0,
        )
        max_real = max(real_overlaps, default=0)
        max_spec = max(specific_overlaps, default=0)

        # 首条已有明确实词命中时，剪掉尾部连 1 个实词都没撞上、BM25 远低于首条的纯代词/虚词凑数行
        kept_indices = list(range(len(candidates)))
        if real_terms and (max_spec >= 2 or (max_real >= 1 and top_kw_score >= 2.5)):
            kept_indices = [
                idx
                for idx in kept_indices
                if candidates[idx].search_hit.vector_rank is not None
                or candidates[idx].search_hit.like_rank is not None
                or real_overlaps[idx] > 0
                or (candidates[idx].search_hit.keyword_score or 0.0) >= max(0.5, top_kw_score * 0.25)
            ]

        kept_entries = [candidates[idx].entry for idx in kept_indices]
        if has_semantic_or_like or not kept_entries:
            return kept_entries, False

        max_spec_kept = max((specific_overlaps[idx] for idx in kept_indices), default=0)
        max_real_kept = max((real_overlaps[idx] for idx in kept_indices), default=0)
        max_cjk_kept = max((cjk_overlaps[idx] for idx in kept_indices), default=0)
        non_cjk_spec = specific_terms - cjk_specific_terms
        has_non_cjk_hit = any(bool(doc_tokens_list[idx] & non_cjk_spec) for idx in kept_indices)

        # 整次查询仅剩字面弱擦边：纯虚词回退、完全没命中任何具体主题实词、或多汉字复合词只擦到孤立 1 个双字碎片
        weak_only = (
            used_stopword_fallback
            or not real_terms
            or (max_real_kept == 0 and top_kw_score < 4.0)
            or (bool(specific_terms) and max_spec_kept == 0 and top_kw_score < 8.0)
            or (len(cjk_specific_terms) >= 4 and max_cjk_kept <= 1 and not has_non_cjk_hit)
        )
        return kept_entries, weak_only

    async def _work_doc_hits(self, query: str, context: _RecallContext) -> List[RecallEntry]:
        """只读检索眼下所在存档的 ``game_work_docs``（工作笔记行与未完成待办），别的存档绝不串入。"""
        if self._work_docs_repo is None or not context.current_saves:
            return []
        norm_keywords = [normalize(kw) for kw in context.keywords if normalize(kw) and not is_stopword_token(kw)]
        if not norm_keywords:
            norm_keywords = [normalize(kw) for kw in context.keywords if normalize(kw)]
        query_terms = set(content_tokens(query)) or set(recall_query_tokens(query))

        scored: List[Tuple[int, int, int, RecallEntry]] = []
        seq = 0
        for game, save in sorted(context.current_saves.items()):
            if save is None or not save.known:
                continue
            try:
                record = await self._work_docs_repo.get_work_docs(game=game, save_id=save.id)
            except Exception as exc:  # noqa: BLE001 - 工作文档读取异常只跳过这一路，不打断回想主流程
                logger.warning(f"回想工具读取 {game} 存档 {save.id!r} 工作文档失败: {exc}", exc=True)
                continue
            if record is None:
                continue
            if context.scope == SCOPE_THIS_STREAM and record.updated_session_id != context.current_session_id:
                continue

            save_label = save.label if save.name else GameSave(id=save.id, name=record.save_name).label
            segments = _extract_work_doc_segments(record.notebook, record.todos)
            for segment in segments:
                norm_seg = normalize(segment)
                if not norm_seg:
                    continue
                seg_tokens = set(bigram_tokens(segment))
                kw_hits = sum(1 for kw in norm_keywords if kw and kw in norm_seg)
                tok_hits = len(seg_tokens & query_terms)
                matched = (
                    kw_hits >= 1
                    or (len(query_terms) == 1 and tok_hits == 1)
                    or (len(query_terms) >= 2 and tok_hits >= 2)
                )
                if not matched:
                    continue
                seq += 1
                # 工作笔记跨场持续保留，一律按跨场口径遮掉已知观众昵称与本场遗忘名单
                masked = self._mask(segment, None, context)
                if len(masked) > SPEECH_SNIPPET_MAX_CHARS:
                    masked = f"{masked[: SPEECH_SNIPPET_MAX_CHARS - 1]}…"
                entry = RecallEntry(
                    source="work_doc",
                    source_id=seq,
                    timestamp_ms=int(record.updated_at_ms or 0),
                    text=f"- 当前存档工作笔记（{game}「{save_label}」）：{masked}",
                )
                scored.append((kw_hits, tok_hits, -seq, entry))

        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [item[3] for item in scored[:MAX_RESULTS]]

    async def _events_by_ids(self, event_ids: Sequence[int]) -> Dict[int, Any]:
        """按主键读回游戏事件，只留里程碑与阶段汇报（索引已按白名单收，这里再核一遍）。"""
        if self._event_repo is None or not event_ids:
            return {}
        rows = await self._event_repo.list_events_by_ids(event_ids=event_ids)
        return {int(row.id): row for row in rows if row.event_type in INDEXED_GAME_EVENT_TYPES}

    async def _facts_by_ids(self, fact_ids: Sequence[int]) -> Dict[int, Any]:
        """按主键读回社交事实，只留现行行（索引已按现行收，这里再核一遍）。"""
        if self._social_fact_repo is None or not fact_ids:
            return {}
        rows = await self._social_fact_repo.list_facts_by_ids(fact_ids=fact_ids)
        return {int(row.id): row for row in rows if row.status == "active"}

    def _social_fact_hit(self, row: Any, context: _RecallContext) -> RecallEntry:
        """一条社交事实渲染成「游戏里的人（类别）：事实」，挂了存档的注明是哪个存档。"""
        session_id = int(row.created_session_id) if row.created_session_id is not None else None
        label = _SOCIAL_FACT_LABELS.get(row.kind, row.kind)
        text = f"- 游戏里的人（{label}）：{self._mask((row.text or '').strip(), session_id, context)}"
        note = place_note(row.game, row.save_id, row.save_name, context.current_saves)
        return RecallEntry(
            source="social_fact",
            source_id=int(row.id),
            timestamp_ms=int(row.updated_at_ms),
            text=f"{text}（{note}）" if note else text,
        )

    def _event_hit(self, row: Any, context: _RecallContext) -> RecallEntry:
        """一条游戏进展渲染成「位置 + 游戏里（里程碑/阶段汇报）+ 正文」，挂了存档的注明是哪个存档。"""
        session_id = int(row.live_session_id)
        text = (
            f"- {self._when(session_id, row.timestamp_ms, context)} 游戏里（{_EVENT_LABELS[row.event_type]}）："
            f"{self._mask((row.message or '').strip(), session_id, context)}"
        )
        note = place_note(row.game, row.save_id, row.save_name, context.current_saves)
        return RecallEntry(
            source="event",
            source_id=int(row.id),
            timestamp_ms=int(row.timestamp_ms),
            text=f"{text}（{note}）" if note else text,
        )

    def _speech_hit(
        self,
        row: Any,
        neighbor_pair: Tuple[Any, Any],
        notes: Mapping[int, str],
        context: _RecallContext,
    ) -> Tuple[RecallEntry, str]:
        """一句台词连同同场前后 30 秒紧邻台词拼成连贯单条（140 字内保留核心句），并返回参与匹配核对的原话。"""
        session_id, core_id = row.live_session_id, int(row.id)
        prev_row, next_row = neighbor_pair

        def _to_pair(msg: Any) -> Optional[Tuple[int, str]]:
            return (int(msg.id), self._mask((msg.content or "").strip(), session_id, context)) if msg else None

        snippet, covered_ids = _compose_speech_window(
            self._mask((row.content or "").strip(), session_id, context),
            core_id,
            _to_pair(prev_row),
            _to_pair(next_row),
        )
        raw_parts = [
            prev_row.content if prev_row is not None and int(prev_row.id) in covered_ids else "",
            row.content or "",
            next_row.content if next_row is not None and int(next_row.id) in covered_ids else "",
        ]
        note = notes.get(session_id, "")
        text = f"- {self._when(session_id, row.timestamp_ms, context)} 你说：「{snippet}」"
        entry = RecallEntry(
            source="speech",
            source_id=core_id,
            timestamp_ms=int(row.timestamp_ms),
            text=f"{text}（{note}）" if note else text,
            covered_chat_ids=covered_ids,
        )
        return entry, "\n".join(part for part in raw_parts if part)

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

    async def _danmaku_hits(self, context: _RecallContext) -> List[RecallEntry]:
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
            RecallEntry(
                source="danmaku",
                source_id=int(row.id),
                timestamp_ms=int(row.timestamp_ms),
                text=(
                    f"- {self._when(current_session_id, row.timestamp_ms, context)} 观众说："
                    f"「{self._mask(row.content, current_session_id, context)}」"
                ),
            )
            for row in rows
        ]

    def _archive_hit(self, line: Any, session_id: int, timestamp_ms: int, context: _RecallContext) -> RecallEntry:
        """一行档案渲染成「位置 + 你说（档案「标题」）+ 她的原话」。"""
        when = self._when(session_id, timestamp_ms, context)
        title = self._mask((line.text or "").strip(), session_id, context)
        quote = self._mask((line.quote or "").strip(), session_id, context)
        body = f"- {when} 你说（档案「{title}」）"
        if quote:
            body += f"：「{quote}」"
        # 观众来源的行注明和观众一起定的（不记得是哪一位）；存档注记照旧
        notes = [*source_notes(line), place_note(line.game, line.save_id, line.save_name, context.current_saves)]
        if any(notes):
            body += f"（{'；'.join(item for item in notes if item)}）"
        covered = (int(line.evidence_chat_id),) if line.evidence_chat_id is not None else ()
        return RecallEntry(
            source="archive",
            source_id=int(line.id),
            timestamp_ms=timestamp_ms,
            text=body,
            covered_chat_ids=covered,
        )

    def _commitment_hit(self, row: Any, context: _RecallContext) -> RecallEntry:
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
        covered = tuple(
            int(ref_id) for ref_id in dict.fromkeys((row.evidence_chat_id, row.close_chat_id)) if ref_id is not None
        )
        return RecallEntry(
            source="commitment",
            source_id=int(row.id),
            timestamp_ms=timestamp_ms,
            text=body,
            covered_chat_ids=covered,
        )

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
        order_text = {ORDER_RELEVANT: "相关在前", ORDER_NEWEST: "最新在前"}.get(order, "最早在前")
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


def _path_counts(found: Sequence[SearchHit], vector_used: bool) -> str:
    """各路在候选里命中了几条（一条可以同时被几路找到），给完成日志看。"""
    keyword = sum(1 for hit in found if hit.keyword_rank is not None)
    like = sum(1 for hit in found if hit.like_rank is not None)
    vector = sum(1 for hit in found if hit.vector_rank is not None)
    vector_text = f"语义 {vector}" if vector_used else "语义没用上"
    return f"候选 {len(found)}（关键词 {keyword}、子串 {like}、{vector_text}）"


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
            order=str(args.get("order", ORDER_RELEVANT) or ORDER_RELEVANT),
        )

    return make_provider_from_specs(
        MEMORY_RECALL_PROVIDER_NAME,
        [(MEMORY_RECALL_SPEC, as_tool_impl(MEMORY_RECALL_SPEC.full_name, _recall))],
        category=MEMORY_TOOL_CATEGORY,
    )
