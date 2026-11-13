"""目标账本：MinecraftAgent 下达给 MaiCraft 的目标的三簿记账与目标↔账本联动。

三簿是纯内存记账，可独立构造断言（不需要建 Agent）：
- 目标此刻的样子（``note``：任务状态 / 提问 / 正在做的事）——任务状态提示、
  看一眼与交付门禁都读它；
- 被暂停的目标（提问与暂停都算"要模型动手才会往下走"）；
- 最近结束的目标（新任务开始时清空，恢复与集中整理带上看）。

任务账与批级委派账联动：经注入的 ``task_tracker`` 写框架侧账本（受理登记 /
进度 / 终态 / 停滞标记），目标到终态时给本批吸收的委派写一条游戏侧进展备注
（快照 ``game_progress_notes``，状态不动——委派终态只由交付上报 / 撤单 /
下播兜底写）``task_tracker`` 缺省 None = 只记内存簿，不写框架账（无任务基建
的装配降级）。批级委派的任务号清单经构造注入的 ``finished_ids`` 供给现取
（即 :class:`DelegationBook.finished_ids`）：批的生命周期归 Agent，账本只读
快照、不持它。

通知路由（结束/暂停/提问时唤醒哪个队列）留在 Agent；本模块只出文案：
``finish_notice`` / ``pause_notice`` 是纯函数，暂停通知里"暂停前急事"的
新鲜度时钟由调用方传入（单调时钟值），可测。
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

from src.modules.logging import get_logger
from src.modules.tools.tasks import TERMINAL_TASK_STATES

from .context import json_text
from .maicraft import AWAITING_ANSWER, EXECUTE, PROVIDER, PAUSED, GoalRun, ledger_status_of, ledger_task_id
from .mod_facts import goal_mining_and_pits_notes

__all__ = [
    "GoalLedger",
    "finish_notice",
    "goal_failure_line",
    "goal_label",
    "is_use_on_air_misuse",
    "pause_notice",
    "use_air_misuse_hint",
]

logger = get_logger("MinecraftGoalLedger")

# 最近结束的目标保留条数：新任务开始时清空，恢复与集中整理带上看
_RECENT_FINISHED_GOALS = 6

# 暂停前急事的保鲜期：超过这个时长才到的暂停通知不再拿它解释暂停原因
_URGENT_BEFORE_PAUSE_S = 60.0

# 委派快照里游戏侧进展备注的保留条数：只留最近的，快照不随目标数无限膨胀
_GAME_PROGRESS_NOTES_MAX = 5

# Mod 写的这种"目标 X 已暂停（第 N 步）"不算暂停原因（宿主自己发的例行通知）
_BARE_PAUSE_DOING = re.compile(r"目标 \S+ 已暂停（第 \d+ 步）")


def goal_label(run: GoalRun) -> str:
    """目标在通知里的人读标签：能力名 + 目的（有目的时）。"""
    return f"{run.ability}，{run.purpose}" if run.purpose else run.ability


def is_use_on_air_misuse(run: GoalRun) -> bool:
    """判定是否属于"对着空气用了物品未生效"的用法错误（协议第 6 节：不是物品或账号用不了）。

    满足三要素：能力是 ``use``、结果 ``status=="failed"``、``details.used_on=="air"`` 且
    ``problem.kind=="not_possible_here"``（大小写容忍）。
    """
    if run.ability not in ("use", "maicraft:use") or run.result_status != "failed":
        return False
    res = run.result if isinstance(run.result, dict) else {}
    details = res.get("details") if isinstance(res.get("details"), dict) else {}
    problem = res.get("problem") if isinstance(res.get("problem"), dict) else {}
    return details.get("used_on") == "air" and str(problem.get("kind") or "").lower() == "not_possible_here"


def use_air_misuse_hint(run: GoalRun) -> str:
    """给游戏 Agent 的用法纠正说明：命中对空使用未生效时返回明确指引，其余返回空串。"""
    if not is_use_on_air_misuse(run):
        return ""
    return (
        "【宿主判定】这次是对着空气用了手上的物品（used_on=air），是用错了对象或少了朝向面——"
        '放在脚下地面上请传 target={"kind": "here"} 并配 parameters.face="up"，'
        '对指定方块用给 b# 或坐标（放顶面配 face="up"），对生物用给 e#。'
        "不是这件物品坏了，也不是这个账号或权限放不出来，改对 target/face 再用即可。"
    )


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
    misuse = use_air_misuse_hint(run)
    tail = f" {misuse}" if misuse else ""
    return (
        f"[系统] 目标 {run.goal_id}（{goal_label(run)}）结束了：{run.result_status}。"
        f"{body}{label}：{json_text(run.result)}{tail}"
    )


def goal_failure_line(run: GoalRun) -> str:
    """目标没做成/只做了一半时给主播的一句话（milestone 事件用）；其余状态返回空串。

    目标结束（做成与没做成）只走 ``finish_notice`` 注入游戏 Agent 自己的
    队列，主播侧要等下一个决策窗才看得到——失败尤其值得当场说（场 65：
    压机问题驻留 63 分钟，她每次都晚一拍）。做成不在此列：委派交付已有
    delivery 上报当场触发，非委派的中间成果逐个播报会刷屏。
    """
    status = run.result_status
    if status not in ("failed", "partial"):
        return ""
    if is_use_on_air_misuse(run):
        # 场 65 魂符教训：对着空气用没生效是用错了对象，明说点空了，避免主播误读成"这个号放不出来"
        return f"{goal_label(run)}对着空气用没生效（用法不对，得对着脚下地面或目标方块用，不是东西用不了）"
    label = "没做成" if status == "failed" else "只做了一半"
    summary = (run.summary or "").strip()
    body = f"：{summary}" if summary else ""
    return f"{goal_label(run)}{label}{body}"


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


def _short_purpose(purpose: str) -> str:
    """purpose 截成一行短尾巴：换行折空格，超过约 16 字截断补省略号。"""
    flat = " ".join(purpose.split())
    return flat if len(flat) <= 16 else f"{flat[:16]}…"


def _dry_goal_summary(run: GoalRun) -> str:
    """目标终态给委派账的干巴机器摘要：只陈述结果，话术语义留给 LLM 的交付语。

    摘要常不带宾语（如"找到 8 个"），脱敏后主播只看到"做完了：找到 8 个"就自己脑补了对象；
    目标 purpose 非空时截短拼在后面，让备注自带"这一步在干嘛"。不拼进抬头括号——抬头整个会被
    脱敏器剥掉，purpose 会跟着丢。采掘目标带了连锁批次或留坑回填时紧凑附在摘要后面。
    """
    label = run.result_status or "结束"
    notes = goal_mining_and_pits_notes(run.result)
    extra = "".join(f"；{notes[key]}" for key in ("chain_mining", "pits") if key in notes)
    tail = f"（这一步：{_short_purpose(run.purpose)}）" if run.purpose else ""
    return f"游戏目标 {run.goal_id}（{run.ability}）{label}：{run.summary}{extra}{tail}"


def _no_batch_task_ids() -> Sequence[str]:
    """缺省批级任务号供给：无委派基建的装配降级下，批级联动恒空。"""
    return ()


class GoalLedger:
    """目标三簿与目标↔任务账/委派账的记账。非线程安全；仅在 Agent 事件循环内使用。"""

    def __init__(
        self,
        *,
        task_tracker: Optional[Any] = None,
        agent_name: str = "",
        finished_ids: Optional[Callable[[], Sequence[str]]] = None,
    ) -> None:
        """Args:
        task_tracker: 框架侧任务账（缺省 None = 只记内存簿的装配降级）
        agent_name: 发起方注册名（任务账登记的 initiator）
        finished_ids: 本批已入账、待联动结算的委派任务号供给（即
            :class:`DelegationBook.finished_ids` 的只读快照）；缺省 None =
            批级联动无任务号（无委派基建的装配降级）
        """
        self._task_tracker = task_tracker
        self._agent_name = agent_name
        self._finished_ids = finished_ids if finished_ids is not None else _no_batch_task_ids
        self._notes: Dict[int, Dict[str, Any]] = {}
        self._paused: set[int] = set()
        self._pause_notified: set[int] = set()
        self._finished: Deque[Dict[str, Any]] = deque(maxlen=_RECENT_FINISHED_GOALS)

    # ----- 三簿：目标此刻的样子 / 被暂停 / 最近结束 -----

    def pause_notice_sent(self, goal_id: int) -> bool:
        """这个目标的暂停是否已经给模型发过通知：判定只看通知簿，不看账本状态。

        账本的暂停态可能被批内的目标查询先记上（看门狗叫醒的批读一眼目标就把
        paused 记了账），随后事件流才送来暂停事件——按账本判会把第一次通知吞掉。
        """
        return goal_id in self._pause_notified

    def mark_pause_notified(self, goal_id: int) -> None:
        self._pause_notified.add(goal_id)

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
        self._pause_notified.discard(goal_id)

    def _append_finished_deduped(self, entry: Dict[str, Any]) -> None:
        """按 goal_id 去重写入最近结束簿：同一目标从回执与事件流两头都看到终态时只留最新一条。"""
        gid = entry.get("goal_id")
        if gid is not None:
            kept = [item for item in self._finished if item.get("goal_id") != gid]
            self._finished.clear()
            self._finished.extend(kept)
        self._finished.append(entry)

    def remember_finished(self, run: GoalRun) -> None:
        """终态目标进最近结束簿（按 goal_id 去重、保留最近若干条，供恢复与集中整理带上看）。"""
        entry: Dict[str, Any] = {
            "goal_id": run.goal_id,
            "ability": run.ability,
            "status": run.result_status,
            "summary": run.summary,
        }
        entry.update(goal_mining_and_pits_notes(run.result))
        self._append_finished_deduped(entry)

    def settle_finished(self, run: GoalRun) -> None:
        """目标结束的完整收账：清此刻的样子与暂停标记、进最近结束簿、写任务账终态。"""
        self.forget(run.goal_id)
        self.remember_finished(run)
        self.update_task_ledger(run)

    def mark_gone(self, goal_id: int, reason: str) -> Dict[str, Any]:
        """在跟踪的目标查不到了（多半是换了世界）：按失败收账，返回它最后的样子。"""
        note = self._notes.pop(goal_id, {"goal_id": goal_id})
        self._paused.discard(goal_id)
        self._append_finished_deduped({**note, "status": "gone", "summary": reason})
        ledger = self._framework_ledger()
        if ledger is not None:
            ledger.update(ledger_task_id(goal_id), "failed", summary=reason)
        self._note_batch_progress(f"游戏目标 {goal_id} 不在了：{reason}")
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

    def update_task_ledger(self, run: GoalRun, summary: str = "") -> None:
        """目标进度/终态写任务账；终态时给本批吸收的委派写游戏侧进展备注。"""
        ledger = self._framework_ledger()
        if ledger is None:
            return
        status = ledger_status_of(run)
        ledger.update(ledger_task_id(run.goal_id), status, snapshot=run.data, summary=summary or run.summary)
        if status in TERMINAL_TASK_STATES:
            self._note_batch_progress(_dry_goal_summary(run))

    def note_stalled(self, run: GoalRun) -> None:
        """Mod 报告目标停着没动静的账面中间态：快照带停滞标记，本批委派账同步打标。"""
        ledger = self._framework_ledger()
        if ledger is not None:
            ledger.update(
                ledger_task_id(run.goal_id),
                ledger_status_of(run),
                snapshot={**run.data, "stalled": True},
                summary=f"Mod 报告目标长时间没有新事件，仍未结束：{run.doing or run.ability}",
            )
        self._flag_batch_stalled(run.goal_id, run.ability)

    def _framework_ledger(self) -> Optional[Any]:
        return getattr(self._task_tracker, "ledger", None)

    def _note_batch_progress(self, summary: str) -> None:
        """批级关联：批内目标收尾一步，给本批吸收的委派写一条游戏侧进展备注。

        备注落在委派快照的 ``game_progress_notes``（保留最近若干条），委派
        状态不动：一个委派的活常拆成多个游戏目标，某个目标终态不代表委派
        整体干完，提前写终态会让主播读成"做完了"而在途门放行重派。委派
        终态只由交付上报、撤单、下播兜底写。任务号经构造注入的
        ``finished_ids`` 供给现取；条目不在册（已被终态移除）的跳过。
        """
        ledger = self._framework_ledger()
        if ledger is None:
            return
        for task_id in self._finished_ids():
            record = ledger.get(task_id)
            if record is None:
                continue
            notes = list(record.snapshot.get("game_progress_notes") or [])
            notes.append(summary)
            ledger.update(
                task_id,
                record.status,
                snapshot={**record.snapshot, "game_progress_notes": notes[-_GAME_PROGRESS_NOTES_MAX:]},
            )

    def _flag_batch_stalled(self, goal_id: int, ability: str) -> None:
        """STALLED 打进本批委派账的快照（中间态）：主播侧渲染看得到"停着没动静"。

        目标随后恢复有动静时由常规进度写入覆盖掉这份标记。
        """
        ledger = self._framework_ledger()
        if ledger is None:
            return
        for task_id in self._finished_ids():
            record = ledger.get(task_id)
            if record is None:
                continue
            stalled = dict(record.snapshot.get("stalled_goals") or {})
            stalled[str(goal_id)] = ability
            ledger.update(task_id, "running", snapshot={**record.snapshot, "stalled_goals": stalled})
