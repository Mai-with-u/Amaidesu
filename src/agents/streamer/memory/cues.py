"""线索推送：每批 Planner 调用前，按线索找出这一批该想起来的事。

线索按优先级取，每批至多两条线索：

1. 对象开口：本批发言人按外部 ID 精确解析出观众编号（只读，不建观众；有待执行删除请求
   的不算），名下有未了结承诺 → 推那张对 ta 的承诺线索，只给动作标题；本场未回应过的老观众
   → 推老观众线索；本回合在游戏公屏开口或冲她做动作、以前和她有过往来的玩家 → 推玩家线索。
2. 游戏地点（被动想起）：角色从远处走到某个记住的地点跟前 → 推挂在这个地点上的档案行；
3. 规则（被动想起）：本批观众弹幕或她最近 3 句台词提到了档案行的触发词或老梗 → 推那一条；
4. 向量（被动想起）：本批观众弹幕或游戏情境的意思和某行档案、某条不针对具体观众的承诺相近 → 推那一条。
   弹幕一到就在后台算查询向量（``prefetch``），这一批组装时算好了就用，没算好这一批不用，决策不等它；
   只发弹幕正文，不发昵称和外部 ID，发过"忘了我"的观众的弹幕不发。

游戏承诺不按"游戏开着"轮播，只在对象开口、走到地点或语义情境命中时单推。

地点、规则、向量三路怎么找候选见 ``passive``；被动想起可在配置里整体关掉，向量一路也可单独关掉
（没配嵌入服务时自动不走）。

限频与用上即停：同一条线索每场至多推两次、两次至少隔十分钟（计数在内存里，进程重启从零
算）；本场台词的记忆引用里已经出现这条线索的编号（约#N 或 档#N），就是被用上了，本场不再推
——这一条每批查一次库，进程重启也不丢。被动想起另有两条：记忆摘要里已经列着的条目不推；
只由她自己台词命中的（自触发）每条每场只推一次，免得她提一句、又推一条、她再接着说，
越说越多。每推一条线索记一行线索推送记录（只记条目编号和分数，不记观众身份），检测到她用上时
回填用上的时刻。

线索块里有三种输出：线索条目（承诺线索、老观众线索、玩家线索、往事线索、老梗线索）、老观众在场一行
（本批开口、本场之前来过至少三场正式场、这一批没有承诺线索、她本场还没回应过、没有待删
请求的观众合成一行），以及本批的遗忘回执。遗忘回执只认记账侧真正处理过的命令：弹幕里的
「忘了我」记账侧一定受理；撤回短语要记账侧确认那次撤回已经生效
（``PrivacyService.was_retracted_in_session``）才回执，超级留言/礼物里的文本一律不回执。
三者合成一个线索块交给参考段；没有线索时整块省略。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.storage.models.memory import NewCueLogEntry
from src.modules.storage.privacy import mask_names_in_text, player_forget_kind
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation
from src.modules.types.session_kind import SESSION_KIND_LIVE
from src.modules.utils.time_utils import now_ms

from .brief import load_used_refs
from .commitment_ttl import is_commitment_dormant
from .passive import (
    SOURCE_DANMAKU,
    SOURCE_PLACE,
    SOURCE_PLAYER,
    SOURCE_SELF,
    SOURCE_SITUATION,
    VECTOR_MAX_QUERIES,
    VECTOR_TOP,
    CueText,
    ItemKey,
    PassiveHit,
    PlaceArrivals,
    RulePool,
    SituationQuery,
    intent_content_tokens,
    query_worthy,
    score_intent_keyword_match,
    vector_hits,
    vector_top3_summary,
)
from .render import (
    render_archive_card,
    render_archive_number,
    render_commitment_number,
    render_cue_block,
    render_familiar_viewer_card,
    render_forget_notice,
    render_joke_card,
    render_player_card,
    render_presence_line,
    render_recalled_commitment_card,
    render_retract_notice,
    render_social_fact_card,
    render_viewer_card,
)
from .saves import SavePlace, place_note, place_of

if TYPE_CHECKING:
    from src.modules.storage.models.memory import ArchiveLine, Commitment
    from src.modules.storage.models.social_facts import SocialFact
    from src.modules.storage.privacy import PrivacyService
    from src.modules.storage.repos.memory_index import IndexSource

    from .search import MemorySearch
    from src.modules.storage.repos import (
        ArchiveRepo,
        ChatRepo,
        CommitmentRepo,
        GamePlayerRepo,
        MemoryCorrectionRepo,
        MemoryCueLogRepo,
        SocialFactRepo,
        ViewerRepo,
    )

__all__ = [
    "MAX_CARDS_PER_BATCH",
    "MAX_PUSHES_PER_CARD",
    "MAX_SELF_PUSHES_PER_CARD",
    "MIN_SESSIONS_FOR_PRESENCE",
    "MemoryCueProvider",
    "MemoryCues",
    "is_social_fact_safe_for_game_lead",
]

logger = get_logger("MemoryCueProvider")

# 一批最多推几条线索、同一条线索每场最多推几次（只由她自己台词触发的必须来自两场以前且每场只推一次）
MAX_CARDS_PER_BATCH = 2
MAX_PUSHES_PER_CARD = 2
MAX_SELF_PUSHES_PER_CARD = 1
# 同一条线索两次推送的最小间隔
PUSH_COOLDOWN_MS = 10 * 60 * 1000
# 老观众在场一行的门槛：本场之前来过至少三场正式场
MIN_SESSIONS_FOR_PRESENCE = 3
# 观众名下最多取几条未了结承诺（一位观众同时压着更多就不是这场该操心的事）
_MAX_COMMITMENTS_PER_VIEWER = 3
# 挂在一个游戏上的未了结承诺最多看几条：别的存档里答应的不推，要往后多看几条才找得到眼下这个存档的
_MAX_COMMITMENTS_PER_GAME = 20
# 玩家线索尾至多写几条答应过他的事
_MAX_PROMISES_PER_PLAYER = 2
# 玩家线索上至多写几条他说过、人批准过的自述
_MAX_STATEMENTS_PER_PLAYER = 2
# 老观众线索上至多写几条已批准近况、至多写几条共同造梗归属
_MAX_NOTES_PER_FAMILIAR_VIEWER = 2
_MAX_AUTHORED_PER_FAMILIAR_VIEWER = 2
# 规则一路看她最近几句播出的台词
RECENT_SPEECH_LINES = 3
# 游戏主控按意图联想允许放行的社交事实类别（仅限对行动有价值的四类，排除 her_role 与 open_question）
_GAME_LEAD_SOCIAL_FACT_KINDS: frozenset[str] = frozenset({"works", "sharing", "setting", "members"})
# 意图关键词兜底查询上限（社交事实池、单次 FTS 召回条数、无 FTS 兜底回扫场次数）
_INTENT_SOCIAL_FACT_LIMIT = 100
_INTENT_FTS_LIMIT = 8
_INTENT_RECENT_ARCHIVE_SESSIONS = 5
# 截断带省略号的排除规矩前缀最小长度与类别前缀长度上限（防止极短前缀误杀无关事实）
_EXCLUDED_TRUNCATED_PREFIX_MIN_CHARS = 12
_EXCLUDED_CATEGORY_PREFIX_MAX_CHARS = 12


def _compact_text(text: str) -> str:
    """去除所有空白字符，供排除列表与候选正文做等值或包含判重。"""
    return "".join(str(text or "").split())


def _strip_exclude_category_prefix(compact_item: str) -> str:
    """剥离排除项首段类别前缀（如「共享惯例：」「记着的事：」），便于对照截断后的长规矩。"""
    for sep in ("：", ":"):
        idx = compact_item.find(sep)
        if 0 < idx <= _EXCLUDED_CATEGORY_PREFIX_MAX_CHARS and idx + 1 < len(compact_item):
            return compact_item[idx + 1 :]
    return compact_item


def _is_excluded_text(text: str, excluded_compact: Sequence[str]) -> bool:
    """候选正文去空白后若与任一排除文本相等、被包含或命中其截断前缀，跳过不重复推送。"""
    compact = _compact_text(text)
    if not compact or not excluded_compact:
        return False
    for item in excluded_compact:
        if compact == item or compact in item:
            return True
        stripped = _strip_exclude_category_prefix(item)
        if compact == stripped or compact in stripped:
            return True
        # 常驻规矩超过长度上限时会被截断并附「…」或「...」，前缀足够长且吻合即视为已常驻
        for ellipsis in ("…", "..."):
            if stripped.endswith(ellipsis):
                prefix = stripped[: -len(ellipsis)]
                if len(prefix) >= _EXCLUDED_TRUNCATED_PREFIX_MIN_CHARS and compact.startswith(prefix):
                    return True
    return False


def is_social_fact_safe_for_game_lead(
    fact: "SocialFact",
    *,
    game: str,
    current_saves: Mapping[str, GameSave],
) -> bool:
    """判定一条游戏社交事实能否放行进游戏主控按意图联想通道（严格防弹幕投毒与存档隔离）。

    触发条件：游戏主控态拉取跨场被动线索并对社交事实候选做安全校验时调用；
    执行顺序：校验 active 状态 -> 校验同游戏与允许类别 -> 校验严格存档隔离 -> 校验防弹幕投毒依据。
    """
    normalized_game = (game or "").strip()
    if not normalized_game or fact.status != "active":
        return False
    if (fact.game or "").strip() != normalized_game:
        return False
    if fact.kind not in _GAME_LEAD_SOCIAL_FACT_KINDS:
        return False
    # 当前存档未知或未上报时 current_save_id 视为空串，仅放行整游戏通用事实（save_id == ""），
    # 严防任一带有明确 save_id 的他档事实跨档泄露
    now_save = current_saves.get(normalized_game)
    current_save_id = (now_save.id if now_save is not None and now_save.known else "").strip()
    fact_save_id = (fact.save_id or "").strip()
    if fact_save_id and fact_save_id != current_save_id:
        return False
    if not (fact.text or "").strip():
        return False
    # 运营者人工锁定的事实直接放行；未锁定事实必须具备非空引文且纯来自游戏公屏，绝不放行弹幕来源
    if int(fact.human_locked or 0) == 1:
        return True
    return bool((fact.quote or "").strip()) and fact.evidence_game_chat_id is not None and fact.evidence_chat_id is None


@dataclass(frozen=True)
class MemoryCues:
    """一批的线索结果：拼好的参考段文本 + 本批出现的承诺与档案行编号（供点名校验）+ 老观众弹幕熟悉度映射。"""

    text: str = ""
    commitment_ids: Tuple[int, ...] = ()
    card_count: int = 0
    # 线索编号 → 对方称呼（对某位观众的承诺线索才有；回复器档要拿它说"答应过谁"）
    labels: Dict[int, str] = field(default_factory=dict)
    archive_ids: Tuple[int, ...] = ()
    # 本批由观众弹幕或玩家开口推上来的档案行：本场提过的旧事有人提到了，再点名不退回
    asked_archive_ids: Tuple[int, ...] = ()
    # 本批非遗忘观众弹幕 message_id → 熟悉度档位（"close" / "regular"；"none" 不写入）
    familiarity_by_message_id: Mapping[str, str] = field(default_factory=dict)


@dataclass
class _Sender:
    """本批发言人解析出的观众信息（解析不到观众时 viewer_id 为空）。"""

    external_id: str = ""
    id_kind: str = "account"
    user_name: str = ""
    content: str = ""
    message_id: str = ""
    message_type: str = ""
    platform: str = ""
    viewer_id: Optional[int] = None
    primary_viewer_id: Optional[int] = None
    cluster_viewer_ids: Tuple[int, ...] = ()
    forget_requested_at_ms: Optional[int] = None
    # 外部 ID 在不记录名单里：发过"忘了我"（建没建过档都算），或观众行已按要求删掉
    forget_listed: bool = False
    sessions_count: int = 0
    earlier_sessions: Optional[int] = None
    last_session_id: Optional[int] = None
    prev_seen_ms: Optional[int] = None
    familiarity_tier: str = "none"
    former_names: Tuple[str, ...] = ()
    message_ids: List[str] = field(default_factory=list)


def _forgetting(sender: _Sender) -> bool:
    """发过"忘了我"的观众：观众行上有待删标记，或外部 ID 在不记录名单里。"""
    return sender.forget_requested_at_ms is not None or sender.forget_listed


@dataclass
class _Card:
    """这一批推出去的一条线索：正文、条目、类别，以及写进线索推送记录的来源与分数。"""

    key: ItemKey
    text: str
    kind: str
    sources: Tuple[str, ...] = ()
    detail: Dict[str, Any] = field(default_factory=dict)
    self_triggered: bool = False


@dataclass
class _Round:
    """一批线索的判定材料与结果：用过的、记忆摘要里列着的条目，眼下的存档，推了哪些、没推哪些。"""

    used: Set[int]
    used_archive: Set[int]
    brief_commitments: Set[int]
    brief_archive: Set[int]
    saves: Mapping[str, GameSave]
    previous_session_id: Optional[int]
    recent_session_ids: Tuple[int, ...] = ()
    earliest_recent_ms: int = 0
    self_trigger_cutoff_ms: Optional[int] = None
    commitment_progress: Dict[int, str] = field(default_factory=dict)
    cards: List[_Card] = field(default_factory=list)
    probes: List[NewCueLogEntry] = field(default_factory=list)
    carded_viewers: Set[int] = field(default_factory=set)
    skipped: List[str] = field(default_factory=list)
    max_cards: int = MAX_CARDS_PER_BATCH

    @property
    def full(self) -> bool:
        return len(self.cards) >= self.max_cards

    def ids(self, kind: str) -> Tuple[int, ...]:
        return tuple(int(card.key[1]) for card in self.cards if card.key[0] == kind)

    def asked_ids(self, kind: str) -> Tuple[int, ...]:
        """本批由观众弹幕或游戏玩家开口推上来的线索指向的条目：有人提到了，本场提过也可以再接。"""
        return tuple(
            int(card.key[1])
            for card in self.cards
            if card.key[0] == kind and {SOURCE_DANMAKU, SOURCE_PLAYER}.intersection(card.sources)
        )

    def commitment_ids(self) -> Tuple[int, ...]:
        """本批能点名的承诺编号：承诺线索的，加玩家线索尾写着的答应过他的事（线索上印着约#N，她点了要认）。"""
        ids = list(self.ids("commitment"))
        for card in self.cards:
            if card.kind == "player":
                ids.extend(int(item) for item in card.detail.get("commitment_ids", ()))
        return tuple(dict.fromkeys(ids))


