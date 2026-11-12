"""开播【上次收尾】（D2-2）：用确定性账面数据拼出上一正式场收工时的实况，不依赖下播整理。

只在开播头两窗（或到本场第一件委派受理为止）注入参考段，不改动系统提示词前缀缓存：
- 上一场最后在哪个存档（对比眼下所在存档）；
- 下播时还在做、待办或搁下的委派及状态；
- 下播前最后播出的几句台词；
- 上一场已整理完成时，再附上那一场新登记的承诺；还没整理完则明确注明"上一场的事还没整理，下面只有账面记录"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Mapping, Optional, Sequence, Tuple

from src.modules.logging import get_logger
from src.modules.storage.models.events import DelegationRecord, GameSaveSpan
from src.modules.storage.models.memory import Commitment
from src.modules.types.game_save import GameSave
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

from .render import render_commitment_number
from .saves import SavePlace, place_of

if TYPE_CHECKING:
    from src.modules.storage.repos import ChatRepo, CommitmentRepo, EventRepo, SessionRepo

__all__ = [
    "LAST_SPEECH_MAX_LINES",
    "LAST_WRAPUP_MAX_COMMITMENTS",
    "LAST_WRAPUP_MAX_DELEGATIONS",
    "OPENING_WRAPUP_MAX_WINDOWS",
    "UNSETTLED_PREVIOUS_NOTE",
    "LastWrapupResult",
    "build_last_wrapup",
    "classify_ending_delegation",
]

logger = get_logger("LastWrapup")

# 开播至多在前几个决策窗注入【上次收尾】（本场受理第一件委派后立即提前停注）
OPENING_WRAPUP_MAX_WINDOWS = 2
# 上一场收尾最后几句台词上限与单句字符上限
LAST_SPEECH_MAX_LINES = 3
_SPEECH_CLIP_CHARS = 80
# 下播时未完委派上限与指令字符上限
LAST_WRAPUP_MAX_DELEGATIONS = 6
_DELEGATION_CLIP_CHARS = 60
# 上一场新登记承诺展示上限
LAST_WRAPUP_MAX_COMMITMENTS = 5

_WRAPUP_HEADER = "【上次收尾】（上一场结束时的账面实况，开场接话参考；做不做看眼前）"
UNSETTLED_PREVIOUS_NOTE = "上一场的事还没整理，下面只有账面记录。"
_AUTO_CLOSE_PREFIX = "下播自动收尾"
_PLANNER_INITIATOR = "planner-react"


@dataclass(frozen=True)
class LastWrapupResult:
    """开播【上次收尾】成品：参考段文本与其中标了编号的承诺主键（供 Planner 放行点名）。"""

    text: str = ""
    commitment_ids: Tuple[int, ...] = ()
    previous_session_id: Optional[int] = None
    settled: bool = False


def _clip(text: str, limit: int) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def classify_ending_delegation(record: DelegationRecord) -> Optional[str]:
    """判定一条委派在上一场结束时是否处于"在做 / 待办 / 搁下"，返回给人看的状态词；已结单返回 None。

    覆盖三类账面落法：
    1. 下播结算写成 ``cancelled``，终态摘要以 ``"下播自动收尾"`` 开头（含 D1 细分的"当时在做"
       "待办，还没开工""待办，搁下了，做到：……"，以及旧版无括号的"下播自动收尾"）；
    2. 进程异常中断收尾写成 ``interrupted``；
    3. 账面仍停在非终态（``accepted`` / ``running`` / ``waiting_for_decision`` / ``queued``）。
    """
    status = (record.status or "").strip()
    summary = " ".join((record.summary or "").split())
    if status in ("accepted", "running", "waiting_for_decision"):
        return "当时在做"
    if status == "queued":
        return "待办"
    if status == "interrupted":
        return "当时在做，被重启打断"
    if status == "cancelled" and summary.startswith(_AUTO_CLOSE_PREFIX):
        tail = summary[len(_AUTO_CLOSE_PREFIX) :].strip()
        if (tail.startswith("（") and tail.endswith("）")) or (tail.startswith("(") and tail.endswith(")")):
            inner = tail[1:-1].strip()
            if inner:
                return _clip(inner, _DELEGATION_CLIP_CHARS)
        return "下播时未做完"
    return None


def _render_last_saves(spans: Sequence[GameSaveSpan], current_saves: Mapping[str, GameSave]) -> List[str]:
    """每个游戏取上一场最后待过的那一段存档，说明和眼下所在存档是不是同一个世界。"""
    last_by_game: Dict[str, GameSaveSpan] = {}
    for span in spans:
        if span.game and span.save_id:
            last_by_game[span.game] = span
    lines: List[str] = []
    for game, span in last_by_game.items():
        label = GameSave(id=span.save_id, name=span.save_name).label
        place = place_of(game, span.save_id, current_saves)
        now = current_saves.get(game)
        if place is SavePlace.SAME:
            live_label = now.label if now is not None and now.name else label
            lines.append(f"- 上次最后在 {game}「{live_label}」（就是眼下这个存档）")
        elif place is SavePlace.OTHER:
            if now is not None and not now.known:
                lines.append(f"- 上次最后在 {game}「{label}」（{game} 眼下还没进存档）")
            elif now is not None:
                lines.append(f"- 上次最后在 {game}「{label}」（现在开着「{now.label}」，不是同一个世界）")
            else:
                lines.append(f"- 上次最后在 {game}「{label}」（不是眼下这个存档）")
        else:
            lines.append(f"- 上次最后在 {game}「{label}」")
    return lines


def _render_ending_delegations(records: Sequence[DelegationRecord]) -> List[str]:
    """筛选主播自己派的、下播时还在做或待办的委派，按受理先后列出。"""
    picked: List[Tuple[str, str, str]] = []
    for record in records:
        if record.initiator != _PLANNER_INITIATOR:
            continue
        instruction = _clip(record.instruction, _DELEGATION_CLIP_CHARS)
        if not instruction:
            continue
        state_text = classify_ending_delegation(record)
        if state_text is None:
            continue
        picked.append((record.game, state_text, instruction))
    return [
        f"- 下播时未完的游戏活（{game}·{state}）：{instruction}"
        for game, state, instruction in picked[:LAST_WRAPUP_MAX_DELEGATIONS]
    ]


def _render_new_commitments(
    rows: Sequence[Commitment],
    *,
    previous_session_id: int,
    current_saves: Mapping[str, GameSave],
) -> Tuple[List[str], List[int]]:
    """上一场已整理时，列出那一场新登记、不针对具体个人且非别的存档的未了结承诺。"""
    matched = [
        row
        for row in rows
        if row.created_session_id is not None
        and int(row.created_session_id) == int(previous_session_id)
        and not row.has_target
        and place_of(row.game, row.save_id, current_saves) is not SavePlace.OTHER
    ]
    matched.reverse()
    lines: List[str] = []
    ids: List[int] = []
    for item in matched[:LAST_WRAPUP_MAX_COMMITMENTS]:
        quote = (item.quote or "").strip()
        what = (item.what or "").strip()
        if not quote and not what:
            continue
        ref = render_commitment_number(item.id)
        text = f"「{_clip(quote, _SPEECH_CLIP_CHARS)}」" if quote else what
        lines.append(f"- 上一场答应过：[{ref}] {text}")
        ids.append(int(item.id))
    return lines, ids


async def build_last_wrapup(
    *,
    sessions_repo: "SessionRepo",
    chat_repo: Optional["ChatRepo"] = None,
    event_repo: Optional["EventRepo"] = None,
    commitments_repo: Optional["CommitmentRepo"] = None,
    kind: SessionKind = SESSION_KIND_LIVE,
    min_session_ms: int = 0,
    live_session_id: Optional[int] = None,
    current_saves: Optional[Mapping[str, GameSave]] = None,
) -> LastWrapupResult:
    """用确定性账面数据拼出上一场的【上次收尾】；没有上一场或无内容时返回空结果。"""
    try:
        prev = await sessions_repo.latest_ended_session(
            kind=kind,
            min_duration_ms=min_session_ms,
            exclude_session_id=live_session_id,
        )
    except Exception as exc:  # noqa: BLE001 - 查不到上一场只省掉开场收尾块，不挡决策
        logger.warning(f"查上一正式场失败（省略【上次收尾】）: {exc}", exc=exc)
        return LastWrapupResult()
    if prev is None:
        return LastWrapupResult()

    prev_id = int(prev.id)
    settled = prev.settled_at_ms is not None
    saves = dict(current_saves or {})
    body: List[str] = []
    if not settled:
        body.append(UNSETTLED_PREVIOUS_NOTE)

    if event_repo is not None:
        try:
            spans = await event_repo.list_session_save_spans(live_session_id=prev_id)
            body.extend(_render_last_saves(spans, saves))
        except Exception as exc:  # noqa: BLE001 - 单项读取失败不拖垮其余收尾项
            logger.warning(f"读上一场 #{prev_id} 存档段失败（跳过存档行）: {exc}", exc=exc)
        try:
            delegations = await event_repo.list_session_delegations(live_session_id=prev_id)
            body.extend(_render_ending_delegations(delegations))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读上一场 #{prev_id} 委派账失败（跳过委派行）: {exc}", exc=exc)

    if chat_repo is not None:
        try:
            speech = await chat_repo.list_session_speech_for_settle(live_session_id=prev_id)
            delivered = [
                _clip(line.content, _SPEECH_CLIP_CHARS)
                for line in speech
                if line.dropped_at_ms is None and (line.content or "").strip()
            ]
            tail = delivered[-LAST_SPEECH_MAX_LINES:]
            if tail:
                joined = " / ".join(f"「{item}」" for item in tail)
                body.append(f"- 上次收尾最后说的几句：{joined}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读上一场 #{prev_id} 最后几句台词失败（跳过台词行）: {exc}", exc=exc)

    commitment_ids: List[int] = []
    if settled and commitments_repo is not None:
        try:
            open_rows = await commitments_repo.list_commitments(statuses=["open"], limit=100)
            promise_lines, commitment_ids = _render_new_commitments(
                open_rows, previous_session_id=prev_id, current_saves=saves
            )
            body.extend(promise_lines)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读上一场 #{prev_id} 新承诺失败（跳过承诺行）: {exc}", exc=exc)

    # 若上一场已整理且四项确定性账面全空（如空场），整段省略不刷空抬头
    if settled and not body:
        return LastWrapupResult(previous_session_id=prev_id, settled=True)

    return LastWrapupResult(
        text="\n".join([_WRAPUP_HEADER, *body]),
        commitment_ids=tuple(commitment_ids),
        previous_session_id=prev_id,
        settled=settled,
    )
