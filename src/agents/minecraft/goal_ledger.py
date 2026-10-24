"""目标账本：MinecraftAgent 下达给 MaiCraft 的目标的三簿记账与目标↔账本联动。

三簿是纯内存记账，可独立构造断言（不需要建 Agent）：
- 目标此刻的样子（``note``：任务状态 / 提问 / 正在做的事）——任务状态提示、
  看一眼与交付门禁都读它；
- 被暂停的目标（提问与暂停都算"要模型动手才会往下走"）；
- 最近结束的目标（新任务开始时清空，恢复与集中整理带上看）。

任务账与批级委派账联动：经注入的 ``task_tracker`` 写框架侧账本（受理登记 /
进度 / 终态 / 停滞标记），目标到终态时本批吸收的委派共享结算（干巴机器摘要
先到账保底，LLM 交付语后到按终态粘滞写不动）。``task_tracker`` 缺省 None =
只记内存簿，不写框架账（无任务基建的装配降级）。批级委派的任务号清单由
Agent 每次调用时传入——批的生命周期归 Agent，账本不持它。

通知路由（结束/暂停/提问时唤醒哪个队列）留在 Agent；本模块只出文案：
``finish_notice`` / ``pause_notice`` 是纯函数，暂停通知里"暂停前急事"的
新鲜度时钟由调用方传入（单调时钟值），可测。
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from src.modules.logging import get_logger
from src.modules.tools.tasks import TERMINAL_TASK_STATES

from .context import json_text
from .maicraft import AWAITING_ANSWER, EXECUTE, PROVIDER, PAUSED, GoalRun, ledger_status_of, ledger_task_id

__all__ = ["GoalLedger", "finish_notice", "goal_label", "pause_notice"]

logger = get_logger("MinecraftGoalLedger")

# 最近结束的目标保留条数：新任务开始时清空，恢复与集中整理带上看
_RECENT_FINISHED_GOALS = 6

# 暂停前急事的保鲜期：超过这个时长才到的暂停通知不再拿它解释暂停原因
_URGENT_BEFORE_PAUSE_S = 60.0

# Mod 写的这种"目标 X 已暂停（第 N 步）"不算暂停原因（宿主自己发的例行通知）
_BARE_PAUSE_DOING = re.compile(r"目标 \S+ 已暂停（第 \d+ 步）")


def goal_label(run: GoalRun) -> str:
    """目标在通知里的人读标签：能力名 + 目的（有目的时）。"""
    return f"{run.ability}，{run.purpose}" if run.purpose else run.ability


def finish_notice(run: GoalRun, self_brief: str) -> str:
    """目标结束的通知：达成情况、结束那一刻角色怎样、结果，以及折起来的大块证据怎么读回。

    结束时在哪、血和饱食、手上拿着什么、开着什么界面由 Mod 写成一行附在结束事件上：
    模型接下一步之前不用为这些先看一眼自己。
    Mod 默认把结果里的大块证据（试过的办法、大的细节、长列表）折起来列在 ``result.folded``，
    这里给的不是完整结果：有折起来的段时说清用哪个调用、照抄哪个 path 读回来，不让模型以为证据丢了。
    """
    brief = self_brief.strip().rstrip("。")
    body = f"结束时：{brief}。" if brief else ""
    folded = (run.result or {}).get("folded")
    if isinstance(folded, list) and folded:
        # 有折起来的段：告诉模型按段读回，长列表照返回的 next 一页页翻，不必一次全拿。
        label = (
            f"结果（大块证据折在 result.folded 里，要看哪段就照抄它的 path 用 "
            f"maicraft_goal(operation=get, goal_id={run.goal_id}, path=…) 读，长的照返回的 next 翻页）"
        )
    else:
        label = "结果"
    return (
        f"[系统] 目标 {run.goal_id}（{goal_label(run)}）结束了：{run.result_status}。"
        f"{body}{label}：{json_text(run.result)}"
    )


def pause_notice(
    run: GoalRun,
    after_world_change: bool,
    urgent: Optional[Tuple[str, float]],
    now_monotonic: float,
) -> str:
    """目标被暂停的通知：说清为什么停，只有事件流真换了才说重进世界。

    原因按这个顺序取：事件流换了编号（重进世界或游戏重启，没做完的目标读回来都先停着）；
    Mod 在 doing 里写了原因就照抄 Mod 的；都没有时带上暂停前最近一条身体急事
    （撤离甩开追兵后"离打点 N 格，主任务停在半路等下一步指示"那句）；还没有就如实说没写原因。
    ``urgent`` 是调用方取出的一条待用急事（收尾那句话, 收到的单调时刻）；是否仍新鲜由
    ``now_monotonic`` 判定，用不用都由调用方决定丢弃——本函数不持状态。
    """
    fresh = urgent is not None and now_monotonic - urgent[1] <= _URGENT_BEFORE_PAUSE_S
    doing = run.doing.strip().rstrip("。")
    if after_world_change:
        why = "游戏重进了世界或重启过，没做完的目标读回来都先停着"
    elif doing and not _BARE_PAUSE_DOING.fullmatch(doing):
        why = f"Mod 说：{doing}"
    elif fresh and urgent is not None:
        why = f"暂停前身体刚处理完一件急事：{urgent[0].strip().rstrip('。')}"
    else:
        why = "Mod 没写暂停原因"
    return (
        f"[系统] 目标 {run.goal_id}（{goal_label(run)}）被暂停了，身体没在做它。{why}。"
        f"先用 minecraft_get_work_log 读工作笔记，核对哪些事其实已经做完"
        f"（东西可能已放置或已消耗，背包里没有不等于丢了）；再看现场。"
        f"然后才用 maicraft_goal(operation=resume 或 cancel, goal_id={run.goal_id}) 继续或取消。"
    )


def _dry_goal_summary(run: GoalRun) -> str:
    """目标终态给委派账的干巴机器摘要：只陈述结果，话术语义留给 LLM 的交付语。"""
    label = run.result_status or "结束"
    return f"游戏目标 {run.goal_id}（{run.ability}）{label}：{run.summary}"


class GoalLedger:
    """目标三簿与目标↔任务账/委派账的记账。非线程安全；仅在 Agent 事件循环内使用。"""

    def __init__(self, *, task_tracker: Optional[Any] = None, agent_name: str = "") -> None:
        self._task_tracker = task_tracker
        self._agent_name = agent_name
        self._notes: Dict[int, Dict[str, Any]] = {}
        self._paused: set[int] = set()
        self._finished: Deque[Dict[str, Any]] = deque(maxlen=_RECENT_FINISHED_GOALS)

    # ----- 三簿：目标此刻的样子 / 被暂停 / 最近结束 -----

    def note(self, run: GoalRun) -> None:
        """记下目标此刻的样子：任务状态、看一眼与交付门禁都读它。"""
        note: Dict[str, Any] = {"goal_id": run.goal_id, "ability": run.ability, "state": run.state}
        if run.purpose:
            note["purpose"] = run.purpose
        if run.question:
            note["question"] = run.question
        if run.doing:
            note["doing"] = run.doing
        self._notes[run.goal_id] = note
        if run.state == PAUSED:
            self._paused.add(run.goal_id)
        else:
            self._paused.discard(run.goal_id)

    def note_of(self, goal_id: int) -> Dict[str, Any]:
        """读目标此刻的样子；没有记录时返回空 dict（调用方按字段判差异）。"""
        return self._notes.get(goal_id, {})

    def forget(self, goal_id: int) -> None:
        """目标移出跟踪时的簿记收尾：此刻的样子与暂停标记一并清除。"""
        self._notes.pop(goal_id, None)
        self._paused.discard(goal_id)

    def remember_finished(self, run: GoalRun) -> None:
        """终态目标进最近结束簿（保留最近若干条，供恢复与集中整理带上看）。"""
        self._finished.append(
            {"goal_id": run.goal_id, "ability": run.ability, "status": run.result_status, "summary": run.summary}
        )

    def settle_finished(self, run: GoalRun, *, batch_task_ids: Sequence[str] = ()) -> None:
        """目标结束的完整收账：清此刻的样子与暂停标记、进最近结束簿、写任务账终态。"""
        self.forget(run.goal_id)
        self.remember_finished(run)
        self.update_task_ledger(run, batch_task_ids=batch_task_ids)

    def mark_gone(self, goal_id: int, reason: str, *, batch_task_ids: Sequence[str] = ()) -> Dict[str, Any]:
        """在跟踪的目标查不到了（多半是换了世界）：按失败收账，返回它最后的样子。"""
        note = self._notes.pop(goal_id, {"goal_id": goal_id})
        self._paused.discard(goal_id)
        self._finished.append({**note, "status": "gone", "summary": reason})
        ledger = self._framework_ledger()
        if ledger is not None:
            ledger.update(ledger_task_id(goal_id), "failed", summary=reason)
        self._settle_batch(batch_task_ids, "failed", f"游戏目标 {goal_id} 不在了：{reason}")
        return note

    def actionable_goal_ids(self) -> List[int]:
        """要模型动手才会往下走的目标：在等回答的、被暂停的。"""
        return sorted(
            goal_id
            for goal_id, note in self._notes.items()
            if note.get("state") == AWAITING_ANSWER or goal_id in self._paused
        )

    def notes_for(self, goal_ids: Sequence[int]) -> List[Dict[str, Any]]:
        """按目标号取此刻的样子；没有记录的跳过。"""
        return [self._notes[goal_id] for goal_id in goal_ids if goal_id in self._notes]

    def all_notes_sorted(self) -> List[Dict[str, Any]]:
        """全部在记的目标样子，按目标号排序（恢复与集中整理用）。"""
        return [note for _, note in sorted(self._notes.items())]

    def recent_finished(self) -> List[Dict[str, Any]]:
        """最近结束的目标（旧到新）。"""
        return list(self._finished)

    def clear_finished(self) -> None:
        """新任务开始：上个任务的目标终态记录不再带看。"""
        self._finished.clear()

    # ----- 任务账与批级委派账联动 -----

    def track(self, run: GoalRun) -> None:
        """受理登记：内存簿记下此刻的样子，任务账登记条目供后续跟踪。"""
        self.note(run)
        if self._task_tracker is not None:
            self._task_tracker.track(
                task_id=ledger_task_id(run.goal_id),
                provider=PROVIDER,
                tool=EXECUTE,
                initiator=self._agent_name,
                snapshot=run.data,
            )

    def register_and_settle(self, run: GoalRun) -> None:
        """execute 当场返回终态的目标补记账：先按受理登记、再写终态，账本才广播得出这次变化。"""
        if self._task_tracker is not None:
            self._task_tracker.track(
                task_id=ledger_task_id(run.goal_id),
                provider=PROVIDER,
                tool=EXECUTE,
                initiator=self._agent_name,
                snapshot=run.data,
            )
        self.update_task_ledger(run)

    def update_task_ledger(self, run: GoalRun, summary: str = "", *, batch_task_ids: Sequence[str] = ()) -> None:
        """目标进度/终态写任务账；终态时本批吸收的委派共享结算。"""
        ledger = self._framework_ledger()
        if ledger is None:
            return
        status = ledger_status_of(run)
        ledger.update(ledger_task_id(run.goal_id), status, snapshot=run.data, summary=summary or run.summary)
        if status in TERMINAL_TASK_STATES:
            self._settle_batch(batch_task_ids, status, _dry_goal_summary(run))

    def note_stalled(self, run: GoalRun, *, batch_task_ids: Sequence[str] = ()) -> None:
        """Mod 报告目标停着没动静的账面中间态：快照带停滞标记，本批委派账同步打标。"""
        ledger = self._framework_ledger()
        if ledger is not None:
            ledger.update(
                ledger_task_id(run.goal_id),
                ledger_status_of(run),
                snapshot={**run.data, "stalled": True},
                summary=f"Mod 报告目标长时间没有新事件，仍未结束：{run.doing or run.ability}",
            )
        self._flag_batch_stalled(batch_task_ids, run.goal_id, run.ability)

    def _framework_ledger(self) -> Optional[Any]:
        return getattr(self._task_tracker, "ledger", None)

    def _settle_batch(self, batch_task_ids: Sequence[str], status: str, summary: str) -> None:
        """批级关联：本批吸收的委派共享批内目标的终态，干巴机器摘要先到账保底。

        LLM 的交付/升级后到时按终态粘滞改不动状态（条目已移除，重复写入无效），
        它的话术经 game.report 走主播侧；这里保证的是"LLM 一句没说也有回音"。
        粗糙性已知并接受：一批委派共享批内目标终态，批中途某个目标先失败就先写失败。
        """
        ledger = self._framework_ledger()
        if ledger is None:
            return
        for task_id in batch_task_ids:
            if ledger.get(task_id) is None:
                # 已被早到的目标终态或 LLM 交付写过终态：状态粘滞，不回退
                continue
            ledger.update(task_id, status, summary=summary)

    def _flag_batch_stalled(self, batch_task_ids: Sequence[str], goal_id: int, ability: str) -> None:
        """STALLED 打进本批委派账的快照（中间态）：主播侧渲染看得到"停着没动静"。

        目标随后结束（或恢复有动静）时分别由终态直写与常规进度写入覆盖掉这份标记。
        """
        ledger = self._framework_ledger()
        if ledger is None:
            return
        for task_id in batch_task_ids:
            record = ledger.get(task_id)
            if record is None:
                continue
            stalled = dict(record.snapshot.get("stalled_goals") or {})
            stalled[str(goal_id)] = ability
            ledger.update(task_id, "running", snapshot={**record.snapshot, "stalled_goals": stalled})
