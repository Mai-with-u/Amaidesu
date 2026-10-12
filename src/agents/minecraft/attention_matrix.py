"""MaiCraft 任务事件的分类表：事件流里哪些是值得向观众叙述的身体遭遇。

## 为什么需要这一层

MaiCraft v1 的事件流里大部分是目标运行的处境变化（开始、提问、暂停、结束），那是游戏 Agent
的任务通道，主播的叙事不需要。和身体有关的有这几种：挨了一阵打（谁打的、几下、掉了多少血，
一段只来一条、不分开始结束）、换了维度、生存需求插进来的临时任务开始与结束（自卫、夜里封顶自保、
退离边沿）、角色自己处理不了的需求（饿了没吃上、封顶没封上）、角色死亡；装备用坏和探索途中的发现
也是主播能讲的经历。`maicraft_attention` 采集器只把这些转成 `game.body.*` 事件，任务通道的事件留在上游。

提醒（快溺水、装备快坏、黑暗里反复挨打这类）是写给游戏 Agent 的维护提示，证据在 observe(self) 里，
不是遭遇本身；转给主播只会变成一串重复的"身体事件"，把真正的挨打和死亡挤掉，所以不进叙事通道。

## 契约

- `classify()` 返回 `None` = 这条事件不进入叙事通道（目标运行的处境变化、提醒）；
- 未知事件种类归 `unknown` 且保留 `source_event_type`，所以**上游加新事件种类不会让本系统的事件面漂移**；
- `summarize()` 只陈述事件里有的事实：Mod 写的那句话去掉坐标，坐标这类遥测不进叙事通道。
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from src.modules.events.names import CoreEvents

#: 叙事种类 → 事件名（事件名一律引用 CoreEvents 常量，不写字面量）
KIND_TO_EVENT: Dict[str, str] = {
    "attacked": CoreEvents.GAME_BODY_ATTACKED,
    "attack_ended": CoreEvents.GAME_BODY_ATTACK_ENDED,
    "died": CoreEvents.GAME_BODY_DIED,
    "respawned": CoreEvents.GAME_BODY_RESPAWNED,
    "reflex_started": CoreEvents.GAME_BODY_REFLEX_STARTED,
    "reflex_finished": CoreEvents.GAME_BODY_REFLEX_FINISHED,
    "dimension_changed": CoreEvents.GAME_BODY_DIMENSION_CHANGED,
    "need_unhandled": CoreEvents.GAME_BODY_NEED_UNHANDLED,
    "unknown": CoreEvents.GAME_BODY_UNKNOWN,
}

#: 已经是"结局面"的种类（主播据此把措辞从"正在"改成"刚才"）
RESOLVED_KINDS = frozenset({"attack_ended", "respawned", "reflex_finished"})

#: 事件种类 → 叙事种类。生存需求的临时任务就是"身体先停下手上的活处理急事"，对应本能接管。
_SOURCE_KINDS: Dict[str, str] = {
    # 一阵挨打 Mod 只发一条（按来源归并，安静 5 秒或每 10 秒发一次），没有"打完了"那一条，所以只用 attacked。
    "hurt": "attacked",
    "dimension_changed": "dimension_changed",
    "temporary_task_started": "reflex_started",
    "temporary_task_finished": "reflex_finished",
    "need_unhandled": "need_unhandled",
    # 角色血量见底进了死亡流程：Mod 只发这一条，重生后接着做原来的目标，不另发重生事件。
    "character_died": "died",
}

#: 目标运行的处境变化与死亡恢复决策的执行结果：游戏 Agent 的任务通道，不进叙事通道
_GOAL_KINDS = frozenset(
    {"started", "asked", "paused", "resumed", "step_finished", "finished", "death_recovery_applied"}
)

#: 写给游戏 Agent 的维护提示：证据在 observe(self) 里，不是遭遇，不进叙事通道
_SILENT_KINDS = frozenset({"reminder"})

#: 每种叙事的开头：Mod 那句话是写给游戏 Agent 的，主播要先知道这是什么性质的事
_LEADS: Dict[str, str] = {
    "attacked": "身体挨了一阵打",
    "dimension_changed": "换了个维度",
    "reflex_started": "身体先停下手上的活处理急事",
    "reflex_finished": "身体处理完急事",
    "need_unhandled": "身体遇到处理不了的事",
    "died": "角色倒下了",
}

#: 没有专门叙事种类、走 unknown 的事件里，主播也能讲的几种：给一个说清性质的开头，不让它读成"身体事件：xxx"
_SOURCE_LEADS: Dict[str, str] = {
    "gear_broken": "手上的装备用坏了",
    "discovered": "探索时发现了值得记的东西",
    "follow_status": "跟着人走时",
}

# 一个分句里出现"x, y, z"三个整数就是坐标：整句去掉，不在半句话里留下"停在了"这种残片。
_COORDINATES = re.compile(r"-?\d+\s*,\s*-?\d+\s*,\s*-?\d+")
# 括号里的观察编号（e12、b5 这类）是给游戏 Agent 指东西用的，念给观众没有意义，去掉括号连同编号。
_OBSERVATION_IDS = re.compile(r"\s*[（(][a-z]\d+[）)]")
# 只按中文标点与分号、冒号断句；英文逗号不断，否则坐标本身会被拆开。
_CLAUSE_BREAK = re.compile(r"[，；：;:]")


def classify(source_event_type: str) -> Optional[str]:
    """事件种类 → 叙事种类；不进入叙事通道返回 ``None``。"""
    source = str(source_event_type or "")
    if not source or source in _GOAL_KINDS or source in _SILENT_KINDS:
        return None
    return _SOURCE_KINDS.get(source, "unknown")


def without_coordinates(text: str) -> str:
    """去掉带坐标的分句与括号里的观察编号，其余原样保留（"被威胁，插入自卫：打点在 3, 64, 9" → "被威胁，插入自卫"）。"""
    plain = _OBSERVATION_IDS.sub("", str(text or ""))
    clauses = [clause.strip() for clause in _CLAUSE_BREAK.split(plain)]
    return "，".join(clause for clause in clauses if clause and not _COORDINATES.search(clause))


def summarize(kind: str, source_event_type: str, message: str) -> str:
    """一句可直接讲述的中文：叙事开头加上 Mod 那句话（去掉坐标）；没有原话时只说开头。"""
    lead = (
        _LEADS.get(kind)
        or _SOURCE_LEADS.get(source_event_type)
        or (f"身体事件：{source_event_type}" if source_event_type else "身体事件")
    )
    detail = without_coordinates(message)
    return f"{lead}：{detail}" if detail else lead


__all__ = [
    "KIND_TO_EVENT",
    "RESOLVED_KINDS",
    "classify",
    "summarize",
    "without_coordinates",
]
