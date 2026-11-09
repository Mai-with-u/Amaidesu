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

``finished_ids`` 只读视图供目标账本联动写进展备注：批内目标收尾一步时，
本批吸收的委派在快照里记一条游戏侧进展（状态不动，见
``GoalLedger._note_batch_progress``）。消息队列与停手通知的注入留在
Agent——那是消息路由的事，账只管账。
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.tools.tasks import REPLY_NOTE_AT_KEY, REPLY_NOTE_KEY

__all__ = ["DelegationBook"]

logger = get_logger("MinecraftDelegationBook")


class DelegationBook:
    """委派任务的批内追踪与账面写入。非线程安全；仅在 Agent 事件循环内使用。"""

    def __init__(self, *, task_tracker: Optional[Any] = None, agent_name: str = "") -> None:
        self._task_tracker = task_tracker
        self._agent_name = agent_name
        self._batch_ids: List[str] = []
        self._finished_ids: List[str] = []
        # 被急件暂放的委派（进度保留，急件办完接着做）：与在办的分开记，
        # 批终态只写在办的，暂放的等恢复；账面状态由委派受理核心写 suspended
        self._suspended_ids: List[str] = []

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

    def tracked_ids(self) -> Tuple[str, ...]:
        """名下仍在追踪的委派任务号（待开跑 + 已入账 running + 暂放中；只读快照，急停撤单用）。"""
        return tuple(self._batch_ids) + tuple(self._finished_ids) + tuple(self._suspended_ids)

    def suspend(self, reason: str) -> int:
        """急件插队：在办的委派挪进暂放清单（账面状态由委派受理核心写）。返回挪了几件。"""
        suspended = len(self._finished_ids)
        self._suspended_ids.extend(self._finished_ids)
        self._finished_ids.clear()
        if suspended:
            logger.info(f"{self._agent_name} 名下 {suspended} 件在办委派暂放（{reason}）")
        return suspended

    def suspended_ids(self) -> Tuple[str, ...]:
        """暂放中的委派任务号（只读快照）。"""
        return tuple(self._suspended_ids)

    def resume_suspended(self) -> int:
        """急件办完：暂放的委派恢复在办（账面重写 running），回到批终态追踪清单。返回恢复几件。"""
        ledger = self._framework_ledger()
        resumed = self._suspended_ids
        self._suspended_ids = []
        if ledger is not None:
            for task_id in resumed:
                ledger.update(task_id, "running", summary="急事办完，恢复暂放的原任务")
        if resumed:
            logger.info(f"{self._agent_name} 恢复暂放的委派 {len(resumed)} 件")
        self._finished_ids.extend(resumed)
        return len(resumed)

    def mark_running(self) -> None:
        """批次开跑：本批吸收的委派写 running 并入终态追踪清单；上批留下的恢复原任务。"""
        ledger = self._framework_ledger()
        if ledger is None:
            self._batch_ids.clear()
            return
        for task_id in self._finished_ids:
            ledger.update(task_id, "running", summary="执行 Agent 已恢复原任务")
        # 被急件暂放的不在这里恢复：急件那一批开跑时恢复的话，急件交付会把暂放的也写成终态；
        # 恢复只在急件那一批收场之后（见 resume_suspended 的调用方）
        for task_id in self._batch_ids:
            ledger.update(task_id, "running", summary="执行 Agent 已开始处理")
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
        """委派停在待定夺：账面 ``waiting_for_decision``，追踪清单保留（批次重启时重写 running）。

        快照在既有记录上合并写入：受理时落下的 ``instruction`` 摘要必须保住，
        整体替换会把仪表盘任务卡的指令摘要冲掉。记录已不存在（终态移除）
        时按空快照起底，只写本次的待定夺标记。
        """
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
        """未受理即拒收（收工态 / 排队中被撤）：账面直接写 cancelled，不进追踪清单。"""
        ledger = self._framework_ledger()
        if ledger is not None:
            ledger.update(task_id, "cancelled", summary=summary)

    def cancel(self, task_id: str, *, summary: str) -> bool:
        """撤单：在追踪清单内则移除并账面 cancelled；不在清单（未被本 Agent 吸收）返回 False。"""
        if task_id not in self._batch_ids and task_id not in self._finished_ids and task_id not in self._suspended_ids:
            return False
        if task_id in self._batch_ids:
            self._batch_ids.remove(task_id)
        if task_id in self._finished_ids:
            self._finished_ids.remove(task_id)
        if task_id in self._suspended_ids:
            self._suspended_ids.remove(task_id)
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
        self._suspended_ids.clear()
        return settled
