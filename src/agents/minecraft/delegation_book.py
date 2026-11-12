"""委派账：MinecraftAgent 名下委派任务的批内追踪与账面写入的单一管辖。

委派从受理到终态是一条完整生命周期：受理入批 → 批次开跑重写 running →
交付写终态 / 挂起写待定夺 / 收手移出由受理核心落账 / 撤单出账 / 下播强制结算。
账面写入走注入的 ``task_tracker``（框架侧任务账本）；``task_tracker`` 缺省 None =
不写框架账（无任务基建的装配降级），此时追踪清单也保持为空。

两份清单是批的生命周期状态，时序契约在此锁定：
- 本批已吸收、还没标 running 的任务号（``absorb`` 进来）；
- 已在账 running、批结束时等着写终态的任务号（``mark_running`` 归拢）。
任何时刻每个执行者至多一件【在做】，不再有急件暂放清单（搁下统一由委派受理核心先让本 Agent
收手，再放回待办最前，轮到时按新任务重送达）。

``finished_ids`` 只读视图供目标账本联动写进展备注：批内目标收尾一步时，
本批吸收的委派在快照里记一条游戏侧进展（状态不动，见
``GoalLedger._note_batch_progress``）。消息队列与停手通知的注入留在
Agent——那是消息路由的事，账只管账。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.modules.agents.delegation_board import SHELVED_AT_KEY, SHELVED_PROGRESS_KEY
from src.modules.logging import get_logger
from src.modules.tools.tasks import REPLY_NOTE_AT_KEY, REPLY_NOTE_KEY

__all__ = ["DelegationBook"]

logger = get_logger("MinecraftDelegationBook")


def _live_end_summary(record: Any) -> str:
    """下播收尾摘要：写明每张单当时是在做、待办还没开工，还是待办里搁下的，供下一场开播【上次收尾】读取。"""
    status = str(getattr(record, "status", "") or "")
    snapshot: Dict[str, Any] = getattr(record, "snapshot", None) or {}
    if status == "queued":
        if int(snapshot.get(SHELVED_AT_KEY) or 0) > 0:
            progress = str(snapshot.get(SHELVED_PROGRESS_KEY) or "").strip() or "未记进展"
            return f"下播自动收尾（待办，搁下了，做到：{progress}）"
        return "下播自动收尾（待办，还没开工）"
    return "下播自动收尾（当时在做）"


class DelegationBook:
    """委派任务的批内追踪与账面写入。非线程安全；仅在 Agent 事件循环内使用。"""

    def __init__(self, *, task_tracker: Optional[Any] = None, agent_name: str = "") -> None:
        self._task_tracker = task_tracker
        self._agent_name = agent_name
        self._batch_ids: List[str] = []
        self._finished_ids: List[str] = []

    def _framework_ledger(self) -> Optional[Any]:
        return getattr(self._task_tracker, "ledger", None)

    # ----- 受理与批内生命周期 -----

    def absorb(self, task_id: str) -> None:
        """委派指令进入本批：登记待标记 running（无任务基建时也先记号，``mark_running`` 清空）。"""
        if task_id not in self._batch_ids and task_id not in self._finished_ids:
            self._batch_ids.append(task_id)

    @property
    def finished_ids(self) -> Tuple[str, ...]:
        """已入账 running、待本批写终态的任务号（只读快照，目标账本联动结算用）。"""
        return tuple(self._finished_ids)

    def tracked_ids(self) -> Tuple[str, ...]:
        """名下仍在追踪的委派任务号（待开跑 + 已入账 running；只读快照，收手/急停/撤单用）。"""
        return tuple(self._batch_ids) + tuple(self._finished_ids)

    def release(self, task_id: str) -> bool:
        """收手移出：把 ``task_id`` 从本 Agent 的待开跑与在办追踪清单里摘除，**不写任务账本**。

        供委派受理核心换活、先搁下或发起方结单时调用：账面后续写成认定完成、放回待办或回滚恢复，
        统一由受理核心写；从追踪清单摘除后，本批后续收尾不会把已收手的旧单误写成终态。
        返回该任务原本是否在本账追踪范围内。
        """
        found = False
        if task_id in self._batch_ids:
            self._batch_ids.remove(task_id)
            found = True
        if task_id in self._finished_ids:
            self._finished_ids.remove(task_id)
            found = True
        if found:
            logger.info(f"{self._agent_name} 委派 {task_id} 已收手移出追踪清单")
        return found

    def mark_running(self) -> None:
        """批次开跑：本批吸收的委派写 running 并入终态追踪清单；上批留下的待定夺任务恢复 running。"""
        ledger = self._framework_ledger()
        if ledger is None:
            self._batch_ids.clear()
            return
        for task_id in self._finished_ids:
            ledger.update(task_id, "running", summary="执行 Agent 已恢复原任务")
        for task_id in self._batch_ids:
            ledger.update(task_id, "running", summary="执行 Agent 已开始处理")
            if task_id not in self._finished_ids:
                self._finished_ids.append(task_id)
        self._batch_ids.clear()

    def note_reply(self, text: str, at_ms: int) -> None:
        """这一步说的话记成名下在跑的委派的旁注（只留最近一句，带时刻）：主播每窗决策前读，标明不一定对。

        走账本旁注，不算进展：每步推理都刷新进展的话，原地打转时停滞告警永远不响。
        """
        ledger = self._framework_ledger()
        content = text.strip()
        if ledger is None or not content:
            return
        for task_id in self._finished_ids:
            ledger.annotate(task_id, {REPLY_NOTE_KEY: content, REPLY_NOTE_AT_KEY: at_ms})

    def clear_reply_notes(self) -> None:
        """换存档时清空名下委派的推理旁注：旧存档里说的判断在新存档里不再成立。"""
        ledger = self._framework_ledger()
        if ledger is None:
            return
        for task_id in self._finished_ids:
            ledger.annotate(task_id, {REPLY_NOTE_KEY: "", REPLY_NOTE_AT_KEY: 0})

    # ----- 终态与中间态 -----

    def finish(self, status: str, *, summary: str) -> None:
        """批终态：追踪清单整体写终态并清空。"""
        ledger = self._framework_ledger()
        if ledger is None:
            return
        for task_id in self._finished_ids:
            ledger.update(task_id, status, summary=summary)
        self._finished_ids.clear()

    def hold(self, reason: str) -> None:
        """委派停在待定夺：账面 ``waiting_for_decision``，追踪清单保留（批次重启时重写 running）。"""
        ledger = self._framework_ledger()
        if ledger is None:
            return
        for task_id in self._finished_ids:
            record = ledger.get(task_id)
            current_snapshot = dict(record.snapshot) if record is not None and isinstance(record.snapshot, dict) else {}
            ledger.update(
                task_id,
                "waiting_for_decision",
                summary=reason,
                snapshot={**current_snapshot, "waiting_for_instruction": True, "reason": reason},
            )

    def reject(self, task_id: str, *, summary: str) -> None:
        """未受理即拒收（收工态 / 队列中被撤）：账面直接写 cancelled，不进追踪清单。"""
        ledger = self._framework_ledger()
        if ledger is not None:
            ledger.update(task_id, "cancelled", summary=summary)

    def cancel(self, task_id: str, *, summary: str) -> bool:
        """撤单：在追踪清单内则移除并账面 cancelled；不在清单（未被本 Agent 吸收）返回 False。"""
        if task_id not in self._batch_ids and task_id not in self._finished_ids:
            return False
        if task_id in self._batch_ids:
            self._batch_ids.remove(task_id)
        if task_id in self._finished_ids:
            self._finished_ids.remove(task_id)
        ledger = self._framework_ledger()
        if ledger is not None:
            ledger.update(task_id, "cancelled", summary=summary)
        return True

    def settle_on_live_end(self) -> int:
        """下播强制结算：名下所有非终态 agent 任务置 cancelled，摘要区分当时是在做、没开工还是搁下。"""
        ledger = self._framework_ledger()
        if ledger is None:
            return 0
        settled = 0
        for task_id in ledger.active_task_ids():
            record = ledger.get(task_id)
            if record is None or record.source != "agent" or record.executor != self._agent_name:
                continue
            if ledger.update(task_id, "cancelled", summary=_live_end_summary(record)) is not None:
                settled += 1
            else:
                logger.debug(f"下播结算跳过任务 {task_id}（幂等或已终态移除）")
        self._batch_ids.clear()
        self._finished_ids.clear()
        return settled
