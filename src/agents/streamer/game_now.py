"""游戏那边此刻：每个决策窗重建一块"游戏里正在怎样"，放在 Planner 参考段最后。

她常把游戏那边的状况说错（第 58 场把楼上有平台的房子说成"就一层"、说"花一直在包里"）：本轮新读的
现场夹在参考段中间，后面几十行已聊话题各带一份十几二十分钟前的状态快照，新读数被埋住、被反着说；
手头的事一段又看不到游戏那边此刻在做哪一步。这一块把此刻能核对的读数收到一处、放在参考段最后，
每窗重建、不进对话历史，下一窗整块换掉。只给 Planner，表达侧不看。

读数各有来处：手上的事取 Planner 的委派账；此刻那一步取任务账本里游戏 Agent 自己发起、还在跑的
最近一步的目的；最近几步结果取委派快照里游戏侧的进展备注；游戏那边的判断取游戏 Agent 每步说的
最近一句（它自己的推理，不一定对）；她在游戏公屏说过的话取她自己打出去的那几句。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Mapping, Optional, Tuple

from .planner_context import age_text
from .planner_render import Delegation, DelegationStatusProvider
from .term_sanitizer import sanitize_internal_terms

__all__ = ["AgentNow", "GameNow", "GameSideLog", "render_game_now"]

# 她在游戏公屏说过的话只看最近这么久、至多列几句、每句至多几个字
_SELF_CHAT_WINDOW_MS = 5 * 60 * 1000
_SELF_CHAT_LINES_MAX = 3
_SELF_CHAT_MAX_CHARS = 24
# 公屏话缓冲上限（条）：五分钟里她说不了这么多，只防无限增长
_SELF_CHAT_BUFFER = 20
# 手上的事、此刻那一步、每步结果、游戏那边的判断各自至多几个字：除现场速览外整块封顶约 600 token
# （现场速览按原样挪进来，第 58 场中位约 960 token，它的大小归游戏侧的看一眼工具管）
_TASK_MAX_CHARS = 40
_STEP_MAX_CHARS = 40
_RESULT_MAX_CHARS = 30
_RESULTS_MAX = 3
_JUDGEMENT_MAX_CHARS = 48

_HEADING = "【游戏那边此刻】（每轮重读；游戏里做到哪、背包里有什么、房子什么样，以这一块为准）"
_OVERVIEW_HEADING = "【现场速览（本轮决策前自动读取）】"


@dataclass(frozen=True)
class AgentNow:
    """一个游戏 Agent 此刻的读数：还在跑的最近一步的目的、它最近说的一句判断（各带时刻；没有时为空串）。"""

    step: str = ""
    step_at_ms: int = 0
    judgement: str = ""
    judgement_at_ms: int = 0


@dataclass(frozen=True)
class GameNow:
    """游戏那边此刻：各游戏 Agent 的读数（按 Agent 注册名），加她最近在游戏公屏说过的话（时刻, 游戏, 原话；旧→新）。"""

    agents: Mapping[str, AgentNow] = field(default_factory=dict)
    self_chat: Tuple[Tuple[int, str, str], ...] = ()


class GameSideLog:
    """游戏那边的两样流水：游戏 Agent 每步说的判断（每个 Agent 只留最近一句）和她自己在游戏公屏说的话。

    主播 Agent 的内部件：事件回调写入，决策窗开窗时读；场次边界（开播、下播）清空。
    """

    def __init__(self) -> None:
        self._judgements: Dict[str, Tuple[str, int]] = {}
        self._self_chat: Deque[Tuple[int, str, str]] = deque(maxlen=_SELF_CHAT_BUFFER)

    def note_judgement(self, agent: str, content: str, at_ms: int) -> None:
        """游戏 Agent 这一步说的话：盖掉它上一句（只关心它最近怎么想）。"""
        text = content.strip()
        if agent and text:
            self._judgements[agent] = (text, at_ms)

    def note_self_chat(self, game: str, message: str, at_ms: int) -> None:
        """她在游戏公屏打出去的一句。"""
        text = message.strip()
        if text:
            self._self_chat.append((at_ms, game, text))

    def judgement(self, agent: str) -> Tuple[str, int]:
        """这个游戏 Agent 最近说的一句判断与时刻；没说过为 ("", 0)。"""
        return self._judgements.get(agent, ("", 0))

    def recent_self_chat(self, current_ms: int) -> Tuple[Tuple[int, str, str], ...]:
        """她最近五分钟在游戏公屏说过的话（旧→新）。"""
        floor = current_ms - _SELF_CHAT_WINDOW_MS
        return tuple(item for item in self._self_chat if item[0] >= floor)

    def reset(self) -> None:
        """场次复位：上一场游戏那边的判断与她的公屏话不带进下一场。"""
        self._judgements.clear()
        self._self_chat.clear()


def render_game_now(
    delegations: Mapping[str, Delegation],
    status_provider: Optional[DelegationStatusProvider],
    now: GameNow,
    overview: str,
    current_ms: int,
) -> str:
    """拼【游戏那边此刻】一块：每件手上的事一组读数，她近五分钟的公屏话，最后是现场速览。

    什么都没有（没在玩、没委派、没读到现场）时为空串，整块省略。
    """
    rows: List[str] = []
    for delegation in delegations.values():
        reading = now.agents.get(delegation.agent, AgentNow())
        rows.extend(_delegation_rows(delegation, status_provider, reading, current_ms))
    chat = _self_chat_row(now.self_chat, current_ms)
    if chat:
        rows.append(chat)
    if not rows and not overview:
        return ""
    parts = [_HEADING, *rows]
    if overview:
        # 原来夹在参考段中间的现场速览挪到这一块最后：新读的现场排在最近的位置，不被旧快照盖住
        parts.append(f"{_OVERVIEW_HEADING}\n{overview}")
    return "\n".join(parts)


def _delegation_rows(
    delegation: Delegation,
    status_provider: Optional[DelegationStatusProvider],
    reading: AgentNow,
    current_ms: int,
) -> List[str]:
    """一件手上的事的几行读数：做什么、做到哪一步、最近几步的结果、游戏那边怎么想。"""
    record = status_provider(delegation.task_id) if status_provider is not None and delegation.task_id else None
    game = f"在 {delegation.agent} 里" if delegation.agent else "在游戏里"
    task = _clip(sanitize_internal_terms(delegation.instruction), _TASK_MAX_CHARS)
    rows = [f"- {game}手上的事：{task}（{_task_state(delegation, record, status_provider)}）"]
    # 此刻那一步只认这件事开始之后、还在跑的那一步：上一件事留下的步骤不算
    if reading.step and delegation.report_kind == "" and reading.step_at_ms >= delegation.at_ms:
        step = _clip(sanitize_internal_terms(reading.step), _STEP_MAX_CHARS)
        rows.append(f"  此刻那一步：{step}（{age_text(current_ms - reading.step_at_ms)}开始）")
    # 最近几步结果：游戏侧进展备注经脱敏换成中文结果（做完了 / 做了一半 / 没做成 / 取消了）
    notes = ((record or {}).get("snapshot") or {}).get("game_progress_notes") or []
    results = [_clip(sanitize_internal_terms(str(note)), _RESULT_MAX_CHARS) for note in list(notes)[-_RESULTS_MAX:]]
    if results:
        rows.append("  最近几步结果：" + "；".join(results))
    # 游戏那边的判断：它自己的推理，可能看错，标明不一定对；这件事开始之前说的不算
    if reading.judgement and reading.judgement_at_ms >= delegation.at_ms:
        first = next((line.strip() for line in reading.judgement.splitlines() if line.strip()), "")
        judgement = _clip(sanitize_internal_terms(first), _JUDGEMENT_MAX_CHARS)
        rows.append(f"  游戏那边的判断（不一定对）：{judgement}（{age_text(current_ms - reading.judgement_at_ms)}）")
    return rows


def _task_state(
    delegation: Delegation,
    record: Optional[Dict[str, Any]],
    status_provider: Optional[DelegationStatusProvider],
) -> str:
    """手上的事的一句状态：做完了、卡住了等我拿主意、停了没交付、还在做。"""
    if delegation.report_kind == "delivery":
        return "做完了"
    if delegation.report_kind == "escalation":
        return "卡住了，等我拿主意"
    if delegation.report_kind == "undelivered":
        return "停了，没交付结果"
    if record is None:
        # 没接任务账本的装配只知道派出去了；接了却查不到是账面已了结
        return "还在做" if status_provider is None else "账面上已了结"
    if record.get("status") == "waiting_for_decision":
        return "停着等我拿主意"
    return "还在做"


def _self_chat_row(lines: Tuple[Tuple[int, str, str], ...], current_ms: int) -> str:
    """她近五分钟在游戏公屏说过的话：至多最近三句，每句截短、带距今多久。"""
    recent = [item for item in lines if current_ms - item[0] <= _SELF_CHAT_WINDOW_MS][-_SELF_CHAT_LINES_MAX:]
    if not recent:
        return ""
    said = "；".join(
        f"「{_clip(sanitize_internal_terms(text), _SELF_CHAT_MAX_CHARS)}」（{age_text(current_ms - at_ms)}）"
        for at_ms, _game, text in recent
    )
    return f"- 我近 5 分钟在游戏公屏说过：{said}"


def _clip(text: str, limit: int) -> str:
    """截到至多 ``limit`` 个字，截掉时补省略号；换行折成空格（每项一行）。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"
