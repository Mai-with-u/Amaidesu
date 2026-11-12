"""游戏那边此刻：每个决策窗重建一块"游戏里正在怎样"，放在 Planner 参考段最后。

她常把游戏那边的状况说错（第 58 场把楼上有平台的房子说成"就一层"、说"花一直在包里"）：本轮新读的
现场夹在参考段中间，后面几十行已聊话题各带一份十几二十分钟前的状态快照，新读数被埋住、被反着说；
手头的事一段又看不到游戏那边此刻在做哪一步。这一块把此刻能核对的读数收到一处、放在参考段最后，
每窗重建、不进对话历史，下一窗整块换掉。只给 Planner，表达侧不看。

读数各有来处：手上那件事的状态与前段【我手头在游戏里做的事】同一口径（委派状态行：停着、账面多久
没进展都照写），委派原话只在前段写一遍；此刻那一步取任务账本里游戏 Agent 自己发起、还在跑的
最近一步的目的；最近几步结果取委派快照里游戏侧的进展备注；游戏那边的判断取游戏 Agent 每步说的
最近一句（它自己的推理，不一定对）；她在游戏公屏说过的话取她自己打出去的那几句。

这些读数是游戏那一面写给自己看的，常带坐标、编号和工具名（"贴着 (-354,71,57) 的北面补上屋面缺格"）：
这一块里一律过滤，坐标换成"那里"或去掉，编号与工具名去掉——她照着念出来，观众只听到一串数字。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Mapping, Optional, Tuple

from .planner_context import age_text
from .planner_render import Delegation, DelegationStatusProvider, delegation_state_line
from .term_sanitizer import sanitize_internal_terms

__all__ = ["AgentNow", "GameNow", "GameSideLog", "render_game_now", "scrub_game_readings"]

# 她在游戏公屏说过的话只看最近这么久、至多列几句、每句至多几个字
_SELF_CHAT_WINDOW_MS = 5 * 60 * 1000
_SELF_CHAT_LINES_MAX = 3
_SELF_CHAT_MAX_CHARS = 24
# 公屏话缓冲上限（条）：五分钟里她说不了这么多，只防无限增长
_SELF_CHAT_BUFFER = 20
# 手上那件事的状态、此刻那一步、每步结果、游戏那边的判断各自至多几个字：除现场速览外整块封顶约 600 token
# （现场速览按原样挪进来，第 58 场中位约 960 token，它的大小归游戏侧的看一眼工具管）
_STATE_MAX_CHARS = 40
_STEP_MAX_CHARS = 40
# 进展备注带"（这一步：……）"尾巴（交代这一步在干嘛）后 30 字经常把尾巴截掉，放宽到与状态行一致
_RESULT_MAX_CHARS = 40
_RESULTS_MAX = 3
_JUDGEMENT_MAX_CHARS = 48
# 游戏那边的判断超过这么久就不再展示（避免长单挂着 1 小时前的旧世界判断误导主播）
_JUDGEMENT_MAX_AGE_MS = 15 * 60 * 1000

# 委派状态行里带着最近一步结果时的开头：这一块下面另有"最近几步结果"一行，状态里不再重复那一步
_PROGRESS_STATE_PREFIX = "还在做，游戏那边有一步有结果了"

# 坐标：括号里两到三个整数（"(-354,71,57)" "(-352,56~57)" "（-353, 56/57）"），连同两边的空白
_COORDINATE = re.compile(r"\s*[（(]\s*-?\d+(?:\s*[,，]\s*-?\d+(?:\s*[~/～]\s*-?\d+)?){1,2}\s*[)）]\s*")
# 单轴写法："y=68" "x=-350" "z=56"
_AXIS = re.compile(r"\s*(?<![A-Za-z0-9_])[xyzXYZ]\s*[=＝]\s*-?\d+(?:\s*[~/～]\s*-?\d+)?\s*")
# 坐标前是这些字时换成"那里"（"苦力怕在那里""箱子在那里和那里"）；后面紧跟方位字或标点、句末时直接去掉（"地板上""缺格。"）
_PLACE_WORDS = ("在", "到", "往", "去", "回", "贴着", "靠着", "从", "靠近", "于", "朝", "和", "与", "或")
_DROP_BEFORE = set("上下里内外旁边前后，。；：、,.;:！？!?)）」\"'")
# UUID（图纸、存档、实体编号；大小写都有）
_UUID = re.compile(r"\s*[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
# 目标、任务编号的键值写法："goal_id=568" "task_id: deleg_1"
_ID_FIELD = re.compile(r"\s*(?<![A-Za-z0-9_])(?:goal|task)_id\s*[=:：]\s*[\w-]+")
# 工具调用写法："maicraft_goal(action='cancel')"：只认游戏那一面的工具前缀。别的"下划线名字 + 括号"多是
# 物品名带数量（"oak_planks (12 个)" "oak_log (16)"），照留；物品 ID（"minecraft:oak_log"）冒号后的不碰
_TOOL_CALL = re.compile(r"\s*(?<![A-Za-z0-9_:])(?:maicraft|minecraft)_[a-z0-9_]*\s*\([^()]*\)")
# 游戏那一面的能力、工具名（"maicraft_execute" "minecraft_glance"）：念给观众没有意义
_TOOL_NAME = re.compile(r"\s*(?<![A-Za-z0-9_:])(?:maicraft|minecraft)_[a-z][a-z0-9_]*")
# 观察编号：只去括号里"一个字母加数字"这种形状（"(e12)" "（b5）"），整个括号去掉。x、y、z 打头的多是数量
# （"铁锭(x3)"），纯数字、"N 个"是数量，都照留；括号外裸写的不碰——第 58 场的读数里一处也没有，
# 换掉只会误伤"v2 版"这类写法
_OBSERVATION_IN_BRACKETS = re.compile(r"\s*[（(][a-w]\d{1,4}[)）]")
# 去掉东西后剩下的空括号、悬空的顿号，以及汉字之间多出来的空白
_EMPTY_BRACKETS = re.compile(r"[（(][\s、，,]*[)）]")
_CJK_GAP = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")

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
    """游戏那边的流水：她自己在游戏公屏说的话。

    主播 Agent 的内部件：事件回调写入，决策窗开窗时读；场次边界（开播、下播）清空。游戏 Agent 最近的判断
    不在这里：它记在任务账本里那件委派的快照旁注上，主播每窗决策前读。
    """

    def __init__(self) -> None:
        self._self_chat: Deque[Tuple[int, str, str]] = deque(maxlen=_SELF_CHAT_BUFFER)

    def note_self_chat(self, game: str, message: str, at_ms: int) -> None:
        """她在游戏公屏打出去的一句。"""
        text = message.strip()
        if text:
            self._self_chat.append((at_ms, game, text))

    def recent_self_chat(self, current_ms: int) -> Tuple[Tuple[int, str, str], ...]:
        """她最近五分钟在游戏公屏说过的话（旧→新）。"""
        floor = current_ms - _SELF_CHAT_WINDOW_MS
        return tuple(item for item in self._self_chat if item[0] >= floor)

    def reset(self) -> None:
        """场次复位：上一场她的公屏话不带进下一场。"""
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
    notes = ((record or {}).get("snapshot") or {}).get("game_progress_notes") or []
    # 状态与前段【我手头在游戏里做的事】同一句（停着没动静、账面多久没进展都照写），委派原话不再写第二遍；
    # 状态里带着的那一步结果下面"最近几步结果"一行另有，这里只留开头
    state = delegation_state_line(delegation, status_provider, current_ms)
    if notes and state.startswith(_PROGRESS_STATE_PREFIX):
        state = f"{_PROGRESS_STATE_PREFIX}（见下面最近几步结果）。"
    rows = [f"- {game}手上那件事（原话见前面）：{_clip(scrub_game_readings(state), _STATE_MAX_CHARS)}"]
    # 此刻那一步只认这件事开始之后、还在跑的那一步：上一件事留下的步骤不算
    if reading.step and delegation.report_kind == "" and reading.step_at_ms >= delegation.at_ms:
        step = _clip(scrub_game_readings(reading.step), _STEP_MAX_CHARS)
        rows.append(f"  此刻那一步：{step}（{age_text(current_ms - reading.step_at_ms)}开始）")
    # 最近几步结果：游戏侧进展备注经脱敏换成中文结果（做完了 / 做了一半 / 没做成 / 取消了）
    results = [_clip(scrub_game_readings(str(note)), _RESULT_MAX_CHARS) for note in list(notes)[-_RESULTS_MAX:]]
    if results:
        rows.append("  最近几步结果：" + "；".join(results))
    # 游戏那边的判断：它自己的推理，可能看错，标明不一定对；这件事开始之前说的或过了保鲜期的旧句都不算
    judgement_age_ms = current_ms - reading.judgement_at_ms
    if reading.judgement and reading.judgement_at_ms >= delegation.at_ms and judgement_age_ms <= _JUDGEMENT_MAX_AGE_MS:
        first = next((line.strip() for line in reading.judgement.splitlines() if line.strip()), "")
        judgement = _clip(scrub_game_readings(first), _JUDGEMENT_MAX_CHARS)
        rows.append(f"  游戏那边的判断（不一定对）：{judgement}（{age_text(judgement_age_ms)}）")
    return rows


def _self_chat_row(lines: Tuple[Tuple[int, str, str], ...], current_ms: int) -> str:
    """她近五分钟在游戏公屏说过的话：至多最近三句，每句截短、带距今多久。"""
    recent = [item for item in lines if current_ms - item[0] <= _SELF_CHAT_WINDOW_MS][-_SELF_CHAT_LINES_MAX:]
    if not recent:
        return ""
    said = "；".join(
        f"「{_clip(scrub_game_readings(text), _SELF_CHAT_MAX_CHARS)}」（{age_text(current_ms - at_ms)}）"
        for at_ms, _game, text in recent
    )
    return f"- 我近 5 分钟在游戏公屏说过：{said}"


def _clip(text: str, limit: int) -> str:
    """截到至多 ``limit`` 个字，截掉时补省略号；换行折成空格（每项一行）。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def scrub_game_readings(text: str) -> str:
    """游戏那一面写给自己看的读数 → 她能直接看懂、不会照念出数字的说法。

    先过通用的内部术语脱敏（目标编号、能力名、任务号），再去掉坐标（前面是"在、到、回"这类字时换成
    "那里"，后面紧跟方位字、标点或句末时直接去掉，其余换成"那里"）、单轴写法（"y=68"）、UUID、
    目标编号的键值写法、游戏那一面的工具调用与工具名、括号里的观察编号，最后收拾空括号与汉字之间多出来的
    空白。数量（"(12 个)" "(16)" "(x3)"）、物品名、版本号照留。
    """
    cleaned = sanitize_internal_terms(text or "")
    for pattern in (_COORDINATE, _AXIS):
        cleaned = pattern.sub(_place_or_drop, cleaned)
    for pattern in (_UUID, _ID_FIELD, _TOOL_CALL, _TOOL_NAME, _OBSERVATION_IN_BRACKETS):
        cleaned = pattern.sub("", cleaned)
    cleaned = _EMPTY_BRACKETS.sub("", cleaned)
    return _CJK_GAP.sub("", " ".join(cleaned.split()))


def _place_or_drop(match: "re.Match[str]") -> str:
    """一处坐标换成"那里"还是直接去掉：看前面是不是"在、到"这类字、后面是不是方位字或标点。"""
    before = match.string[: match.start()].rstrip()
    after = match.string[match.end() :]
    if before.endswith(_PLACE_WORDS):
        return "那里"
    if not after or after[0] in _DROP_BEFORE:
        # 两边都是字母数字时留一个空格，免得前后两个英文词粘在一起
        return " " if before[-1:].isascii() and before[-1:].isalnum() and after[:1].isalnum() else ""
    return "那里"
