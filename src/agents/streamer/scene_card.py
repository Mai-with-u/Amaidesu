"""现场卡：嘴每次开口前读的一份事实快照。

离线对照实验里，只给人设、不给依据的"瘦嘴"会理直气壮地编现场（明明在外面逃命，却连说十分钟
"缩在屋里"），也会忘记直播进行到哪了；加一张几百字的现场卡、列出她自己刚说过的话之后，编造与
复读都压到了和现管线一样低。这张卡就是那份依据：

- 现在的状态：游戏提供的一句话现场（参考工具结果里的 ``brief``），带"几秒前看的"；
- 手上的目标：正在做的事和进展。目标和现状分开写——实验里瘦嘴把"退回屋内关好门"这条指令
  当成了已经发生的事，所以这一段明写"这是打算，不一定已经做到"；
- 刚发生的事、直播间、悬着的事（备忘录）、她刚说过的话、这次的由头。

本模块只按输入拼卡、按长度取舍，不读事件、不调 LLM；数据从哪来、多久刷新由调用方决定。
卡里不出现任何具体游戏的知识：游戏相关的白话化在各游戏 Agent 包里完成，这里只当成文字放进来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Sequence, Tuple

from .planner_context import age_text

__all__ = ["GoalLine", "SceneInputs", "brief_of", "build_scene_card"]

#: 现场信息过了这么久就提醒"可能已经变了"：嘴据此少下断言
STALE_SCENE_MS = 60_000

GoalStatus = Literal["进行中", "做完了", "卡住了"]


@dataclass(frozen=True)
class GoalLine:
    """手上的一件事：在哪个游戏里、原话、何时开始、进展到哪、进展是何时知道的。"""

    game: str
    instruction: str
    started_ms: int
    status: GoalStatus = "进行中"
    status_ms: int = 0


@dataclass(frozen=True)
class SceneInputs:
    """拼一张现场卡要的全部事实；空字段对应的段落直接省略。"""

    now_ms: int
    trigger: str = ""
    scene_brief: str = ""
    scene_brief_ms: int = 0
    goals: Sequence[GoalLine] = ()
    recent_events: Sequence[Tuple[int, str]] = ()
    segment_title: str = ""
    room_activity: str = ""
    notebook: str = ""
    recent_speeches: Sequence[str] = field(default_factory=tuple)


def brief_of(result: object) -> str:
    """参考工具结果里的一句话现场（``brief`` 字段）；没有就返回空串。

    这是给各游戏的通用约定：只读的现场工具在结果里带一句 ``brief`` 白话，主播的现场卡就放这一句；
    不带的工具不进卡——几千字的结构化原文塞不下，放进去也会被逐字念出来。
    """
    if isinstance(result, dict) and isinstance(result.get("brief"), str):
        return result["brief"].strip()
    return ""


def _ago(age_ms: int) -> str:
    """距今多久：十秒内"刚刚"，一分钟内按秒说（嘴要分得清是不是刚发生的），再往前按分钟说。"""
    seconds = max(0, age_ms) // 1000
    if seconds < 10:
        return "刚刚"
    if seconds < 60:
        return f"{seconds} 秒前"
    return age_text(age_ms)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _scene_section(inputs: SceneInputs) -> str:
    if not inputs.scene_brief:
        return ""
    age_ms = inputs.now_ms - inputs.scene_brief_ms if inputs.scene_brief_ms else 0
    note = f"（{_ago(age_ms)}看的" + ("，可能已经变了）" if age_ms >= STALE_SCENE_MS else "）")
    return f"【现在的状态】{note}\n{_clip(inputs.scene_brief, 320)}"


def _goal_section(inputs: SceneInputs) -> str:
    if not inputs.goals:
        return ""
    rows: List[str] = []
    for goal in inputs.goals:
        where = f"在 {goal.game} 里" if goal.game else ""
        status = goal.status
        if goal.status != "进行中" and goal.status_ms:
            status = f"{_ago(inputs.now_ms - goal.status_ms)}{goal.status}"
        rows.append(f"- {_ago(inputs.now_ms - goal.started_ms)}{where}开始：{_clip(goal.instruction, 120)}（{status}）")
    # 目标是打算，不是现状：实验里嘴把"退回屋内"这条指令说成了"我就死守在小木屋里"
    return "【手上的目标】（这是打算，不一定已经做到；现状以上面和下面为准）\n" + "\n".join(rows)


def _events_section(inputs: SceneInputs, limit: int) -> str:
    events = list(inputs.recent_events)[-limit:] if limit > 0 else []
    if not events:
        return ""
    rows = [f"- {_ago(inputs.now_ms - at_ms)} {_clip(text, 80)}" for at_ms, text in events]
    return "【刚发生的事】\n" + "\n".join(rows)


def _room_section(inputs: SceneInputs) -> str:
    parts = [f"环节：{inputs.segment_title}" if inputs.segment_title else "", inputs.room_activity.strip()]
    parts = [part for part in parts if part]
    return "【直播间】" + "；".join(parts) if parts else ""


def _notebook_section(inputs: SceneInputs) -> str:
    return f"【悬着的事】\n{_clip(inputs.notebook, 200)}" if inputs.notebook.strip() else ""


def _speeches_section(inputs: SceneInputs, limit: int) -> str:
    speeches = [line for line in inputs.recent_speeches if line.strip()][-limit:] if limit > 0 else []
    if not speeches:
        return ""
    return "【你刚说过】（别再说一遍）\n" + "\n".join(f"- {_clip(line, 60)}" for line in speeches)


def build_scene_card(
    inputs: SceneInputs,
    *,
    max_chars: int = 800,
    max_events: int = 6,
    max_speeches: int = 6,
) -> str:
    """拼一张不超过 ``max_chars`` 的现场卡。

    超长时按"谁最不能丢"往下砍：先减刚发生的事和她刚说过的话的条数，再丢悬着的事、直播间；
    现在的状态、手上的目标与这次的由头一直保留（各自已截短）。段落按阅读顺序排列，由头放最后。
    """
    trigger = f"【这次的由头】{_clip(inputs.trigger, 120)}" if inputs.trigger.strip() else ""
    events, speeches = max_events, max_speeches
    keep_room, keep_notebook = True, True
    while True:
        sections = [
            _scene_section(inputs),
            _goal_section(inputs),
            _events_section(inputs, events),
            _room_section(inputs) if keep_room else "",
            _notebook_section(inputs) if keep_notebook else "",
            _speeches_section(inputs, speeches),
            trigger,
        ]
        card = "\n\n".join(section for section in sections if section)
        if len(card) <= max_chars:
            return card
        if events > 2 or speeches > 2:
            events, speeches = max(2, events - 1), max(2, speeches - 1)
        elif keep_notebook:
            keep_notebook = False
        elif keep_room:
            keep_room = False
        elif events > 0 or speeches > 0:
            events, speeches = max(0, events - 1), max(0, speeches - 1)
        else:
            # 只剩必保的三段仍超长：整张截断，保住开头的现状
            return _clip(card, max_chars)
