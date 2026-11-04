"""线索推送：每批 Planner 调用前，按线索找出这一批该想起来的事。

线索只有两种（按优先级取，每批至多三张卡）：

1. 对象开口：本批发言人按外部 ID 精确解析出观众编号（只读，不建观众；有待执行删除请求
   的不算），名下有未了结承诺 → 推那张对 ta 的承诺卡，只给动作标题；
2. 游戏开着：主播当前开着某个游戏，有挂这个游戏、不针对具体观众的未了结承诺 → 推游戏卡。
   分存档的游戏只推眼下这个存档里答应的（别的存档里的事不催她提起），没记存档的照推、
   卡面注明不确定；对某位观众的卡照推，注明是在哪个存档里答应的（判定见 ``saves``）。

限频与用上即停：同一张卡每场至多推两次、两次至少隔十分钟（计数在内存里，进程重启从零
算）；本场台词的记忆引用里已经出现这张卡的编号，就是被用上了，本场不再推——这一条每批
查一次库，进程重启也不丢。

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
from src.modules.types.game_save import GameSave
from src.modules.utils.time_utils import now_ms

from .brief import load_used_commitment_ids
from .render import (
    render_cue_block,
    render_forget_notice,
    render_game_card,
    render_presence_line,
    render_retract_notice,
    render_viewer_card,
)
from .saves import SavePlace, place_note, place_of

if TYPE_CHECKING:
    from src.modules.storage.privacy import PrivacyService
    from src.modules.storage.repos import ChatRepo, CommitmentRepo, ViewerRepo

__all__ = [
    "MAX_CARDS_PER_BATCH",
    "MAX_PUSHES_PER_CARD",
    "MIN_SESSIONS_FOR_PRESENCE",
    "MemoryCueProvider",
    "MemoryCues",
]

logger = get_logger("MemoryCueProvider")

# 一批最多推几张卡、同一张卡每场最多推几次
MAX_CARDS_PER_BATCH = 3
MAX_PUSHES_PER_CARD = 2
# 同一张卡两次推送的最小间隔
PUSH_COOLDOWN_MS = 10 * 60 * 1000
# 老观众在场一行的门槛：本场之前来过至少三场正式场
MIN_SESSIONS_FOR_PRESENCE = 3
# 观众名下最多取几条未了结承诺（一位观众同时压着更多就不是这场该操心的事）
_MAX_COMMITMENTS_PER_VIEWER = 3
# 挂在一个游戏上的未了结承诺最多看几条：别的存档里答应的不推，要往后多看几条才找得到眼下这个存档的
_MAX_COMMITMENTS_PER_GAME = 20


@dataclass(frozen=True)
class MemoryCues:
    """一批的线索结果：拼好的参考段文本 + 本批出现的承诺编号（供点名校验）。"""

    text: str = ""
    commitment_ids: Tuple[int, ...] = ()
    card_count: int = 0
    # 卡片编号 → 对方称呼（对某位观众的卡才有；说话档要拿它说"答应过谁"）
    labels: Dict[int, str] = field(default_factory=dict)


@dataclass
class _Sender:
    """本批发言人解析出的观众信息（解析不到观众时 viewer_id 为空）。"""

    external_id: str = ""
    user_name: str = ""
    content: str = ""
    message_id: str = ""
    message_type: str = ""
    platform: str = ""
    viewer_id: Optional[int] = None
    forget_requested_at_ms: Optional[int] = None
    sessions_count: int = 0
    last_session_id: Optional[int] = None
    prev_seen_ms: Optional[int] = None


class MemoryCueProvider:
    """按批计算线索；推送计数与回执追踪只活在本场，换场时清空。"""

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
    ) -> None:
        """仓储与时钟注入；隐私服务缺失时不出回执行（看不见遗忘命令）。

        ``previous_session_provider`` 给"上一场"的判定（承诺卡的时间词）：缺省时
        承诺卡一律写"之前"，不冒险把三场前的承诺说成上一场。
        """
        self._commitments = commitments_repo
        self._chat = chat_repo
        self._viewers = viewer_repo
        self._privacy = privacy_service
        self._current_session_provider = current_session_provider
        self._previous_session_provider = previous_session_provider
        self._clock = clock
        self._session_id: Optional[int] = None
        # 承诺编号 → 本场推送时刻列表
        self._push_at: dict[int, List[int]] = {}
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
    ) -> MemoryCues:
        """算一批的线索：卡片、老观众在场一行、回执行合成一个块；没有线索返回空串。

        ``current_saves`` 是分存档的游戏眼下所在的存档：游戏卡只推这个存档里答应的。
        """
        session_id = self._current_session_provider() if self._current_session_provider is not None else None
        if session_id is None:
            return MemoryCues()
        self._reset_if_new_session(int(session_id))
        senders = await self._resolve_senders(batch)
        used = await self._used_commitment_ids(int(session_id))
        previous_session_id = await self._previous_session_id()
        cards, card_ids, carded_viewers = await self._collect_cards(
            senders, used, open_games, previous_session_id, current_saves or {}
        )
        # 老观众一行不再因为"本批有别人的卡"整行消失；只跳过这一批拿了自己卡的观众
        presence = await self._presence_line(int(session_id), senders, carded_viewers)
        notices = self._command_notices(batch)
        text = render_cue_block(cards=cards, presence=presence, notices=notices)
        return MemoryCues(
            text=text,
            commitment_ids=tuple(card_ids),
            card_count=len(cards),
            labels=dict(self._round_labels),
        )

    def reset(self) -> None:
        """下播时清空本场状态（推送计数与回执追踪都只在本场内有效）。"""
        self._session_id = None
        self._push_at.clear()

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
                user_name=str(getattr(user, "name", "") or ""),
                content=str(getattr(message, "content", "") or ""),
                message_id=str(getattr(message, "message_id", "") or ""),
                message_type=str(getattr(message, "message_type", "") or ""),
                platform=str(getattr(message, "platform", "") or ""),
            )
            if self._viewers is not None:
                await self._fill_viewer(sender, platform=str(getattr(message, "platform", "") or ""))
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

    async def _used_commitment_ids(self, session_id: int) -> Set[int]:
        """本场台词已经点名引用过的承诺编号（用上即停；与常驻简报同一份口径）。"""
        return await load_used_commitment_ids(self._chat, session_id)

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

    # ------------------------------------------------------------------
    # 卡片
    # ------------------------------------------------------------------

    async def _collect_cards(
        self,
        senders: Sequence[_Sender],
        used: Set[int],
        open_games: Sequence[str],
        previous_session_id: Optional[int],
        current_saves: Mapping[str, GameSave],
    ) -> Tuple[List[str], List[int], Set[int]]:
        """按优先级凑卡片：先对象开口，再正在玩的游戏；每批至多三张。

        第三个返回值是本批拿到"对 ta 的承诺卡"的观众编号：老观众在场一行只跳过这些人。
        """
        cards: List[str] = []
        ids: List[int] = []
        carded_viewers: Set[int] = set()
        self._round_labels = {}
        for sender in senders:
            if len(cards) >= MAX_CARDS_PER_BATCH:
                return cards, ids, carded_viewers
            if sender.viewer_id is None or sender.forget_requested_at_ms is not None:
                continue
            for commitment in await self._open_commitments(viewer_id=sender.viewer_id):
                if not self._may_push(int(commitment.id), used):
                    continue
                cards.append(
                    render_viewer_card(
                        commitment,
                        viewer_label=sender.user_name or "观众",
                        message_id=sender.message_id,
                        is_previous=self._registered_in_previous_session(commitment, previous_session_id),
                        place=place_note(commitment.game, commitment.save_id, commitment.save_name, current_saves),
                    )
                )
                ids.append(int(commitment.id))
                carded_viewers.add(int(sender.viewer_id))
                self._round_labels[int(commitment.id)] = sender.user_name or "观众"
                self._record_push(int(commitment.id))
                break
        for game in open_games:
            if len(cards) >= MAX_CARDS_PER_BATCH:
                break
            for commitment in await self._open_commitments(game=game, limit=_MAX_COMMITMENTS_PER_GAME):
                if commitment.viewer_id is not None:
                    continue
                # 别的存档里答应的事不催她提起：眼前这个世界里没有那座房子
                place = place_of(game, commitment.save_id, current_saves)
                if place is SavePlace.OTHER:
                    continue
                if not self._may_push(int(commitment.id), used):
                    continue
                cards.append(render_game_card(commitment, game=game, unsure_save=place is SavePlace.UNSURE))
                ids.append(int(commitment.id))
                self._record_push(int(commitment.id))
                break
        return cards, ids, carded_viewers

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

    def _may_push(self, commitment_id: int, used: Set[int]) -> bool:
        """用上即停加限频：本场已用过、推满两次或没到间隔都不推。"""
        if commitment_id in used:
            return False
        pushes = self._push_at.get(int(commitment_id), [])
        if len(pushes) >= MAX_PUSHES_PER_CARD:
            return False
        if pushes and int(self._clock()) - pushes[-1] < PUSH_COOLDOWN_MS:
            return False
        return True

    def _record_push(self, commitment_id: int) -> None:
        self._push_at.setdefault(int(commitment_id), []).append(int(self._clock()))

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