def _label(key: ItemKey) -> str:
    """日志里认条目用的写法：约#7 / 档#41 / 事实#5 / 老观众#3 / 玩家#2 / 老梗「倒立洗头」。"""
    kind, value = key
    if kind == "commitment":
        return render_commitment_number(int(value))
    if kind == "archive":
        return render_archive_number(int(value))
    if kind == "social_fact":
        return f"事实#{int(value)}"
    if kind == "viewer_familiar":
        # 只记观众编号，不写昵称（日志与记忆线索记录同口径）
        return f"老观众#{int(value)}"
    if kind == "player":
        # 只记玩家编号，不写名字（日志与记忆线索记录同口径）
        return f"玩家#{int(value)}"
    return f"老梗「{value}」"


class MemoryCueProvider:
    """按批计算线索；推送计数、到过的地点与回执追踪只活在本场，换场时清空。"""

    def __init__(
        self,
        *,
        commitments_repo: "CommitmentRepo",
        chat_repo: "ChatRepo",
        viewer_repo: Optional["ViewerRepo"] = None,
        privacy_service: Optional["PrivacyService"] = None,
        current_session_provider: Optional[Callable[[], Optional[int]]] = None,
        previous_session_provider: Optional[Callable[[], Awaitable[Optional[int]]]] = None,
        clock: Callable[[], int] = now_ms,
        archive_repo: Optional["ArchiveRepo"] = None,
        cue_log_repo: Optional["MemoryCueLogRepo"] = None,
        inside_jokes: str = "",
        passive_enabled: bool = True,
        search: Optional["MemorySearch"] = None,
        vector_enabled: bool = True,
        game_player_repo: Optional["GamePlayerRepo"] = None,
        correction_repo: Optional["MemoryCorrectionRepo"] = None,
        social_fact_repo: Optional["SocialFactRepo"] = None,
        min_session_ms: int = 0,
    ) -> None:
        """仓储与时钟注入；隐私服务缺失时不出遗忘回执（看不见遗忘命令）。

        ``previous_session_provider`` 给"上一场"的判定（承诺线索与往事线索的时间词）：缺省时
        一律写"之前"，不冒险把三场前的事说成上一场。``archive_repo`` 缺失时被动想起没有往事线索；
        ``cue_log_repo`` 缺失时只记日志；``passive_enabled`` 为假时被动想起三路都不跑；``search`` 缺失、
        没配嵌入服务或 ``vector_enabled`` 为假时向量一路不跑。``game_player_repo`` 缺失时没有玩家线索；
        ``correction_repo`` 缺失时不附本场进展注记；``social_fact_repo`` 缺失时游戏主控意图联想
        优雅降级，不推社交事实卡。
        """
        self._commitments = commitments_repo
        self._chat = chat_repo
        self._viewers = viewer_repo
        self._privacy = privacy_service
        self._current_session_provider = current_session_provider
        self._previous_session_provider = previous_session_provider
        self._clock = clock
        self._cue_log = cue_log_repo
        self._passive_enabled = passive_enabled
        self._archive = archive_repo
        self._search = search
        self._vector_enabled = vector_enabled
        self._players = game_player_repo
        self._corrections = correction_repo
        self._social_facts = social_fact_repo
        self._min_session_ms = max(0, int(min_session_ms))
        self._pool = RulePool(archive_repo=archive_repo, inside_jokes=inside_jokes, clock=clock)
        self._arrivals = PlaceArrivals()
        self._situation = SituationQuery()
        self._session_id: Optional[int] = None
        # 已知的玩家名（发过弹幕的观众、游戏里说过话的人、身边看到的玩家）：游戏情境查询发出去前遮名用。
        # 不随换场清空：开播后第一批组装前就可能有人在游戏里说过话
        self._known_names: Set[str] = set()
        # 条目 → 本场推送时刻列表
        self._push_at: Dict[ItemKey, List[int]] = {}
        # 本场已经给过"常来的某某进来了"提示的观众（按同人簇主 ID 记，每场每人最多一次）
        self._announced_enter_viewers: Set[int] = set()
        # 本场观众来访统计轻缓存：观众主键 → (行关键快照元组, ViewerPresenceStats)，遗忘或换场立即作废
        self._presence_stats_cache: Dict[int, Tuple[Tuple[Any, ...], Any]] = {}
        # 本批推送的线索：编号 → 对方称呼（回复器档据此说"答应过谁"）
        self._round_labels: Dict[int, str] = {}

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    async def collect(
        self,
        *,
        batch: Sequence[Any],
        open_games: Sequence[str] = (),
        current_saves: Optional[Mapping[str, GameSave]] = None,
        situations: Optional[Mapping[str, GameSituation]] = None,
        brief_archive_ids: Sequence[int] = (),
        brief_commitment_ids: Sequence[int] = (),
        game_tasks: Optional[Mapping[str, str]] = None,
        game_players: Sequence[Tuple[str, str]] = (),
        recent_enters: Sequence[Tuple[str, str]] = (),
    ) -> MemoryCues:
        """算一批的记忆线索：各条线索、老观众在场一行、遗忘回执合成一个块；没有线索返回空串。

        ``current_saves`` 是分存档的游戏眼下所在的存档：挂游戏的线索只推这个存档里答应的。
        ``situations`` 是各游戏角色离记住的地点多远（地点一路判到达用）；``brief_*_ids`` 是
        记忆摘要里列着的条目，被动想起不再推它们；``game_tasks`` 是各游戏手上还在做的委派原话
        （游戏情境查询用）；``game_players`` 是本回合在游戏里开口或冲她做动作的玩家，(游戏, UUID)。
        """
        session_id = self._current_session_provider() if self._current_session_provider is not None else None
        if session_id is None:
            return MemoryCues()
        sid = int(session_id)
        self._reset_if_new_session(sid)
        senders = await self._resolve_senders(batch, current_session_id=sid)
        familiarity_by_message_id = self._build_familiarity_map(senders)
        replied: Optional[Set[int]] = None
        if any(sender.familiarity_tier in ("close", "regular") and not _forgetting(sender) for sender in senders):
            replied = await self._replied_viewer_ids(sid)
        used, used_archive = await load_used_refs(self._chat, sid)
        await self._mark_used(sid, used_archive, used)
        del open_games  # 游戏承诺不再按"游戏开着"轮播，只在对象开口、走到地点或情境命中时单推
        recent_ids, earliest_ms, self_cutoff_ms = await self._recent_session_bounds(sid)
        commitment_progress = await self._load_commitment_progress(sid)
        state = _Round(
            used=used,
            used_archive=used_archive,
            brief_commitments={int(item) for item in brief_commitment_ids},
            brief_archive={int(item) for item in brief_archive_ids},
            saves=dict(current_saves or {}),
            previous_session_id=await self._previous_session_id(),
            recent_session_ids=recent_ids,
            earliest_recent_ms=earliest_ms,
            self_trigger_cutoff_ms=self_cutoff_ms,
            commitment_progress=commitment_progress,
        )
        self._round_labels = {}
        # 对象开口（观众承诺、老观众线索、游戏玩家） -> 游戏地点 -> 规则 -> 向量：每一路只在前面没凑满上限时补
        await self._viewer_cards(senders, state)
        if replied is not None:
            await self._familiar_viewer_cards(senders, replied, state)
        await self._player_cards(sid, game_players, state)
        if self._passive_enabled:
            await self._pool.refresh_if_stale()
            await self._place_cards(situations or {}, state)
            self._rule_cards(self._pool.match(await self._rule_texts(sid, batch, senders)), sid=sid, state=state)
        if self._vector_on():
            for situation in (situations or {}).values():
                for player in situation.players:
                    self.note_name(player)
            self._situation.update(situations or {}, state.saves, game_tasks or {}, names=self._known_names)
            await self._vector_cards(sid, batch, senders, state)
        # 老观众一行不再因为"本批有别人的线索"整行消失；只跳过这一批拿了自己线索的观众，并顺带认出只进房没说话的老观众
        presence = await self._presence_line(
            sid, senders, state.carded_viewers, replied=replied, recent_enters=recent_enters
        )
        notices = self._command_notices(batch)
        text = render_cue_block(cards=[card.text for card in state.cards], presence=presence, notices=notices)
        await self._record_round(sid, state)
        return MemoryCues(
            text=text,
            commitment_ids=state.commitment_ids(),
            card_count=len(state.cards),
            labels=dict(self._round_labels),
            archive_ids=state.ids("archive"),
            asked_archive_ids=state.asked_ids("archive"),
            familiarity_by_message_id=familiarity_by_message_id,
        )

    def reset(self) -> None:
        """下播时清空本场状态（推送计数、进房提示去重、统计缓存、到过的地点与回执追踪都只在本场内有效）。"""
        self._session_id = None
        self._push_at.clear()
        self._announced_enter_viewers.clear()
        self._presence_stats_cache.clear()
        self._arrivals.reset()
        self._pool.reset()
        self._situation.reset()

    async def prefetch(self, message: Any) -> None:
        """一条弹幕到达就在后台算它的查询向量：攒批窗口几秒，组装线索时多半已经算好，决策不等它。

        只发弹幕正文。不在直播、向量一路没开、模拟消息、遗忘与撤回命令、太短的弹幕、发过"忘了我"
        的观众（删除执行没执行、建没建过档都算），一律不发。
        """
        user = getattr(message, "user", None)
        self.note_name(str(getattr(user, "name", "") or ""))
        if not self._vector_on() or self._search is None:
            return
        if self._current_session_provider is None or self._current_session_provider() is None:
            return
        content = self._query_text(message)
        if content is None or not query_worthy(content):
            return
        external_id = str(getattr(user, "id", "") or "").strip()
        if external_id:
            probe = _Sender(external_id=external_id, id_kind=str(getattr(user, "id_kind", "") or "account"))
            # 先查不记录名单（删掉了的、没建过档的也在里面），再看观众行上的待删标记
            probe.forget_listed = await self._on_forget_list(probe)
            if not probe.forget_listed and self._viewers is not None:
                await self._fill_viewer(probe, platform=str(getattr(message, "platform", "") or ""))
            if _forgetting(probe):
                return
        self._search.prefetch(content)

    def note_name(self, name: str) -> None:
        """记下一个玩家名（游戏里说过话的人）：之后的游戏情境查询里出现它就换成"玩家"。"""
        cleaned = (name or "").strip()
        if cleaned:
            self._known_names.add(cleaned)

    def prefetch_game_text(self, text: str) -> None:
        """游戏主控态收到公开玩家游戏公屏时在后台非阻塞预取查询向量（遮名且过滤遗忘命令，绝不阻塞等待）。"""
        if not self._vector_on() or self._search is None:
            return
        if self._current_session_provider is None or self._current_session_provider() is None:
            return
        cleaned = self._sanitize_game_text(text)
        if not cleaned or not query_worthy(cleaned):
            return
        masked = mask_names_in_text(cleaned, self._known_names, "玩家")
        if query_worthy(masked):
            self._search.prefetch(masked)

    async def cues_for_game_lead(
        self,
        *,
        game: str,
        texts: Sequence[str] = (),
        players: Sequence[Tuple[str, str]] = (),
        situation: Optional[Any] = None,
        arrived_places: Sequence[str] = (),
        exclude_texts: Sequence[str] = (),
        current_saves: Optional[Mapping[str, GameSave]] = None,
        brief_archive_ids: Sequence[int] = (),
        brief_commitment_ids: Sequence[int] = (),
        limit: int = MAX_CARDS_PER_BATCH,
    ) -> List[str]:
        """供自主主控游戏 Agent 按需拉取跨场被动记忆记忆线索（纯只读、绝不泄露直播间观众隐私）。

        复用路径：游戏玩家往来卡（``_player_cards``）-> 到达记住地点卡（``_place_cards`` 与显式
        ``arrived_places``）-> 档案触发词规则卡（``_rule_cards``，不含观众老梗）-> 非阻塞向量联想
        （不阻塞等慢嵌入）-> 轻量意图关键词/FTS 兜底（浮出被前 6 条上限截断的现行游戏社交事实与
        相关跨场档案）。严格共享本场每地点只推一次、10 分钟限频、已用编号去重、遗忘名单过滤、
        ``exclude_texts`` 去重与最多 1–2 条上限。
        """
        normalized_game = (game or "").strip()
        effective_limit = min(MAX_CARDS_PER_BATCH, max(0, int(limit)))
        if not normalized_game or effective_limit <= 0:
            return []
        session_id = self._current_session_provider() if self._current_session_provider is not None else None
        if session_id is None:
            return []
        sid = int(session_id)
        self._reset_if_new_session(sid)

        used, used_archive = await load_used_refs(self._chat, sid)
        await self._mark_used(sid, used_archive, used)
        recent_ids, earliest_ms, self_cutoff_ms = await self._recent_session_bounds(sid)
        commitment_progress = await self._load_commitment_progress(sid)
        state = _Round(
            used=used,
            used_archive=used_archive,
            brief_commitments={int(item) for item in brief_commitment_ids},
            brief_archive={int(item) for item in brief_archive_ids},
            saves=dict(current_saves or {}),
            previous_session_id=await self._previous_session_id(),
            recent_session_ids=recent_ids,
            earliest_recent_ms=earliest_ms,
            self_trigger_cutoff_ms=self_cutoff_ms,
            commitment_progress=commitment_progress,
            max_cards=effective_limit,
        )
        self._round_labels = {}
        excluded_compact = tuple(c for raw in (exclude_texts or ()) if (c := _compact_text(raw)))

        # 游戏内往来玩家线索：过滤遗忘名单上的玩家，绝不触碰直播间观众表
        valid_players = await self._filter_game_players(normalized_game, players)
        if valid_players:
            await self._player_cards(sid, valid_players, state)

        clean_texts = [cleaned for raw in texts if (cleaned := self._sanitize_game_text(raw))]
        game_situation = situation if isinstance(situation, GameSituation) else None
        if game_situation is not None:
            for player_name in game_situation.players:
                self.note_name(player_name)

        # 地点到达与档案触发词的规则一路线索（用 SOURCE_PLAYER 来源，天然跳过直播间老梗）
        if self._passive_enabled and not state.full:
            await self._pool.refresh_if_stale()
            if game_situation is not None:
                await self._place_cards({normalized_game: game_situation}, state)
            if arrived_places and not state.full:
                await self._explicit_place_cards(
                    game=normalized_game,
                    arrived_places=arrived_places,
                    situation=game_situation,
                    state=state,
                )
            if clean_texts and not state.full:
                cue_texts = [CueText(source=SOURCE_PLAYER, text=item) for item in clean_texts]
                self._rule_cards(
                    self._pool.match(cue_texts),
                    sid=sid,
                    state=state,
                    excluded_compact=excluded_compact,
                )

        # 非阻塞向量联想：已算好的查询向量立即参与匹配，未算好的仅后台起算不等待
        if self._vector_on() and not state.full:
            await self._vector_cards_for_game_lead(
                sid=sid,
                game=normalized_game,
                texts=clean_texts,
                situation=game_situation,
                excluded_compact=excluded_compact,
                state=state,
            )

        # 轻量非阻塞意图关键词/FTS 兜底：不依赖慢嵌入，按实质实词精准浮出社交事实（尤其 works）与跨场档案
        if self._passive_enabled and clean_texts and not state.full:
            await self._intent_keyword_cards_for_game_lead(
                sid=sid,
                game=normalized_game,
                texts=clean_texts,
                excluded_compact=excluded_compact,
                state=state,
            )

        await self._record_round(sid, state)
        return [card.text for card in state.cards[:effective_limit]]

    def _sanitize_game_text(self, text: str) -> str:
        """清洗游戏文本：去空白、跳过遗忘/撤回指令并经隐私遮名，防止遗忘观众名字经线索回流。"""
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return ""
        if self._privacy is not None:
            if self._privacy.is_forget_command(cleaned) or self._privacy.is_retract_command(cleaned):
                return ""
            cleaned = self._privacy.mask_speech(cleaned)
        return cleaned

    async def _filter_game_players(
        self, default_game: str, players: Sequence[Tuple[str, str]]
    ) -> List[Tuple[str, str]]:
        """归一化游戏玩家 (game, uuid) 列表并排除遗忘名单上的身份标识。"""
        filtered: List[Tuple[str, str]] = []
        seen: Set[Tuple[str, str]] = set()
        for pair in players or ():
            if not isinstance(pair, (tuple, list)) or len(pair) < 2:
                continue
            game_name = str(pair[0] or "").strip() or default_game
            player_uuid = str(pair[1] or "").strip()
            if not player_uuid:
                continue
            key = (game_name, player_uuid)
            if key in seen:
                continue
            if self._privacy is not None:
                try:
                    if await self._privacy.is_external_id_forgotten(
                        id_kind=player_forget_kind(game_name), external_id=player_uuid
                    ):
                        continue
                except Exception as exc:  # noqa: BLE001 - 查不了遗忘表时保守跳过该玩家
                    logger.warning(f"核对游戏玩家遗忘名单失败（跳过该玩家线索）: {exc}", exc=exc)
                    continue
            seen.add(key)
            filtered.append(key)
        return filtered

    async def _vector_cards_for_game_lead(
        self,
        *,
        sid: int,
        game: str,
        texts: Sequence[str],
        situation: Optional[GameSituation],
        excluded_compact: Sequence[str],
        state: _Round,
    ) -> None:
        """游戏主控态非阻塞向量联想：只消费已就绪的查询向量，未就绪的后台起算、本次不等待。"""
        search = self._search
        if search is None:
            return
        queries: List[Tuple[str, str, str]] = []
        for raw_text in texts[-VECTOR_MAX_QUERIES:]:
            masked = mask_names_in_text(raw_text, self._known_names, "玩家")
            if query_worthy(masked):
                queries.append((SOURCE_PLAYER, "", masked))
        if situation is not None:
            self._situation.update({game: situation}, state.saves, {}, names=self._known_names)
            if self._situation.pending:
                queries.append((SOURCE_SITUATION, "", self._situation.pending))

        sources: Tuple["IndexSource", ...] = (
            ("archive", "commitment", "social_fact") if self._social_facts is not None else ("archive", "commitment")
        )
        results = []
        for source, message_id, query_text in queries:
            if not search.vector_ready(query_text, start=True):
                if source == SOURCE_SITUATION:
                    self._situation.missed()
                continue
            try:
                result = await search.search_detailed(query_text, sources=sources, limit=VECTOR_TOP)
            except Exception as exc:  # noqa: BLE001 - 这一条查不了就跳过，不挡其余线索
                logger.warning(f"游戏主控被动线索语义查询失败（跳过）: {exc}", exc=exc)
                continue
            if source == SOURCE_SITUATION:
                self._situation.done()
            results.append((source, message_id, result))

        for hit in vector_hits(results, allowed_sources=sources):
            if state.full:
                return
            if self._skip(hit.key, state, self._passive_blocked(hit.key, state)):
                continue
            if hit.key[0] == "social_fact":
                fact = await self._safe_social_fact_for_game_lead(
                    int(hit.key[1]), game=game, excluded_compact=excluded_compact, state=state
                )
                if fact is None:
                    continue
                text = render_social_fact_card(fact)
                if not text:
                    continue
                detail: Dict[str, Any] = {
                    "social_fact_id": int(fact.id),
                    "fact_kind": fact.kind,
                    "cosine": round(hit.cosine or 0.0, 4),
                    "rank": hit.vector_rank,
                    "fused": round(hit.fused or 0.0, 5),
                }
                self._push(
                    state,
                    _Card(
                        key=hit.key,
                        text=text,
                        kind="social_fact",
                        sources=tuple(sorted(hit.sources)),
                        detail=detail,
                    ),
                )
                continue
            text = await self._vector_card_text(hit, state, excluded_compact=excluded_compact, game=game)
            if not text:
                continue
            detail = {
                "cosine": round(hit.cosine or 0.0, 4),
                "rank": hit.vector_rank,
                "fused": round(hit.fused or 0.0, 5),
            }
            if hit.top3:
                detail["top3"] = list(hit.top3)
            self._push(
                state,
                _Card(key=hit.key, text=text, kind="vector", sources=tuple(sorted(hit.sources)), detail=detail),
            )
        del sid

    async def _safe_social_fact_for_game_lead(
        self,
        fact_id: int,
        *,
        game: str,
        excluded_compact: Sequence[str],
        state: _Round,
    ) -> Optional["SocialFact"]:
        """按主键核实一条社交事实是否通过游戏主控防火墙与排除名单；读不到或不合规返回 None。"""
        if self._social_facts is None:
            return None
        try:
            fact = await self._social_facts.get_fact(fact_id=fact_id)
        except Exception as exc:  # noqa: BLE001 - 读不到就不推，不阻断其余线索
            logger.warning(f"读社交事实 {fact_id} 失败（不推）: {exc}", exc=exc)
            return None
        if fact is None:
            return None
        if not is_social_fact_safe_for_game_lead(fact, game=game, current_saves=state.saves):
            self._skip(("social_fact", fact_id), state, "未过防火墙")
            return None
        if _is_excluded_text(fact.text, excluded_compact):
            self._skip(("social_fact", fact_id), state, "已在常驻规矩")
            return None
        return fact

    async def _intent_keyword_cards_for_game_lead(
        self,
        *,
        sid: int,
        game: str,
        texts: Sequence[str],
        excluded_compact: Sequence[str],
        state: _Round,
    ) -> None:
        """轻量非阻塞意图关键词/FTS 兜底：按任务/计划/玩家托付文本精准浮出社交事实与跨场档案。

        触发条件：游戏主控态拉取线索且当前仍未凑满上限时调用；
        执行顺序：提取高纯度意图实词（全泛词立即返回 0 噪声）-> 并行/依次收集社交事实与跨场档案候选
        -> 严格执行防投毒、存档隔离、exclude_texts 去重与实质词重合门槛 -> 按相关性与优先级推满上限。
        """
        intent_queries: List[str] = []
        for raw_text in texts[-VECTOR_MAX_QUERIES:]:
            masked = mask_names_in_text(raw_text, self._known_names, "玩家")
            if intent_content_tokens(masked):
                intent_queries.append(masked)
        if not intent_queries:
            return

        # 候选元组：(综合分, 类型优先级, 条目新近 ID, 卡片)
        candidates: List[Tuple[float, int, int, _Card]] = []
        if self._social_facts is not None:
            candidates.extend(
                await self._intent_social_fact_candidates(
                    game=game,
                    intent_queries=intent_queries,
                    excluded_compact=excluded_compact,
                    state=state,
                )
            )
        if self._archive is not None:
            candidates.extend(
                await self._intent_archive_candidates(
                    sid=sid,
                    game=game,
                    intent_queries=intent_queries,
                    excluded_compact=excluded_compact,
                    state=state,
                )
            )
        if not candidates:
            return

        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        for _score, _priority, _item_id, card in candidates:
            if state.full:
                return
            if self._skip(card.key, state, self._passive_blocked(card.key, state)):
                continue
            self._push(state, card)

    async def _intent_social_fact_candidates(
        self,
        *,
        game: str,
        intent_queries: Sequence[str],
        excluded_compact: Sequence[str],
        state: _Round,
    ) -> List[Tuple[float, int, int, _Card]]:
        """召回并评估当前存档下的现行游戏社交事实候选（含 FTS 加权与实时仓储兜底）。"""
        repo = self._social_facts
        if repo is None:
            return []
        save = state.saves.get(game)
        # 仅在明确认出当前存档时按 save.id 筛选；存档未上报或未知时固定传 ""（只取整游戏通用行，不扫他档）
        query_save_id = (save.id if save is not None and save.known else "").strip()
        try:
            facts = await repo.list_active_facts(game=game, save_id=query_save_id, limit=_INTENT_SOCIAL_FACT_LIMIT)
        except Exception as exc:  # noqa: BLE001 - 社交事实读失败不挡档案线索
            logger.warning(f"意图联想读游戏社交事实失败（跳过）: {exc}", exc=exc)
            return []
        if not facts:
            return []

        fts_hit_ids: Set[int] = set()
        if self._search is not None:
            for query_text in intent_queries:
                try:
                    found = await self._search.search_detailed(
                        query_text, sources=("social_fact",), limit=_INTENT_FTS_LIMIT, wait_vector_ms=0
                    )
                    for hit in found.hits:
                        if hit.source == "social_fact":
                            fts_hit_ids.add(int(hit.source_id))
                except Exception as exc:  # noqa: BLE001 - FTS 失败不影响本地实质词匹配兜底
                    logger.debug(f"社交事实 FTS 辅助召回失败（改走仓储实词匹配）: {exc}", exc=exc)

        scored: List[Tuple[float, int, int, _Card]] = []
        for fact in facts:
            key: ItemKey = ("social_fact", int(fact.id))
            if not is_social_fact_safe_for_game_lead(fact, game=game, current_saves=state.saves):
                continue
            if _is_excluded_text(fact.text, excluded_compact):
                self._skip(key, state, "已在常驻规矩")
                continue
            if self._passive_blocked(key, state) is not None:
                continue

            best_score = 0.0
            best_phrases: Tuple[str, ...] = ()
            quote_text = (fact.quote or "").strip()
            for query_text in intent_queries:
                s_text, p_text = score_intent_keyword_match(query_text, fact.text)
                if s_text > best_score:
                    best_score, best_phrases = s_text, p_text
                if quote_text:
                    s_full, p_full = score_intent_keyword_match(query_text, f"{fact.text} {quote_text}")
                    weighted = s_full * 0.85
                    if weighted > best_score:
                        best_score, best_phrases = weighted, p_full
            if best_score <= 0.0:
                continue

            card_text = render_social_fact_card(fact)
            if not card_text:
                continue
            # 同等重合度下优先 works（工程现状最直接防重复施工），已命中 FTS 给微幅加分
            priority = 2 if fact.kind == "works" else 1
            total_score = best_score + (0.2 if int(fact.id) in fts_hit_ids else 0.0)
            card = _Card(
                key=key,
                text=card_text,
                kind="social_fact",
                sources=(SOURCE_PLAYER,),
                detail={
                    "social_fact_id": int(fact.id),
                    "fact_kind": fact.kind,
                    "match_score": round(best_score, 3),
                    "matched_count": len(best_phrases),
                },
            )
            scored.append((total_score, priority, int(fact.id), card))
        return scored

    async def _intent_archive_candidates(
        self,
        *,
        sid: int,
        game: str,
        intent_queries: Sequence[str],
        excluded_compact: Sequence[str],
        state: _Round,
    ) -> List[Tuple[float, int, int, _Card]]:
        """召回并评估跨场档案行候选（优先走 FTS 索引，缺失时轻量兜底近期正式场档案）。"""
        lines, fts_hit_ids = await self._load_intent_archive_pool(sid=sid, intent_queries=intent_queries)
        if not lines:
            return []

        scored: List[Tuple[float, int, int, _Card]] = []
        for line in lines:
            if int(line.live_session_id) == sid:
                continue
            line_game = (line.game or "").strip()
            if line_game and line_game != game:
                continue
            key: ItemKey = ("archive", int(line.id))
            if self._skip(key, state, self._other_save(line.game, line.save_id, state)):
                continue
            if _is_excluded_text(line.text, excluded_compact):
                self._skip(key, state, "已在常驻规矩")
                continue
            if self._passive_blocked(key, state) is not None:
                continue

            # 观众定下的事所在行的原话先遮掉观众名再匹配，杜绝观众昵称命中或泄露隐私
            safe_quote = (line.quote or "").strip()
            if line.source == "viewer" and line.source_viewer_names and safe_quote:
                safe_quote = mask_names_in_text(safe_quote, line.source_viewer_names, "那位观众")

            best_score = 0.0
            best_phrases: Tuple[str, ...] = ()
            for query_text in intent_queries:
                s_text, p_text = score_intent_keyword_match(query_text, line.text)
                if s_text > best_score:
                    best_score, best_phrases = s_text, p_text
                if safe_quote:
                    s_full, p_full = score_intent_keyword_match(query_text, f"{line.text} {safe_quote}")
                    weighted = s_full * 0.85
                    if weighted > best_score:
                        best_score, best_phrases = weighted, p_full
            if best_score <= 0.0:
                continue

            why_phrase = best_phrases[0] if best_phrases else ""
            why = f"眼下计划提到了「{why_phrase}」" if why_phrase else "眼下计划和这件事有关"
            card_text = render_archive_card(
                line,
                why=why,
                is_previous=self._line_in_previous_session(line.live_session_id, state),
                note=place_note(line.game, line.save_id, line.save_name, state.saves),
            )
            if not card_text:
                continue
            total_score = best_score + (0.2 if int(line.id) in fts_hit_ids else 0.0)
            card = _Card(
                key=key,
                text=card_text,
                kind="intent",
                sources=(SOURCE_PLAYER,),
                detail={
                    "match_score": round(best_score, 3),
                    "matched_count": len(best_phrases),
                },
            )
            scored.append((total_score, 0, int(line.id), card))
        return scored

    async def _load_intent_archive_pool(
        self, *, sid: int, intent_queries: Sequence[str]
    ) -> Tuple[List["ArchiveLine"], Set[int]]:
        """加载意图联想待评估的正式场档案行：优先查 FTS 索引，未建索引时轻量回扫最近几场正式场。"""
        archive = self._archive
        if archive is None:
            return [], set()

        fts_hit_ids: Set[int] = set()
        if self._search is not None:
            for query_text in intent_queries:
                try:
                    found = await self._search.search_detailed(
                        query_text, sources=("archive",), limit=_INTENT_FTS_LIMIT, wait_vector_ms=0
                    )
                    for hit in found.hits:
                        if hit.source == "archive":
                            fts_hit_ids.add(int(hit.source_id))
                except Exception as exc:  # noqa: BLE001 - FTS 异常退回近期档案兜底
                    logger.debug(f"档案 FTS 意图召回失败（退回近期档案兜底）: {exc}", exc=exc)

        candidate_ids: List[int] = list(fts_hit_ids)
        # 无 FTS 命中或未配置检索索引时，回扫最近数场已归档场次以兼容未预热索引场景
        if not candidate_ids:
            bounds_fn = getattr(archive, "list_archived_session_bounds", None)
            list_lines_fn = getattr(archive, "list_lines", None)
            if callable(bounds_fn) and callable(list_lines_fn):
                try:
                    bounds = await bounds_fn(limit=_INTENT_RECENT_ARCHIVE_SESSIONS + 1)
                    for session_id, _started_ms in bounds:
                        if int(session_id) == sid:
                            continue
                        session_lines = await list_lines_fn(live_session_id=int(session_id))
                        candidate_ids.extend(int(item.id) for item in session_lines)
                except Exception as exc:  # noqa: BLE001 - 近期档案回扫失败不阻断其余线索
                    logger.warning(f"意图联想读取近期跨场档案失败（跳过）: {exc}", exc=exc)

        if not candidate_ids:
            return [], fts_hit_ids
        try:
            # 经 list_cue_lines 统一核实正式场身份并带上观众定下的事的遮名信息
            verified = await archive.list_cue_lines(line_ids=candidate_ids)
        except Exception as exc:  # noqa: BLE001 - 核实失败不推该批档案
            logger.warning(f"意图联想核对跨场档案行失败（跳过）: {exc}", exc=exc)
            return [], fts_hit_ids
        return verified, fts_hit_ids

    def _vector_on(self) -> bool:
        return (
            self._passive_enabled and self._vector_enabled and self._search is not None and self._search.semantic_ready
        )

    # ------------------------------------------------------------------
    # 取数
    # ------------------------------------------------------------------

    def _reset_if_new_session(self, session_id: int) -> None:
        """换场才清状态：同场里推送计数与回执追踪要跨批累积。"""
        if self._session_id == session_id:
            return
        self.reset()
        self._session_id = session_id

    async def _resolve_senders(
        self, batch: Sequence[Any], *, current_session_id: Optional[int] = None
    ) -> List[_Sender]:
        """本批发言人解析成观众：模拟消息不解析，同一观众合并同批消息编号，遗忘请求立即作废缓存。"""
        forget_ext_keys: Set[Tuple[str, str]] = set()
        if self._privacy is not None:
            for message in batch:
                if bool(getattr(message, "simulated", False)):
                    continue
                if str(getattr(message, "message_type", "") or "") != "danmaku":
                    continue
                user = getattr(message, "user", None)
                ext_id = str(getattr(user, "id", "") or "").strip()
                content = str(getattr(message, "content", "") or "")
                if ext_id and content and self._privacy.is_forget_command(content):
                    forget_ext_keys.add((str(getattr(message, "platform", "") or ""), ext_id))

        senders: List[_Sender] = []
        by_ext: Dict[Tuple[str, str], _Sender] = {}
        by_viewer: Dict[int, _Sender] = {}
        for message in batch:
            if bool(getattr(message, "simulated", False)):
                continue
            user = getattr(message, "user", None)
            external_id = str(getattr(user, "id", "") or "").strip()
            if user is None or not external_id:
                continue
            platform = str(getattr(message, "platform", "") or "")
            message_id = str(getattr(message, "message_id", "") or "")
            content = str(getattr(message, "content", "") or "")
            message_type = str(getattr(message, "message_type", "") or "")
            ext_key = (platform, external_id)
            is_forget_cmd = ext_key in forget_ext_keys or (
                self._privacy is not None and message_type == "danmaku" and self._privacy.is_forget_command(content)
            )

            existing = by_ext.get(ext_key)
            if existing is not None:
                if is_forget_cmd:
                    self._evict_sender_cache(existing)
                elif message_id and not _forgetting(existing) and message_id not in existing.message_ids:
                    existing.message_ids.append(message_id)
                continue

            sender = _Sender(
                external_id=external_id,
                id_kind=str(getattr(user, "id_kind", "") or "account"),
                user_name=str(getattr(user, "name", "") or ""),
                content=content,
                message_id=message_id,
                message_type=message_type,
                platform=platform,
            )
            self.note_name(sender.user_name)
            if self._viewers is not None:
                await self._fill_viewer(
                    sender,
                    platform=platform,
                    current_session_id=current_session_id,
                )
            sender.forget_listed = is_forget_cmd or await self._on_forget_list(sender)
            if _forgetting(sender):
                self._evict_sender_cache(sender)
            elif message_id:
                sender.message_ids.append(message_id)

            by_ext[ext_key] = sender
            if sender.viewer_id is not None:
                dedup_ids = sender.cluster_viewer_ids or (sender.viewer_id,)
                matched_existing = next((by_viewer[vid] for vid in dedup_ids if vid in by_viewer), None)
                if matched_existing is not None:
                    by_ext[ext_key] = matched_existing
                    if _forgetting(sender):
                        self._evict_sender_cache(matched_existing)
                    elif (
                        message_id
                        and not _forgetting(matched_existing)
                        and message_id not in matched_existing.message_ids
                    ):
                        matched_existing.message_ids.append(message_id)
                    continue
                for vid in dedup_ids:
                    by_viewer[vid] = sender
            senders.append(sender)
        return senders

    def _evict_sender_cache(self, sender: _Sender) -> None:
        """收到遗忘请求或命中不记录名单时，立即清除该观众的本场统计缓存与熟悉度映射。"""
        sender.forget_listed = True
        sender.familiarity_tier = "none"
        sender.message_ids.clear()
        for vid in sender.cluster_viewer_ids or ((sender.viewer_id,) if sender.viewer_id is not None else ()):
            self._presence_stats_cache.pop(int(vid), None)

    @staticmethod
    def _build_familiarity_map(senders: Sequence[_Sender]) -> Dict[str, str]:
        """提取本批非遗忘老观众弹幕的 message_id → 熟悉度档位（仅 close 与 regular 入表）。"""
        mapping: Dict[str, str] = {}
        for sender in senders:
            if _forgetting(sender) or sender.familiarity_tier not in ("close", "regular"):
                continue
            for mid in sender.message_ids:
                if mid:
                    mapping[mid] = sender.familiarity_tier
        return mapping

    async def _fill_viewer(
        self,
        sender: _Sender,
        *,
        platform: str,
        current_session_id: Optional[int] = None,
    ) -> None:
        """按外部 ID 只读解析观众（合并待确认关联簇场次数与熟悉度，不建观众行）；解析失败按陌生人处理。"""
        assert self._viewers is not None  # noqa: S101 调用方已判空
        try:
            viewer_id = await self._viewers.find_viewer_id(platform=platform, external_id=sender.external_id)
        except Exception as exc:  # noqa: BLE001 - 解析失败按陌生人处理，不挡决策
            logger.warning(f"线索推送解析观众失败（按陌生人处理）: {exc}", exc=exc)
            return
        if viewer_id is None:
            return
        try:
            row = await self._viewers.get_viewer(viewer_id=int(viewer_id))
        except Exception as exc:  # noqa: BLE001 - 取不到详情就当不认识，不挡决策
            logger.warning(f"线索推送读观众 {viewer_id} 失败（按陌生人处理）: {exc}", exc=exc)
            return
        if row is None:
            return
        vid = int(viewer_id)
        sender.viewer_id = vid
        sender.primary_viewer_id = vid
        sender.cluster_viewer_ids = (vid,)
        sender.user_name = row.user_name or sender.user_name
        sender.forget_requested_at_ms = row.forget_requested_at_ms
        sender.sessions_count = int(row.sessions_count)
        sender.last_session_id = int(row.last_session_id) if row.last_session_id is not None else None
        sender.prev_seen_ms = row.prev_seen_ms
        if row.forget_requested_at_ms is not None:
            self._presence_stats_cache.pop(vid, None)
            return
        resolver = getattr(self._viewers, "resolve_presence_stats", None)
        if callable(resolver):
            row_sig = (
                current_session_id,
                int(row.sessions_count),
                int(getattr(row, "replied_count", 0) or 0),
                int(getattr(row, "paid_count", 0) or 0),
                str(row.user_name or ""),
                row.prev_seen_ms,
                row.forget_requested_at_ms,
            )
            cached = self._presence_stats_cache.get(vid)
            merged = cached[1] if cached is not None and cached[0] == row_sig else None
            if merged is None:
                try:
                    merged = await resolver(
                        viewer_id=vid,
                        current_session_id=current_session_id,
                    )
                    if merged is not None and merged.forget_requested_at_ms is None:
                        self._presence_stats_cache[vid] = (row_sig, merged)
                except Exception as exc:  # noqa: BLE001 - 合并查询失败退回单行计数
                    logger.debug(f"合并待确认观众场次数失败（退回单行计数）: {exc}", exc=exc)
            if merged is not None:
                sender.primary_viewer_id = int(merged.primary_viewer_id)
                sender.cluster_viewer_ids = merged.cluster_viewer_ids
                sender.earlier_sessions = merged.earlier_sessions
                sender.prev_seen_ms = merged.prev_seen_ms
                sender.familiarity_tier = str(getattr(merged, "familiarity_tier", "none") or "none")
                sender.former_names = tuple(getattr(merged, "former_names", ()) or ())
                if merged.forget_requested_at_ms is not None:
                    sender.forget_requested_at_ms = merged.forget_requested_at_ms
                    sender.familiarity_tier = "none"
                    self._presence_stats_cache.pop(vid, None)
                return
        # 假对象未实现 resolve_presence_stats 时，按单行场次数退回基础熟悉度判定
        fallback_earlier = sender.sessions_count
        if current_session_id is not None and sender.last_session_id == current_session_id:
            fallback_earlier = max(0, fallback_earlier - 1)
        if fallback_earlier >= MIN_SESSIONS_FOR_PRESENCE:
            sender.familiarity_tier = "regular"

    async def _on_forget_list(self, sender: _Sender) -> bool:
        """这位发言人的外部 ID 在不记录名单里就返回 True；没有隐私服务时只能靠观众行上的标记。

        查不了名单时按在名单里处理：宁可这条弹幕不拿来想起旧事，也不把可能要求过遗忘的人的话
        发给嵌入服务。
        """
        if self._privacy is None or not sender.external_id:
            return False
        try:
            return bool(
                await self._privacy.is_external_id_forgotten(id_kind=sender.id_kind, external_id=sender.external_id)
            )
        except Exception as exc:  # noqa: BLE001 - 查不了名单就当在名单里，这条弹幕不进查询
            logger.warning(f"线索推送查不记录名单失败（这条弹幕不拿来想起旧事）: {exc}", exc=exc)
            return True

    async def _replied_viewer_ids(self, session_id: int) -> Set[int]:
        """本场她回应过的观众。

        每批查一次、不整场缓存：缓存会让"这一批刚回应过 ta"晚一批才生效，
        老观众一行会在她已经回应之后又多冒一批；一次轻查询换时序正确。
        """
        try:
            ids = await self._chat.list_session_replied_viewer_ids(live_session_id=session_id)
        except Exception as exc:  # noqa: BLE001 - 查不到按都没回应过，最多多出一行提示
            logger.warning(f"读本场已回应的观众失败（本批不做回应过滤）: {exc}", exc=exc)
            return set()
        return {int(item) for item in ids}

    async def _rule_texts(self, session_id: int, batch: Sequence[Any], senders: Sequence[_Sender]) -> List[CueText]:
        """规则一路看的文本：本批能进查询的观众弹幕，加她最近几句播出的台词。"""
        texts = [
            CueText(source=SOURCE_DANMAKU, text=content, message_id=message_id)
            for content, message_id in self._query_messages(batch, senders)
        ]
        texts.extend(CueText(source=SOURCE_SELF, text=line) for line in await self._recent_lines(session_id))
        return texts

    def _query_messages(self, batch: Sequence[Any], senders: Sequence[_Sender]) -> List[Tuple[str, str]]:
        """本批能拿来想起旧事的弹幕（正文, 弹幕编号）：只看正文，不带昵称与外部 ID。

        模拟消息不算（线索只认真观众）；遗忘与撤回命令本身不算；发过"忘了我"的观众（删除执行没执行、
        建没建过档都算），ta 的弹幕一律不算。
        """
        forgetting = {sender.external_id for sender in senders if _forgetting(sender)}
        messages: List[Tuple[str, str]] = []
        for message in batch:
            content = self._query_text(message)
            if content is None:
                continue
            user = getattr(message, "user", None)
            if str(getattr(user, "id", "") or "").strip() in forgetting:
                continue
            messages.append((content, str(getattr(message, "message_id", "") or "")))
        return messages

    def _query_text(self, message: Any) -> Optional[str]:
        """一条消息能拿来想起旧事时返回它的正文：真观众的弹幕、不是遗忘或撤回命令；否则 None。"""
        if bool(getattr(message, "simulated", False)):
            return None
        if str(getattr(message, "message_type", "") or "") != "danmaku":
            return None
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            return None
        if self._privacy is not None and (
            self._privacy.is_forget_command(content) or self._privacy.is_retract_command(content)
        ):
            return None
        return content

    async def _recent_lines(self, session_id: int) -> List[str]:
        """她本场最近几句播出了的台词（确知没送达的不算）；读不到就当没有。"""
        try:
            rows = await self._chat.list_recent_live_chat(
                live_session_id=session_id, limit=RECENT_SPEECH_LINES, sender_role="assistant"
            )
        except Exception as exc:  # noqa: BLE001 - 读不到她的台词，这一批规则只看弹幕
            logger.warning(f"读她最近的台词失败（这一批规则只看弹幕）: {exc}", exc=exc)
            return []
        return [row.content for row in rows if row.dropped_at_ms is None and (row.content or "").strip()]

    async def _mark_used(self, session_id: int, used_archive: Set[int], used: Set[int]) -> None:
        """本场已被点名用上的条目：回填记忆线索记录里用上的时刻（只回填还没回填的）。"""
        if self._cue_log is None or not (used_archive or used):
            return
        try:
            await self._cue_log.mark_used(
                live_session_id=session_id,
                archive_line_ids=used_archive,
                commitment_ids=used,
                used_at_ms=int(self._clock()),
            )
        except Exception as exc:  # noqa: BLE001 - 记录回填失败不挡推送，下一批再回填
            logger.warning(f"回填记忆线索用上时刻失败（下一批再试）: {exc}", exc=exc)

    async def _record_round(self, session_id: int, state: _Round) -> None:
        """这一批推了什么、没推什么记一行日志；推出去的线索写进线索推送记录。"""
        if state.cards:
            pushed = "、".join(
                f"{_label(card.key)}（{card.kind}，来源 {'+'.join(card.sources) or '无'}）" for card in state.cards
            )
            skipped = f"；没推 {'、'.join(state.skipped)}" if state.skipped else ""
            logger.info(f"线索推送：推了 {pushed}{skipped}")
        elif state.skipped:
            logger.debug(f"线索推送：没推 {'、'.join(state.skipped)}")
        if self._cue_log is None or (not state.cards and not state.probes):
            return
        now = int(self._clock())
        entries = [
            NewCueLogEntry(
                live_session_id=session_id,
                pushed_at_ms=now,
                card_kind=card.kind,
                archive_line_id=int(card.key[1]) if card.key[0] == "archive" else None,
                commitment_id=int(card.key[1]) if card.key[0] == "commitment" else None,
                sources=list(card.sources),
                detail=dict(card.detail),
                self_triggered=card.self_triggered,
            )
            for card in state.cards
        ]
        entries.extend(state.probes)
        try:
            await self._cue_log.insert_entries(entries=entries)
        except Exception as exc:  # noqa: BLE001 - 记录写不进去不挡推送，日志里仍看得到这一批
            logger.warning(f"写记忆线索记录失败（这一批只留日志）: {exc}", exc=exc)

    # ------------------------------------------------------------------
    # 各条线索
    # ------------------------------------------------------------------

    async def _viewer_cards(self, senders: Sequence[_Sender], state: _Round) -> None:
        """对象开口：本批开口的观众名下有未了结承诺，推一条对 ta 的承诺线索（每人一条）。"""
        for sender in senders:
            if state.full:
                return
            if sender.viewer_id is None or sender.forget_requested_at_ms is not None:
                continue
            for commitment in await self._open_commitments(viewer_id=sender.viewer_id):
                key: ItemKey = ("commitment", int(commitment.id))
                if self._skip(key, state, "搁了很久" if self._dormant_commitment(commitment, state) else None):
                    continue
                if self._blocked(key, state) is not None:
                    continue
                text = render_viewer_card(
                    commitment,
                    viewer_label=sender.user_name or "观众",
                    message_id=sender.message_id,
                    is_previous=self._registered_in_previous_session(commitment, state.previous_session_id),
                    place=place_note(commitment.game, commitment.save_id, commitment.save_name, state.saves),
                    progress_note=state.commitment_progress.get(int(commitment.id), ""),
                )
                self._push(state, _Card(key=key, text=text, kind="viewer", sources=(SOURCE_DANMAKU,)))
                state.carded_viewers.add(int(sender.viewer_id))
                self._round_labels[int(commitment.id)] = sender.user_name or "观众"
                break

    async def _familiar_viewer_cards(self, senders: Sequence[_Sender], replied: Set[int], state: _Round) -> None:
        """老观众开口：本场尚未回应过的老观众若名下有已批准近况或共同造梗归属，推一张老观众线索（记录不带昵称）。"""
        if self._viewers is None:
            return
        notes_fn = getattr(self._viewers, "approved_viewer_notes", None)
        authored_fn = getattr(self._archive, "list_viewer_authored_lines", None) if self._archive is not None else None
        if not callable(notes_fn) and not callable(authored_fn):
            return
        for sender in senders:
            if state.full:
                return
            if sender.viewer_id is None or _forgetting(sender):
                continue
            if sender.familiarity_tier not in ("close", "regular"):
                continue
            cluster_ids = sender.cluster_viewer_ids or (int(sender.viewer_id),)
            primary_id = int(sender.primary_viewer_id or sender.viewer_id)
            key: ItemKey = ("viewer_familiar", primary_id)
            if self._skip(key, state, "本场已回应" if replied.intersection(cluster_ids) else self._blocked(key, state)):
                continue
            notes = []
            if callable(notes_fn):
                try:
                    notes = await notes_fn(viewer_ids=cluster_ids, limit=_MAX_NOTES_PER_FAMILIAR_VIEWER)
                except Exception as exc:  # noqa: BLE001 - 读取失败只少这一部分，不挡其余线索
                    logger.warning(f"读老观众近况失败（跳过该部分）: {exc}", exc=exc)
            authored = []
            if callable(authored_fn):
                try:
                    authored = await authored_fn(viewer_ids=cluster_ids, limit=_MAX_AUTHORED_PER_FAMILIAR_VIEWER)
                except Exception as exc:  # noqa: BLE001 - 读取失败只少这一部分，不挡其余线索
                    logger.warning(f"读老观众共同造梗归属失败（跳过该部分）: {exc}", exc=exc)
            if not notes and not authored:
                continue
            text = render_familiar_viewer_card(
                user_name=sender.user_name or "观众",
                tier=sender.familiarity_tier,
                former_names=sender.former_names,
                self_notes=[item.title for item in notes],
                authored_titles=[line.text for line in authored],
            )
            if not text:
                continue
            self._push(
                state,
                _Card(
                    key=key,
                    text=text,
                    kind="viewer_familiar",
                    sources=("viewer_presence",),
                    detail={"viewer_id": primary_id},
                ),
            )
            state.carded_viewers.update(cluster_ids)

    async def _player_cards(self, session_id: int, game_players: Sequence[Tuple[str, str]], state: _Round) -> None:
        """对象开口扩到游戏玩家：本回合在游戏里开口或冲她做动作的玩家，以前有往来的推一张"这位是谁、和你有过什么"。

        线索只推给这一窗找过她的那几位（按 UUID 对上，不会推错人）；同一位每场至多两次、隔十分钟，
        本场她已经回应过他（台词记下了回应对象）就不再推——认出来了，用上即停。新面孔没有往来，不推。
        人批准过的他的自述（至多两条）写成"他跟你说过"；答应过他、还没了结的事（至多两条）写在线索末尾：
        这两样都只在他露面时提，不进记忆摘要。
        """
        if self._players is None or not game_players:
            return
        replied = set(await self._players.replied_player_ids(live_session_id=session_id))
        for game, uuid in game_players:
            if state.full:
                return
            player = await self._players.find_player(game=game, uuid=uuid)
            if player is None or player.forget_requested_at_ms is not None:
                # 他发过"忘了我"、还在宽限期里：不再提起他
                continue
            key: ItemKey = ("player", int(player.id))
            if self._skip(key, state, "本场说上话了" if player.id in replied else self._blocked(key, state)):
                continue
            history = await self._players.list_history(player_id=player.id, exclude_session_id=session_id)
            # 答应过他的事：本场已经点名用上或已搁了很久的不再写，其余至多两条
            promises = [
                item
                for item in await self._open_commitments(player_id=int(player.id), limit=_MAX_PROMISES_PER_PLAYER)
                if int(item.id) not in state.used and not self._dormant_commitment(item, state)
            ]
            # 别的存档里答应的照样提，但写明是在哪个存档答应的
            notes = [(item, place_note(item.game, item.save_id, item.save_name, state.saves)) for item in promises]
            # 他自己说过、人批准过的事：同样写明是在哪个存档说的
            statements = await self._players.approved_statements(
                player_id=int(player.id), limit=_MAX_STATEMENTS_PER_PLAYER
            )
            said = [
                (item.title, place_note(item.game, item.save_id, item.save_name, state.saves)) for item in statements
            ]
            text = render_player_card(
                player,
                history,
                previous_session_id=state.previous_session_id,
                promises=notes,
                statements=said,
                commitment_progress=state.commitment_progress,
            )
            if not text:
                continue
            detail: Dict[str, Any] = {"player_id": player.id}
            if statements:
                detail["statement_ids"] = [int(item.id) for item in statements]
            if promises:
                detail["commitment_ids"] = [int(item.id) for item in promises]
                # 线索末尾的约#N 她点了要认：回复器档说"答应过 Alex"，不说成"答应过大家"
                for item in promises:
                    self._round_labels[int(item.id)] = player.name
            card = _Card(key=key, text=text, kind="player", sources=(SOURCE_PLAYER,), detail=detail)
            self._push(state, card)

    async def _place_cards(self, situations: Mapping[str, GameSituation], state: _Round) -> None:
        """游戏地点：刚走到记住的地点跟前，单推挂在那里的档案行与提到该地点的未搁置公开承诺。"""
        arrivals = self._arrivals.update(situations, state.saves)
        if not arrivals:
            return
        await self._emit_place_arrivals(arrivals, state)

    async def _explicit_place_cards(
        self,
        *,
        game: str,
        arrived_places: Sequence[str],
        situation: Optional[GameSituation],
        state: _Round,
    ) -> None:
        """游戏主控侧已本地判定到达记住地标时，按本场每地点一次与同存档口径补推地点一路的线索。"""
        distances: Dict[str, int] = {}
        if situation is not None:
            for nearby in situation.places:
                if nearby.name and nearby.name not in distances:
                    distances[nearby.name] = int(nearby.distance)
        arrivals: List[Tuple[str, str, int]] = []
        for raw in arrived_places:
            place = str(raw or "").strip()
            if not place:
                continue
            if not self._arrivals.claim_arrived(game, place, state.saves):
                continue
            arrivals.append((game, place, distances.get(place, 0)))
        if arrivals:
            await self._emit_place_arrivals(arrivals, state)

    async def _emit_place_arrivals(self, arrivals: Sequence[Tuple[str, str, int]], state: _Round) -> None:
        """把已确认到达的地点元组转成地点往事线索与相关未搁置公开承诺线索（遵守同存档与 10 分钟限频）。"""
        game_commitments: Dict[str, List[Any]] = {}
        for game, place, distance in arrivals:
            for line in self._pool.lines_at(game, place, state.saves):
                key: ItemKey = ("archive", int(line.id))
                if self._skip(key, state, self._passive_blocked(key, state)):
                    continue
                # 地点一路的线索用提示语气，不替她断言眼下在哪：位置可能是几分钟前读到的，存档也只说"记录上"
                same_save = place_of(line.game, line.save_id, state.saves) is SavePlace.SAME
                text = render_archive_card(
                    line,
                    why=f"游戏里的位置显示你可能走到了「{place}」附近",
                    is_previous=self._line_in_previous_session(line.live_session_id, state),
                    note="记录上和眼下是同一个存档" if same_save else "",
                )
                # 记忆线索记录不存地点名：复盘时从档案行眼下挂的地点现查，行删了就看不到
                detail = {"distance": distance}
                self._push(state, _Card(key=key, text=text, kind="place", sources=(SOURCE_PLACE,), detail=detail))
            if not place or state.full:
                continue
            if game not in game_commitments:
                game_commitments[game] = await self._open_commitments(game=game, limit=_MAX_COMMITMENTS_PER_GAME)
            for commitment in game_commitments[game]:
                if commitment.has_target:
                    continue
                if place not in f"{commitment.what} {commitment.quote}":
                    continue
                key = ("commitment", int(commitment.id))
                if self._skip(key, state, "搁了很久" if self._dormant_commitment(commitment, state) else None):
                    continue
                if self._skip(key, state, self._other_save(game, commitment.save_id, state)):
                    continue
                if self._skip(key, state, self._passive_blocked(key, state)):
                    continue
                save_place = place_of(game, commitment.save_id, state.saves)
                text = render_recalled_commitment_card(
                    commitment,
                    why=f"游戏里的位置显示你可能走到了「{place}」附近",
                    unsure_save=save_place is SavePlace.UNSURE,
                    progress_note=state.commitment_progress.get(int(commitment.id), ""),
                )
                self._push(
                    state,
                    _Card(key=key, text=text, kind="place", sources=(SOURCE_PLACE,), detail={"distance": distance}),
                )

    def _rule_cards(
        self,
        hits: Sequence[PassiveHit],
        *,
        sid: int,
        state: _Round,
        excluded_compact: Sequence[str] = (),
    ) -> None:
        """规则：弹幕或她最近的台词提到了触发词；自触发须不在记忆摘要、来自两场以前，且每条每场只推一次。"""
        for hit in hits:
            limit = MAX_SELF_PUSHES_PER_CARD if hit.self_only else MAX_PUSHES_PER_CARD
            if self._skip(hit.key, state, self._passive_blocked(hit.key, state, limit=limit)):
                continue
            if hit.self_only and hit.key[0] == "archive":
                if self._skip(hit.key, state, self._self_trigger_age_blocked(int(hit.key[1]), sid=sid, state=state)):
                    continue
            text = self._rule_card_text(hit, state, excluded_compact=excluded_compact)
            if not text:
                continue
            kind = "joke" if hit.key[0] == "joke" else "rule"
            # 记忆线索记录不存触发词原词（可能带观众昵称）：只存第几个触发词与匹配方式，复盘时从条目现查
            detail: Dict[str, Any] = {"keyword_index": list(hit.keyword_indexes), "how": list(hit.hows)}
            if hit.key[0] == "joke":
                detail["joke_index"] = self._pool.joke_index(str(hit.key[1]))
            card = _Card(
                key=hit.key,
                text=text,
                kind=kind,
                sources=tuple(sorted(hit.sources)),
                detail=detail,
                self_triggered=hit.self_only,
            )
            self._push(state, card)

    def _self_trigger_age_blocked(self, line_id: int, *, sid: int, state: _Round) -> Optional[str]:
        """只由她自己台词命中的规则一路线索，必须来自两场以前（排掉本场及最近两个完结正式场内的行）。"""
        line = self._pool.archive_line(line_id)
        if line is None:
            return None
        line_sid = int(line.live_session_id)
        if line_sid == sid:
            return "近两场内"
        if state.self_trigger_cutoff_ms is None:
            return None
        if state.self_trigger_cutoff_ms <= 0:
            return "近两场内"
        if line_sid in state.recent_session_ids[:2]:
            return "近两场内"
        started_ms = self._pool.session_started_at_ms(line_sid)
        if started_ms is not None and started_ms >= state.self_trigger_cutoff_ms:
            return "近两场内"
        return None

    def _rule_card_text(
        self,
        hit: PassiveHit,
        state: _Round,
        *,
        excluded_compact: Sequence[str] = (),
    ) -> str:
        """规则命中的卡面；条目不在了、已在常驻排除名单、或是别的存档里的事时返回空串。"""
        kind, value = hit.key
        if kind == "joke":
            return render_joke_card(str(value), message_id=hit.message_id)
        keyword = hit.keywords[0] if hit.keywords else ""
        if SOURCE_DANMAKU in hit.sources:
            who = f"弹幕 {hit.message_id} " if hit.message_id else "观众"
            why = f"{who}提到「{keyword}」"
        elif SOURCE_PLAYER in hit.sources:
            why = f"游戏里刚提到「{keyword}」"
        else:
            why = f"你刚说到「{keyword}」"
        line = self._pool.archive_line(int(value))
        if line is None or self._skip(hit.key, state, self._other_save(line.game, line.save_id, state)):
            return ""
        if excluded_compact and _is_excluded_text(line.text, excluded_compact):
            self._skip(hit.key, state, "已在常驻规矩")
            return ""
        return render_archive_card(
            line,
            why=why,
            is_previous=self._line_in_previous_session(line.live_session_id, state),
            note=place_note(line.game, line.save_id, line.save_name, state.saves),
        )

    async def _vector_cards(self, sid: int, batch: Sequence[Any], senders: Sequence[_Sender], state: _Round) -> None:
        """向量：本批弹幕（最新几条）与变了的游戏情境各查一次，前三名分数无论推没推都记入线索记录。"""
        search = self._search
        if search is None:
            return
        queries: List[Tuple[str, str, str]] = [
            (SOURCE_DANMAKU, message_id, content)
            for content, message_id in self._query_messages(batch, senders)
            if query_worthy(content)
        ][-VECTOR_MAX_QUERIES:]
        if self._situation.pending:
            queries.append((SOURCE_SITUATION, "", self._situation.pending))
        results = []
        for source, message_id, text in queries:
            # 弹幕的向量在到达时预取过一次，这里只看缓存，没算好就这一批不推、不补发；情境查询没有预取，
            # 这里没算好就开始算，留给下一批
            if not search.vector_ready(text, start=source == SOURCE_SITUATION):
                if source == SOURCE_SITUATION:
                    self._situation.missed()
                continue
            try:
                result = await search.search_detailed(text, sources=("archive", "commitment"), limit=VECTOR_TOP)
            except Exception as exc:  # noqa: BLE001 - 这一条查不了就跳过，不挡这一批别的线索
                logger.warning(f"被动想起的语义查询失败（这一条跳过）: {exc}", exc=exc)
                continue
            if source == SOURCE_SITUATION:
                self._situation.done()
            results.append((source, message_id, result))
        recorded_queries: Set[int] = set()
        for hit in vector_hits(results):
            if self._skip(hit.key, state, self._passive_blocked(hit.key, state)):
                continue
            text = await self._vector_card_text(hit, state)
            if not text:
                continue
            detail: Dict[str, Any] = {
                "cosine": round(hit.cosine or 0.0, 4),
                "rank": hit.vector_rank,
                "fused": round(hit.fused or 0.0, 5),
            }
            if hit.top3:
                detail["top3"] = list(hit.top3)
            if hit.best_query_index is not None:
                recorded_queries.add(hit.best_query_index)
            self._push(
                state, _Card(key=hit.key, text=text, kind="vector", sources=tuple(sorted(hit.sources)), detail=detail)
            )
        # 没推出向量一路线索的语义查询同样把前三名余弦与融合分落库留痕，供阈值校准分析
        now = int(self._clock())
        for query_idx, (source, _message_id, result) in enumerate(results):
            if query_idx in recorded_queries or not result.vector_used:
                continue
            top3 = vector_top3_summary(result)
            first = top3[0] if top3 else None
            state.probes.append(
                NewCueLogEntry(
                    live_session_id=sid,
                    pushed_at_ms=now,
                    card_kind="vector_probe",
                    archive_line_id=int(first["id"]) if first is not None and first["kind"] == "archive" else None,
                    commitment_id=int(first["id"]) if first is not None and first["kind"] == "commitment" else None,
                    sources=[source],
                    detail={
                        "pushed": False,
                        "cosine": first["cosine"] if first is not None else None,
                        "top3": top3,
                    },
                    self_triggered=False,
                )
            )

    async def _vector_card_text(
        self,
        hit: PassiveHit,
        state: _Round,
        *,
        excluded_compact: Sequence[str] = (),
        game: str = "",
    ) -> str:
        """向量命中的卡面；不是正式场的档案行、不能推的承诺、别的存档或排除名单里的事返回空串。"""
        if SOURCE_DANMAKU in hit.sources:
            why = f"弹幕 {hit.message_id} 说的像这件事" if hit.message_id else "观众说的像这件事"
        elif SOURCE_PLAYER in hit.sources:
            why = "游戏里刚说的像这件事"
        else:
            why = "眼下游戏里的情境像这件事"
        kind, value = hit.key
        if kind == "archive":
            line = await self._cue_line(int(value))
            if line is None:
                self._skip(hit.key, state, "不是正式场的行")
                return ""
            if game and line.game and (line.game or "").strip() != game:
                self._skip(hit.key, state, "别的游戏")
                return ""
            if self._skip(hit.key, state, self._other_save(line.game, line.save_id, state)):
                return ""
            if excluded_compact and _is_excluded_text(line.text, excluded_compact):
                self._skip(hit.key, state, "已在常驻规矩")
                return ""
            return render_archive_card(
                line,
                why=why,
                is_previous=self._line_in_previous_session(line.live_session_id, state),
                note=place_note(line.game, line.save_id, line.save_name, state.saves),
            )
        commitment = await self._recallable_commitment(int(value))
        if commitment is None:
            self._skip(hit.key, state, "不是未了结的公开承诺")
            return ""
        if game and commitment.game and (commitment.game or "").strip() != game:
            self._skip(hit.key, state, "别的游戏")
            return ""
        if self._skip(hit.key, state, "搁了很久" if self._dormant_commitment(commitment, state) else None):
            return ""
        if self._skip(hit.key, state, self._other_save(commitment.game, commitment.save_id, state)):
            return ""
        place = place_of(commitment.game, commitment.save_id, state.saves)
        return render_recalled_commitment_card(
            commitment,
            why=why,
            unsure_save=place is SavePlace.UNSURE,
            progress_note=state.commitment_progress.get(int(commitment.id), ""),
        )

    async def _cue_line(self, line_id: int) -> Optional["ArchiveLine"]:
        """向量命中的档案行再核一次：只推正式场的；读不到当没有。"""
        if self._archive is None:
            return None
        try:
            lines = await self._archive.list_cue_lines(line_ids=[line_id])
        except Exception as exc:  # noqa: BLE001 - 核不了就不推这一行
            logger.warning(f"核对档案行 {line_id} 失败（不推）: {exc}", exc=exc)
            return None
        return lines[0] if lines else None

    async def _recallable_commitment(self, commitment_id: int) -> Optional["Commitment"]:
        """向量命中的承诺：只推未了结、不针对具体的人的（别人说的像，推对某人的会把 ta 的事翻给全场）。"""
        try:
            commitment = await self._commitments.get_commitment(commitment_id=commitment_id)
        except Exception as exc:  # noqa: BLE001 - 读不到就不推
            logger.warning(f"读承诺 {commitment_id} 失败（不推）: {exc}", exc=exc)
            return None
        if commitment is None or commitment.status != "open" or commitment.has_target:
            return None
        return commitment

    # ------------------------------------------------------------------
    # 推不推
    # ------------------------------------------------------------------

    def _blocked(self, key: ItemKey, state: _Round, *, limit: int = MAX_PUSHES_PER_CARD) -> Optional[str]:
        """用上即停加限频：本场已用过、本批已推、推满或没到间隔都不推；返回原因，能推返回 None。"""
        kind, value = key
        if (kind == "commitment" and int(value) in state.used) or (
            kind == "archive" and int(value) in state.used_archive
        ):
            return "本场用过"
        if any(card.key == key for card in state.cards):
            return "本批已推"
        pushes = self._push_at.get(key, [])
        if len(pushes) >= limit:
            return "推满"
        if pushes and int(self._clock()) - pushes[-1] < PUSH_COOLDOWN_MS:
            return "冷却"
        return None

    def _passive_blocked(self, key: ItemKey, state: _Round, *, limit: int = MAX_PUSHES_PER_CARD) -> Optional[str]:
        """被动想起另加两条：记忆摘要里已经列着的不推；这一批已经凑满上限的不推。"""
        kind, value = key
        if (kind == "commitment" and int(value) in state.brief_commitments) or (
            kind == "archive" and int(value) in state.brief_archive
        ):
            return "已在记忆摘要"
        reason = self._blocked(key, state, limit=limit)
        if reason is None and state.full:
            return "每批上限"
        return reason

    @staticmethod
    def _dormant_commitment(commitment: Any, state: _Round) -> bool:
        """承诺已转成"搁了很久"时线索侧不再推送（只留回忆工具可查）。"""
        return is_commitment_dormant(
            commitment,
            recent_session_ids=state.recent_session_ids,
            earliest_recent_ms=state.earliest_recent_ms,
        )

    async def _recent_session_bounds(self, session_id: int) -> Tuple[Tuple[int, ...], int, Optional[int]]:
        """本场之前最近至多三个已完结正式场的场次号、三场最早开播时刻与两场前自触发门槛时刻。"""
        if self._archive is None:
            return (), 0, None
        try:
            bounds = await self._archive.recent_ended_session_bounds(
                kind=SESSION_KIND_LIVE,
                min_duration_ms=self._min_session_ms,
                exclude_session_id=session_id,
                limit=3,
            )
        except Exception as exc:  # noqa: BLE001 - 查不到边界时不误拦承诺线索
            logger.warning(f"线索推送查最近完结场次边界失败（本批不做场次过期拦截）: {exc}", exc=exc)
            return (), 0, None
        if not bounds:
            return (), 0, 0
        recent_ids = tuple(int(item[0]) for item in bounds)
        earliest_ms = int(bounds[2][1]) if len(bounds) >= 3 else 0
        self_cutoff_ms = int(bounds[1][1]) if len(bounds) >= 2 else 0
        return recent_ids, earliest_ms, self_cutoff_ms

    @staticmethod
    def _other_save(game: Optional[str], save_id: str, state: _Round) -> Optional[str]:
        """别的存档里的事不催她提起（与挂游戏的线索同口径）。"""
        return "别的存档" if place_of(game, save_id, state.saves) is SavePlace.OTHER else None

    @staticmethod
    def _skip(key: ItemKey, state: _Round, reason: Optional[str]) -> bool:
        """有原因就记下没推，返回 True。"""
        if reason is None:
            return False
        state.skipped.append(f"{_label(key)}（{reason}）")
        return True

    def _push(self, state: _Round, card: _Card) -> None:
        state.cards.append(card)
        self._push_at.setdefault(card.key, []).append(int(self._clock()))

    @staticmethod
    def _line_in_previous_session(live_session_id: int, state: _Round) -> bool:
        """档案行写在"上一场"才写"上一场"，其余写"之前"。"""
        return state.previous_session_id is not None and int(live_session_id) == int(state.previous_session_id)

    @staticmethod
    def _registered_in_previous_session(commitment: Any, previous_session_id: Optional[int]) -> bool:
        """承诺是不是登记在"上一场"：只有这一种写"上一场"，本场与更早的都写"之前"。"""
        session_id = getattr(commitment, "created_session_id", None)
        if session_id is None or previous_session_id is None:
            return False
        return int(session_id) == int(previous_session_id)

    async def _previous_session_id(self) -> Optional[int]:
        """上一场 = 最近一个已整理、有档案行的同类型场次（与记忆摘要同口径）。

        没注入回调或取不到时返回 None：承诺线索一律写"之前"，不把三场前的事说成上一场。
        """
        if self._previous_session_provider is None:
            return None
        try:
            value = await self._previous_session_provider()
        except Exception as exc:  # noqa: BLE001 - 取不到就按没有上一场处理，不挡线索推送
            logger.warning(f"线索推送取上一场失败（承诺线索时间词写「之前」）: {exc}", exc=exc)
            return None
        return int(value) if value is not None else None

    async def _load_commitment_progress(self, session_id: int) -> Dict[int, str]:
        """读取本场各未了结承诺的最新进展摘要（未注入更正仓储或查询异常时降级返回空）。"""
        if self._corrections is None:
            return {}
        try:
            if hasattr(self._corrections, "list_commitment_updates_by_session"):
                items = await self._corrections.list_commitment_updates_by_session(live_session_id=int(session_id))
            else:
                all_active = await self._corrections.list_active_by_session(live_session_id=int(session_id))
                items = [item for item in all_active if item.target_kind == "commitment"]
        except Exception as exc:  # noqa: BLE001 - 本场进展读不到不挡其余线索推送
            logger.warning(f"读本场承诺进展失败（线索暂不附进展注记）: {exc}", exc=exc)
            return {}
        progress: Dict[int, str] = {}
        for item in items:
            if item.verdict == "progress" and item.commitment_id is not None:
                cleaned = " ".join(str(item.summary or "").split())
                if cleaned:
                    progress[int(item.commitment_id)] = cleaned
        return progress

    async def _open_commitments(
        self,
        *,
        viewer_id: Optional[int] = None,
        game: Optional[str] = None,
        player_id: Optional[int] = None,
        limit: int = _MAX_COMMITMENTS_PER_VIEWER,
    ) -> List[Any]:
        """名下（某位观众、某位游戏玩家或某个游戏）的未了结承诺（新的在前）；查不到就当没有。"""
        try:
            return await self._commitments.list_commitments(
                statuses=["open"],
                viewer_id=viewer_id,
                game=game,
                target_player_id=player_id,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 - 查不到这条线索，不挡本批其余线索
            logger.warning(f"读未了结承诺失败（本批少一条线索）: {exc}", exc=exc)
            return []

    # ------------------------------------------------------------------
    # 老观众在场一行与遗忘回执
    # ------------------------------------------------------------------

    async def _presence_line(
        self,
        session_id: int,
        senders: Sequence[_Sender],
        carded_viewers: Set[int],
        *,
        replied: Optional[Set[int]] = None,
        recent_enters: Sequence[Tuple[str, str]] = (),
    ) -> str:
        """老观众在场一行：覆盖本批开口的老观众，以及只进房尚未开口的老观众（每场每人最多提示一次）。"""
        replied_ids = replied if replied is not None else await self._replied_viewer_ids(session_id)
        entries: List[Tuple[str, Optional[int], str, Sequence[str]]] = []
        active_cluster_ids: Set[int] = set()
        active_names_lower: Set[str] = set()
        for sender in senders:
            if sender.viewer_id is None:
                continue
            cluster_ids = sender.cluster_viewer_ids or (sender.viewer_id,)
            active_cluster_ids.update(cluster_ids)
            if sender.user_name:
                active_names_lower.add(sender.user_name.strip().lower())
            # 只跳过这一批拿了对 ta 本人承诺线索或老观众线索的观众；别人拿线索不压 ta 这一行
            if carded_viewers.intersection(cluster_ids):
                continue
            if sender.earlier_sessions is not None:
                earlier_sessions = sender.earlier_sessions
            else:
                earlier_sessions = sender.sessions_count
                if sender.last_session_id is not None and sender.last_session_id == session_id:
                    earlier_sessions -= 1
            if earlier_sessions < MIN_SESSIONS_FOR_PRESENCE and sender.familiarity_tier not in ("close", "regular"):
                continue
            if _forgetting(sender):
                continue
            if replied_ids.intersection(cluster_ids):
                continue
            entries.append(
                (sender.user_name or "观众", sender.prev_seen_ms, sender.familiarity_tier, sender.former_names)
            )

        entered_veterans = await self._resolve_entered_veterans(
            session_id=session_id,
            recent_enters=recent_enters,
            replied=replied_ids,
            active_cluster_ids=active_cluster_ids,
            active_names_lower=active_names_lower,
        )
        return render_presence_line(entries, now_ms=int(self._clock()), entered_names=entered_veterans)

    async def _resolve_entered_veterans(
        self,
        *,
        session_id: int,
        recent_enters: Sequence[Tuple[str, str]],
        replied: Set[int],
        active_cluster_ids: Set[int],
        active_names_lower: Set[str],
    ) -> List[str]:
        """从近 10 分钟未欢迎进房名单里认出来过 ≥3 场的老观众，每场每人最多提示一次。"""
        if not recent_enters or self._viewers is None:
            return []
        resolver = getattr(self._viewers, "resolve_presence_stats", None)
        if not callable(resolver):
            return []
        entered_names: List[str] = []
        for raw_uid, raw_name in recent_enters:
            uid = (raw_uid or "").strip()
            uname = (raw_name or "").strip()
            if not uid and not uname:
                continue
            if uname and uname.lower() in active_names_lower:
                continue
            if uid and self._privacy is not None:
                try:
                    if await self._privacy.is_external_id_forgotten(
                        id_kind="account", external_id=uid
                    ) or await self._privacy.is_external_id_forgotten(id_kind="open", external_id=uid):
                        continue
                except Exception as exc:  # noqa: BLE001 - 查不了遗忘表时不报该进房人
                    logger.debug(f"核对进房观众遗忘名单失败（跳过提示）: {exc}", exc=exc)
                    continue
            try:
                stats = await resolver(
                    external_id=uid,
                    user_name=uname,
                    current_session_id=session_id,
                )
            except Exception as exc:  # noqa: BLE001 - 进房认人失败不挡线索
                logger.debug(f"核对进房观众场次数失败: {exc}", exc=exc)
                continue
            if stats is None:
                continue
            if stats.primary_viewer_id in self._announced_enter_viewers:
                continue
            if active_cluster_ids.intersection(stats.cluster_viewer_ids):
                continue
            if replied.intersection(stats.cluster_viewer_ids):
                continue
            if stats.forget_requested_at_ms is not None:
                continue
            if stats.earlier_sessions < MIN_SESSIONS_FOR_PRESENCE:
                continue
            shown = uname or stats.user_name
            if not shown:
                continue
            self._announced_enter_viewers.add(stats.primary_viewer_id)
            entered_names.append(shown)
        return entered_names

    def _command_notices(self, batch: Sequence[Any]) -> List[str]:
        """本批的遗忘回执：逐条看非模拟弹幕，不按发言人去重。

        同一批里老观众先发"哈哈"再发"忘了我"时，按人去重只剩第一条，回执就丢了——
        各条线索与老观众一行才需要按人去重。只认记账侧真正处理过的命令：记账侧只处理
        弹幕里的遗忘命令（超级留言、礼物、上舰里的文本不进遗忘流程），所以非弹幕消息
        一律不回执；撤回短语还要记账侧确认撤回已生效才回执。
        """
        if self._privacy is None:
            return []
        notices: List[str] = []
        for message in batch:
            if bool(getattr(message, "simulated", False)):
                continue
            user = getattr(message, "user", None)
            external_id = str(getattr(user, "id", "") or "").strip()
            if user is None or not external_id:
                continue
            message_id = str(getattr(message, "message_id", "") or "")
            content = str(getattr(message, "content", "") or "")
            if not message_id or not content:
                continue
            if str(getattr(message, "message_type", "") or "") != "danmaku":
                continue
            if self._privacy.is_forget_command(content):
                notices.append(render_forget_notice(message_id=message_id))
                continue
            if not self._privacy.is_retract_command(content):
                continue
            if self._privacy.was_retracted_in_session(
                message_id=message_id,
                platform=str(getattr(message, "platform", "") or ""),
                external_id=external_id,
            ):
                notices.append(render_retract_notice(message_id=message_id))
        return notices
