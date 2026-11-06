"""线索推送：每批 Planner 调用前，按线索找出这一批该想起来的事。

线索按优先级取，每批至多三张卡：

1. 对象开口：本批发言人按外部 ID 精确解析出观众编号（只读，不建观众；有待执行删除请求
   的不算），名下有未了结承诺 → 推那张对 ta 的承诺卡，只给动作标题；
2. 游戏地点（被动想起）：角色从远处走到某个记住的地点跟前 → 推挂在这个地点上的档案行；
3. 规则（被动想起）：本批观众弹幕或她最近 3 句台词提到了档案行的触发词或老梗 → 推那一条；
4. 游戏开着：主播当前开着某个游戏，有挂这个游戏、不针对具体观众的未了结承诺 → 推游戏卡。
   分存档的游戏只推眼下这个存档里答应的（别的存档里的事不催她提起），没记存档的照推、
   卡面注明不确定；对某位观众的卡照推，注明是在哪个存档里答应的（判定见 ``saves``）。
5. 向量（被动想起）：本批观众弹幕或游戏情境的意思和某行档案、某条不针对具体观众的承诺相近 → 推那一条。
   弹幕一到就在后台算查询向量（``prefetch``），这一批组装时算好了就用，没算好这一批不用，决策不等它；
   只发弹幕正文，不发昵称和外部 ID，发过"忘了我"的观众的弹幕不发。

地点、规则、向量三路怎么找候选见 ``passive``；被动想起可在配置里整体关掉，向量一路也可单独关掉
（没配嵌入服务时自动不走）。

限频与用上即停：同一张卡每场至多推两次、两次至少隔十分钟（计数在内存里，进程重启从零
算）；本场台词的记忆引用里已经出现这张卡的编号（约#N 或 档#N），就是被用上了，本场不再推
——这一条每批查一次库，进程重启也不丢。被动想起另有两条：常驻简报里已经列着的条目不推；
只由她自己台词命中的（自触发）每条每场只推一次，免得她提一句、卡片推一张、她再接着说，
越说越多。每推一张卡记一行线索卡记录（只记条目编号和分数，不记观众身份），检测到她用上时
回填用上的时刻。

另外两种输出不是卡而是提示行：老观众在场一行（本批开口、本场之前来过至少三场正式场、
这一批没有承诺卡、她本场还没回应过、没有待删请求的观众合成一行），以及本批遗忘命令的
回执行。回执只认记账侧真正处理过的命令：弹幕里的「忘了我」记账侧一定受理；撤回短语要
记账侧确认那次撤回已经生效（``PrivacyService.was_retracted_in_session``）才回执，超级
留言/礼物里的文本一律不回执。三者合成一个线索块交给参考段；没有线索时整块省略。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.modules.logging import get_logger
from src.modules.storage.models.memory import NewCueLogEntry
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation
from src.modules.utils.time_utils import now_ms

from .brief import load_used_refs
from .passive import (
    SOURCE_DANMAKU,
    SOURCE_GAME,
    SOURCE_PLACE,
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
    query_worthy,
    vector_hits,
)
from .render import (
    render_archive_card,
    render_archive_number,
    render_commitment_number,
    render_cue_block,
    render_forget_notice,
    render_game_card,
    render_joke_card,
    render_presence_line,
    render_recalled_commitment_card,
    render_retract_notice,
    render_viewer_card,
)
from .saves import SavePlace, place_note, place_of

if TYPE_CHECKING:
    from src.modules.storage.models.memory import ArchiveLine, Commitment
    from src.modules.storage.privacy import PrivacyService

    from .search import MemorySearch
    from src.modules.storage.repos import ArchiveRepo, ChatRepo, CommitmentRepo, MemoryCueLogRepo, ViewerRepo

__all__ = [
    "MAX_CARDS_PER_BATCH",
    "MAX_PUSHES_PER_CARD",
    "MAX_SELF_PUSHES_PER_CARD",
    "MIN_SESSIONS_FOR_PRESENCE",
    "MemoryCueProvider",
    "MemoryCues",
]

logger = get_logger("MemoryCueProvider")

# 一批最多推几张卡、同一张卡每场最多推几次（只由她自己台词触发的只推一次）
MAX_CARDS_PER_BATCH = 3
MAX_PUSHES_PER_CARD = 2
MAX_SELF_PUSHES_PER_CARD = 1
# 同一张卡两次推送的最小间隔
PUSH_COOLDOWN_MS = 10 * 60 * 1000
# 老观众在场一行的门槛：本场之前来过至少三场正式场
MIN_SESSIONS_FOR_PRESENCE = 3
# 观众名下最多取几条未了结承诺（一位观众同时压着更多就不是这场该操心的事）
_MAX_COMMITMENTS_PER_VIEWER = 3
# 挂在一个游戏上的未了结承诺最多看几条：别的存档里答应的不推，要往后多看几条才找得到眼下这个存档的
_MAX_COMMITMENTS_PER_GAME = 20
# 规则一路看她最近几句播出的台词
RECENT_SPEECH_LINES = 3


@dataclass(frozen=True)
class MemoryCues:
    """一批的线索结果：拼好的参考段文本 + 本批出现的承诺与档案行编号（供点名校验）。"""

    text: str = ""
    commitment_ids: Tuple[int, ...] = ()
    card_count: int = 0
    # 卡片编号 → 对方称呼（对某位观众的卡才有；说话档要拿它说"答应过谁"）
    labels: Dict[int, str] = field(default_factory=dict)
    archive_ids: Tuple[int, ...] = ()


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
    forget_requested_at_ms: Optional[int] = None
    # 外部 ID 在不记录名单里：发过"忘了我"（建没建过档都算），或观众行已按要求删掉
    forget_listed: bool = False
    sessions_count: int = 0
    last_session_id: Optional[int] = None
    prev_seen_ms: Optional[int] = None


def _forgetting(sender: _Sender) -> bool:
    """发过"忘了我"的观众：观众行上有待删标记，或外部 ID 在不记录名单里。"""
    return sender.forget_requested_at_ms is not None or sender.forget_listed


@dataclass
class _Card:
    """这一批推出去的一张卡：卡面、条目、卡类，以及写进线索卡记录的来源与分数。"""

    key: ItemKey
    text: str
    kind: str
    sources: Tuple[str, ...] = ()
    detail: Dict[str, Any] = field(default_factory=dict)
    self_triggered: bool = False


@dataclass
class _Round:
    """一批线索的判定材料与结果：用过的、简报里列着的条目，眼下的存档，推了哪些、没推哪些。"""

    used: Set[int]
    used_archive: Set[int]
    brief_commitments: Set[int]
    brief_archive: Set[int]
    saves: Mapping[str, GameSave]
    previous_session_id: Optional[int]
    cards: List[_Card] = field(default_factory=list)
    carded_viewers: Set[int] = field(default_factory=set)
    skipped: List[str] = field(default_factory=list)

    @property
    def full(self) -> bool:
        return len(self.cards) >= MAX_CARDS_PER_BATCH

    def ids(self, kind: str) -> Tuple[int, ...]:
        return tuple(int(card.key[1]) for card in self.cards if card.key[0] == kind)


def _label(key: ItemKey) -> str:
    """日志里认条目用的写法：约#7 / 档#41 / 老梗「倒立洗头」。"""
    kind, value = key
    if kind == "commitment":
        return render_commitment_number(int(value))
    if kind == "archive":
        return render_archive_number(int(value))
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
    ) -> None:
        """仓储与时钟注入；隐私服务缺失时不出回执行（看不见遗忘命令）。

        ``previous_session_provider`` 给"上一场"的判定（承诺卡与档案卡的时间词）：缺省时
        一律写"之前"，不冒险把三场前的事说成上一场。``archive_repo`` 缺失时被动想起没有档案卡；
        ``cue_log_repo`` 缺失时只记日志；``passive_enabled`` 为假时被动想起三路都不跑；``search`` 缺失、
        没配嵌入服务或 ``vector_enabled`` 为假时向量一路不跑。
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
        self._pool = RulePool(archive_repo=archive_repo, inside_jokes=inside_jokes, clock=clock)
        self._arrivals = PlaceArrivals()
        self._situation = SituationQuery()
        self._session_id: Optional[int] = None
        # 条目 → 本场推送时刻列表
        self._push_at: Dict[ItemKey, List[int]] = {}
        # 本批推送的卡：编号 → 对方称呼（说话档据此说"答应过谁"）
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
    ) -> MemoryCues:
        """算一批的线索：卡片、老观众在场一行、回执行合成一个块；没有线索返回空串。

        ``current_saves`` 是分存档的游戏眼下所在的存档：游戏卡只推这个存档里答应的。
        ``situations`` 是各游戏角色离记住的地点多远（地点一路判到达用）；``brief_*_ids`` 是
        常驻简报里列着的条目，被动想起不再推它们；``game_tasks`` 是各游戏手上还在做的委派原话
        （游戏情境查询用）。
        """
        session_id = self._current_session_provider() if self._current_session_provider is not None else None
        if session_id is None:
            return MemoryCues()
        sid = int(session_id)
        self._reset_if_new_session(sid)
        senders = await self._resolve_senders(batch)
        used, used_archive = await load_used_refs(self._chat, sid)
        await self._mark_used(sid, used_archive, used)
        state = _Round(
            used=used,
            used_archive=used_archive,
            brief_commitments={int(item) for item in brief_commitment_ids},
            brief_archive={int(item) for item in brief_archive_ids},
            saves=dict(current_saves or {}),
            previous_session_id=await self._previous_session_id(),
        )
        self._round_labels = {}
        # 对象开口 -> 游戏地点 -> 规则 -> 游戏开着 -> 向量：每一路只在前面没凑满三张时补
        await self._viewer_cards(senders, state)
        if self._passive_enabled:
            await self._pool.refresh_if_stale()
            self._place_cards(situations or {}, state)
            self._rule_cards(self._pool.match(await self._rule_texts(sid, batch, senders)), state)
        await self._game_cards(open_games, state)
        if self._vector_on():
            self._situation.update(situations or {}, state.saves, game_tasks or {})
            await self._vector_cards(batch, senders, state)
        # 老观众一行不再因为"本批有别人的卡"整行消失；只跳过这一批拿了自己卡的观众
        presence = await self._presence_line(sid, senders, state.carded_viewers)
        notices = self._command_notices(batch)
        text = render_cue_block(cards=[card.text for card in state.cards], presence=presence, notices=notices)
        await self._record_round(sid, state)
        return MemoryCues(
            text=text,
            commitment_ids=state.ids("commitment"),
            card_count=len(state.cards),
            labels=dict(self._round_labels),
            archive_ids=state.ids("archive"),
        )

    def reset(self) -> None:
        """下播时清空本场状态（推送计数、到过的地点与回执追踪都只在本场内有效）。"""
        self._session_id = None
        self._push_at.clear()
        self._arrivals.reset()
        self._pool.reset()
        self._situation.reset()

    async def prefetch(self, message: Any) -> None:
        """一条弹幕到达就在后台算它的查询向量：攒批窗口几秒，组装线索时多半已经算好，决策不等它。

        只发弹幕正文。不在直播、向量一路没开、模拟消息、遗忘与撤回命令、太短的弹幕、发过"忘了我"
        的观众（删除执行没执行、建没建过档都算），一律不发。
        """
        if not self._vector_on() or self._search is None:
            return
        if self._current_session_provider is None or self._current_session_provider() is None:
            return
        content = self._query_text(message)
        if content is None or not query_worthy(content):
            return
        user = getattr(message, "user", None)
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

    async def _resolve_senders(self, batch: Sequence[Any]) -> List[_Sender]:
        """本批发言人解析成观众：模拟消息不解析，同一观众一批只算一次。"""
        senders: List[_Sender] = []
        seen: Set[int] = set()
        for message in batch:
            if bool(getattr(message, "simulated", False)):
                continue
            user = getattr(message, "user", None)
            external_id = str(getattr(user, "id", "") or "").strip()
            if user is None or not external_id:
                continue
            sender = _Sender(
                external_id=external_id,
                id_kind=str(getattr(user, "id_kind", "") or "account"),
                user_name=str(getattr(user, "name", "") or ""),
                content=str(getattr(message, "content", "") or ""),
                message_id=str(getattr(message, "message_id", "") or ""),
                message_type=str(getattr(message, "message_type", "") or ""),
                platform=str(getattr(message, "platform", "") or ""),
            )
            if self._viewers is not None:
                await self._fill_viewer(sender, platform=str(getattr(message, "platform", "") or ""))
            sender.forget_listed = await self._on_forget_list(sender)
            if sender.viewer_id is not None:
                if sender.viewer_id in seen:
                    continue
                seen.add(sender.viewer_id)
            senders.append(sender)
        return senders

    async def _fill_viewer(self, sender: _Sender, *, platform: str) -> None:
        """按外部 ID 只读解析观众（不建观众行）；解析失败按陌生人处理。"""
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
        sender.viewer_id = int(viewer_id)
        sender.user_name = row.user_name or sender.user_name
        sender.forget_requested_at_ms = row.forget_requested_at_ms
        sender.sessions_count = int(row.sessions_count)
        sender.last_session_id = int(row.last_session_id) if row.last_session_id is not None else None
        sender.prev_seen_ms = row.prev_seen_ms

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
        """本场已被点名用上的条目：回填线索卡记录里用上的时刻（只回填还没回填的）。"""
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
            logger.warning(f"回填线索卡用上时刻失败（下一批再试）: {exc}", exc=exc)

    async def _record_round(self, session_id: int, state: _Round) -> None:
        """这一批推了什么、没推什么记一行日志；推出去的卡写进线索卡记录。"""
        if state.cards:
            pushed = "、".join(
                f"{_label(card.key)}（{card.kind}，来源 {'+'.join(card.sources) or '无'}）" for card in state.cards
            )
            skipped = f"；没推 {'、'.join(state.skipped)}" if state.skipped else ""
            logger.info(f"线索推送：推了 {pushed}{skipped}")
        elif state.skipped:
            logger.debug(f"线索推送：没推 {'、'.join(state.skipped)}")
        if self._cue_log is None or not state.cards:
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
        try:
            await self._cue_log.insert_entries(entries=entries)
        except Exception as exc:  # noqa: BLE001 - 记录写不进去不挡推送，日志里仍看得到这一批
            logger.warning(f"写线索卡记录失败（这一批只留日志）: {exc}", exc=exc)

    # ------------------------------------------------------------------
    # 卡片
    # ------------------------------------------------------------------

    async def _viewer_cards(self, senders: Sequence[_Sender], state: _Round) -> None:
        """对象开口：本批开口的观众名下有未了结承诺，推一张对 ta 的卡（每人一张）。"""
        for sender in senders:
            if state.full:
                return
            if sender.viewer_id is None or sender.forget_requested_at_ms is not None:
                continue
            for commitment in await self._open_commitments(viewer_id=sender.viewer_id):
                key: ItemKey = ("commitment", int(commitment.id))
                if self._blocked(key, state) is not None:
                    continue
                text = render_viewer_card(
                    commitment,
                    viewer_label=sender.user_name or "观众",
                    message_id=sender.message_id,
                    is_previous=self._registered_in_previous_session(commitment, state.previous_session_id),
                    place=place_note(commitment.game, commitment.save_id, commitment.save_name, state.saves),
                )
                self._push(state, _Card(key=key, text=text, kind="viewer", sources=(SOURCE_DANMAKU,)))
                state.carded_viewers.add(int(sender.viewer_id))
                self._round_labels[int(commitment.id)] = sender.user_name or "观众"
                break

    async def _game_cards(self, open_games: Sequence[str], state: _Round) -> None:
        """游戏开着：挂在这个游戏上、不针对具体观众的未了结承诺，每个游戏一张。"""
        for game in open_games:
            if state.full:
                return
            for commitment in await self._open_commitments(game=game, limit=_MAX_COMMITMENTS_PER_GAME):
                if commitment.viewer_id is not None:
                    continue
                # 别的存档里答应的事不催她提起：眼前这个世界里没有那座房子
                place = place_of(game, commitment.save_id, state.saves)
                if place is SavePlace.OTHER:
                    continue
                key: ItemKey = ("commitment", int(commitment.id))
                if self._blocked(key, state) is not None:
                    continue
                text = render_game_card(commitment, game=game, unsure_save=place is SavePlace.UNSURE)
                self._push(state, _Card(key=key, text=text, kind="game", sources=(SOURCE_GAME,)))
                break

    def _place_cards(self, situations: Mapping[str, GameSituation], state: _Round) -> None:
        """游戏地点：这一批刚走到某个记住的地点跟前，推挂在那里的档案行。"""
        for game, place, distance in self._arrivals.update(situations, state.saves):
            for line in self._pool.lines_at(game, place, state.saves):
                key: ItemKey = ("archive", int(line.id))
                if self._skip(key, state, self._passive_blocked(key, state)):
                    continue
                # 地点卡用提示语气，不替她断言眼下在哪：位置可能是几分钟前读到的，存档也只说"记录上"
                same_save = place_of(line.game, line.save_id, state.saves) is SavePlace.SAME
                text = render_archive_card(
                    line,
                    why=f"游戏里的位置显示你可能走到了「{place}」附近",
                    is_previous=self._line_in_previous_session(line.live_session_id, state),
                    note="记录上和眼下是同一个存档" if same_save else "",
                )
                # 线索卡记录不存地点名：复盘时从档案行眼下挂的地点现查，行删了就看不到
                detail = {"distance": distance}
                self._push(state, _Card(key=key, text=text, kind="place", sources=(SOURCE_PLACE,), detail=detail))

    def _rule_cards(self, hits: Sequence[PassiveHit], state: _Round) -> None:
        """规则：弹幕或她最近的台词提到了触发词；只由她自己台词命中的每条每场只推一次。"""
        for hit in hits:
            limit = MAX_SELF_PUSHES_PER_CARD if hit.self_only else MAX_PUSHES_PER_CARD
            if self._skip(hit.key, state, self._passive_blocked(hit.key, state, limit=limit)):
                continue
            text = self._rule_card_text(hit, state)
            if not text:
                continue
            kind = "joke" if hit.key[0] == "joke" else "rule"
            # 线索卡记录不存触发词原词（可能带观众昵称）：只存第几个触发词与匹配方式，复盘时从条目现查
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

    def _rule_card_text(self, hit: PassiveHit, state: _Round) -> str:
        """规则命中的卡面；条目不在了、或是别的存档里的事（不催她提起）时返回空串。"""
        kind, value = hit.key
        if kind == "joke":
            return render_joke_card(str(value), message_id=hit.message_id)
        keyword = hit.keywords[0] if hit.keywords else ""
        if SOURCE_DANMAKU in hit.sources:
            who = f"弹幕 {hit.message_id} " if hit.message_id else "观众"
            why = f"{who}提到「{keyword}」"
        else:
            why = f"你刚说到「{keyword}」"
        line = self._pool.archive_line(int(value))
        if line is None or self._skip(hit.key, state, self._other_save(line.game, line.save_id, state)):
            return ""
        return render_archive_card(
            line,
            why=why,
            is_previous=self._line_in_previous_session(line.live_session_id, state),
            note=place_note(line.game, line.save_id, line.save_name, state.saves),
        )

    async def _vector_cards(self, batch: Sequence[Any], senders: Sequence[_Sender], state: _Round) -> None:
        """向量：本批弹幕（最新几条）与变了的游戏情境各查一次，向量没算好的这一批跳过。"""
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
        for hit in vector_hits(results):
            if self._skip(hit.key, state, self._passive_blocked(hit.key, state)):
                continue
            text = await self._vector_card_text(hit, state)
            if not text:
                continue
            detail = {
                "cosine": round(hit.cosine or 0.0, 4),
                "rank": hit.vector_rank,
                "fused": round(hit.fused or 0.0, 5),
            }
            self._push(
                state, _Card(key=hit.key, text=text, kind="vector", sources=tuple(sorted(hit.sources)), detail=detail)
            )

    async def _vector_card_text(self, hit: PassiveHit, state: _Round) -> str:
        """向量命中的卡面；不是正式场的档案行、不能推的承诺、别的存档里的事返回空串。"""
        if SOURCE_DANMAKU in hit.sources:
            why = f"弹幕 {hit.message_id} 说的像这件事" if hit.message_id else "观众说的像这件事"
        else:
            why = "眼下游戏里的情境像这件事"
        kind, value = hit.key
        if kind == "archive":
            line = await self._cue_line(int(value))
            if line is None:
                self._skip(hit.key, state, "不是正式场的行")
                return ""
            if self._skip(hit.key, state, self._other_save(line.game, line.save_id, state)):
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
        if self._skip(hit.key, state, self._other_save(commitment.game, commitment.save_id, state)):
            return ""
        place = place_of(commitment.game, commitment.save_id, state.saves)
        return render_recalled_commitment_card(commitment, why=why, unsure_save=place is SavePlace.UNSURE)

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
        """向量命中的承诺：只推未了结、不针对具体观众的（别人说的像，推对某位观众的会把 ta 的事翻给全场）。"""
        try:
            commitment = await self._commitments.get_commitment(commitment_id=commitment_id)
        except Exception as exc:  # noqa: BLE001 - 读不到就不推
            logger.warning(f"读承诺 {commitment_id} 失败（不推）: {exc}", exc=exc)
            return None
        if commitment is None or commitment.status != "open" or commitment.viewer_id is not None:
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
        """被动想起另加两条：常驻简报里已经列着的不推；这一批已经凑满三张的不推。"""
        kind, value = key
        if (kind == "commitment" and int(value) in state.brief_commitments) or (
            kind == "archive" and int(value) in state.brief_archive
        ):
            return "已在简报"
        reason = self._blocked(key, state, limit=limit)
        if reason is None and state.full:
            return "每批上限"
        return reason

    @staticmethod
    def _other_save(game: Optional[str], save_id: str, state: _Round) -> Optional[str]:
        """别的存档里的事不催她提起（与游戏卡同口径）。"""
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
        """上一场 = 最近一个已整理、有档案行的同类型场次（与常驻简报同口径）。

        没注入回调或取不到时返回 None：承诺卡一律写"之前"，不把三场前的事说成上一场。
        """
        if self._previous_session_provider is None:
            return None
        try:
            value = await self._previous_session_provider()
        except Exception as exc:  # noqa: BLE001 - 取不到就按没有上一场处理，不挡线索推送
            logger.warning(f"线索推送取上一场失败（承诺卡时间词写「之前」）: {exc}", exc=exc)
            return None
        return int(value) if value is not None else None

    async def _open_commitments(
        self,
        *,
        viewer_id: Optional[int] = None,
        game: Optional[str] = None,
        limit: int = _MAX_COMMITMENTS_PER_VIEWER,
    ) -> List[Any]:
        """名下的未了结承诺（新的在前）；查不到就当没有。"""
        try:
            return await self._commitments.list_commitments(
                statuses=["open"],
                viewer_id=viewer_id,
                game=game,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001 - 查不到这张卡，不挡本批其余线索
            logger.warning(f"读未了结承诺失败（本批少一张卡）: {exc}", exc=exc)
            return []

    # ------------------------------------------------------------------
    # 老观众在场一行与回执行
    # ------------------------------------------------------------------

    async def _presence_line(
        self,
        session_id: int,
        senders: Sequence[_Sender],
        carded_viewers: Set[int],
    ) -> str:
        """老观众在场一行：本场之前来过至少三场正式场、她本场还没回应过、没有待删请求的观众。

        ``sessions_count`` 是含本场的累计（观众本场首条弹幕进账时 +1）：这位观众上次计入
        的场次就是本场时，先减掉本场再比门槛——否则回访第 3 场的人会被当成老观众。
        这一批拿到对 ta 本人承诺卡的观众不进行：同一批里说两遍同一件事；别人拿卡不压 ta。
        """
        replied = await self._replied_viewer_ids(session_id)
        entries: List[Tuple[str, Optional[int]]] = []
        for sender in senders:
            if sender.viewer_id is None:
                continue
            # 只跳过这一批拿了对 ta 本人承诺卡的观众；别人拿卡不压 ta 这一行
            if sender.viewer_id in carded_viewers:
                continue
            earlier_sessions = sender.sessions_count
            if sender.last_session_id is not None and sender.last_session_id == session_id:
                earlier_sessions -= 1
            if earlier_sessions < MIN_SESSIONS_FOR_PRESENCE:
                continue
            if sender.forget_requested_at_ms is not None:
                continue
            if sender.viewer_id in replied:
                continue
            entries.append((sender.user_name or "观众", sender.prev_seen_ms))
        return render_presence_line(entries, now_ms=int(self._clock()))

    def _command_notices(self, batch: Sequence[Any]) -> List[str]:
        """本批遗忘命令的回执行：逐条看非模拟弹幕，不按发言人去重。

        同一批里老观众先发"哈哈"再发"忘了我"时，按人去重只剩第一条，回执就丢了——
        卡片与老观众一行才需要按人去重。只认记账侧真正处理过的命令：记账侧只处理
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
