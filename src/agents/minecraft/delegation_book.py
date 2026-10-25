"""委派账：MinecraftAgent 名下委派任务的批内追踪与账面写入的单一管辖。

委派从受理到终态是一条完整生命周期：受理入批 → 批次开跑重写 running →
交付写终态 / 挂起写待定夺 / 撤单出账 / 下播强制结算。账面写入走注入的
``task_tracker``（框架侧任务账本）；``task_tracker`` 缺省 None = 不写框架账
（无任务基建的装配降级），此时追踪清单也保持为空。

两份清单是批的生命周期状态，时序契约在此锁定：
- 本批已吸收、还没标 running 的任务号（``absorb`` 进来）；
- 已在账 running、批结束时等着写终态的任务号（``mark_running`` 归拢）。
批结束时账面必须已登记——未走过 ``absorb`` → ``mark_running`` 的任务号
写终态会落空（框架账本不认识它），这是撤单与终态结算的正确性前提。

``finished_ids`` 只读视图供目标账本联动结算：目标到终态时本批吸收的
委派共享结算（见 ``GoalLedger._settle_batch``）。消息队列与停手通知的
注入留在 Agent——那是消息路由的事，账只管账。
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from src.modules.logging import get_logger

__all__ = ["DelegationBook"]

logger = get_logger("MinecraftDelegationBook")


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
        self._batch_ids.append(task_id)

    @property
    def finished_ids(self) -> Tuple[str, ...]:
        """已入账 running、待本批写终态的任务号（只读快照，目标账本联动结算用）。"""
        return tuple(self._finished_ids)

    def mark_running(self) -> None:
        """批次开跑：本批吸收的委派写 running 并入终态追踪清单；上批留下的恢复原任务。"""
        ledger = self._framework_ledger()
        if ledger is None:
            self._batch_ids.clear()
            return
        for task_id in self._finished_ids:
            ledger.update(task_id, "running", summary="执行 Agent 已恢复原任务")
        for task_id in self._batch_ids:
            ledger.update(task_id, "running", summary="执行 Agent 已开始处理")
            self._finished_ids.append(task_id)
        self._batch_ids.clear()

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
            ledger.update(
                task_id,
                "waiting_for_decision",
                summary=reason,
                snapshot={"waiting_for_instruction": True, "reason": reason},
            )

    def reject(self, task_id: str, *, summary: str) -> None:
        """未受理即拒收（收工态 / 排队中被撤）：账面直接写 cancelled，不进追踪清单。"""
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
        """下播强制结算：名下所有非终态 agent 任务置 cancelled（结算原因"下播自动收尾"）。

        挂在 ``waiting_for_decision`` 的悬置条目若不收尾，任务账本跨场残留、
        下次开播前状态不清——收工兜底（丢消息 + 收工态拒收新委派）之外
        把已在账的也一并结清。单写者规则不破：agent 型任务本就由执行
        Agent（自己）写入。

        Returns:
            结算条数。
        """
        ledger = self._framework_ledger()
        if ledger is None:
            return 0
        settled = 0
        for task_id in ledger.active_task_ids():
            record = ledger.get(task_id)
            if record is None or record.source != "agent" or record.executor != self._agent_name:
                continue
            if ledger.update(task_id, "cancelled", summary="下播自动收尾") is not None:
                settled += 1
            else:
                logger.debug(f"下播结算跳过任务 {task_id}（幂等或已终态移除）")
        # 追踪清单随账面清空：跨场不再有可"重写 running"的旧任务
        self._batch_ids.clear()
        self._finished_ids.clear()
        return settled
