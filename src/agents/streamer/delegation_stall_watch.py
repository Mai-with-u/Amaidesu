"""委派停滞告警的决断兜底（Planner 内部件，不是工具、不进注册表）。

停滞告警（``task.changed`` 的 alert 载荷）此前只记日志、进不了主播决策上下文，
告警发了主播也做不出决断（场 65：全场 11 次告警，主播侧零反应）。本件按同一
委派计数告警，输出两样东西：

- 每次告警一条决策窗必读材料行：委派原话、已停多久、第几次告警；
- 排队（queued）与停着等定夺（waiting_for_decision）的委派连续收到第 N 次
  告警仍无决断（无撤单 / 无改单 / 无账面变化）→ 判自动撤单，由调用方经
  ``framework_task_cancel`` 撤掉并补一条场记事实（"这件事我喊停了"），主播
  下一窗顺带交代。N 可配（``[tools.tasks].stall_auto_cancel_alerts``）。

running 与急件抢占暂放（suspended）不自动杀：运行中任务有自己的升级与目标
停滞通路；暂放件的恢复由急件收场驱动，不归停滞告警管。

计数与"有无决断"的判定方式：停滞告警本身会刷新账面 ``updated_at_ms``，时间
戳判不出决断；这里对每次告警比对（状态，过滤掉旁注键的快照）——旁注是执行
侧随手记的一句判断、不算进展，过滤口径与委派受理核心的看门狗一致
（instruction / own_words 是受理时落下的原文，也不随进展变化）。两次告警之
间这份对比发生了变化，或收到非告警的账面变化（真状态迁移），都算有进展 /
有决断，重新计数；账面走到终态（含被撤）即出册。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from src.modules.logging import get_logger
from src.modules.tools.tasks import REPLY_NOTE_AT_KEY, REPLY_NOTE_KEY, TERMINAL_TASK_STATES
from src.modules.utils.time_utils import now_ms

__all__ = ["DelegationStallWatch", "StallAlertOutcome"]

logger = get_logger("DelegationStallWatch")

# 快照里不算进展的键：受理原文与执行侧旁注（口径与 src/modules/agents/control.py 看门狗一致）
_NON_PROGRESS_KEYS = frozenset({"instruction", "own_words", REPLY_NOTE_KEY, REPLY_NOTE_AT_KEY})
# 第 N 次告警可自动撤单的状态：排队（还没送到执行方）与停着等主播定夺（执行方上报卡住后停手）
_AUTO_CANCEL_STATUSES = frozenset({"queued", "waiting_for_decision"})


def _progress_view(snapshot: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """快照去掉旁注与受理原文后的对比视图（决断/进展判定用它）。"""
    snap = snapshot if isinstance(snapshot, Mapping) else {}
    return {key: value for key, value in snap.items() if key not in _NON_PROGRESS_KEYS}


@dataclass(frozen=True, slots=True)
class StallAlertOutcome:
    """一次停滞告警的处置结果：决策窗材料行与是否判自动撤单。

    ``material`` 是未经脱敏的行原文（含委派原话），入账侧统一脱敏。
    """

    material: str
    own_words: str
    executor: str
    auto_cancel: bool


@dataclass(slots=True)
class _Entry:
    """同一委派的告警计数册：当前对比基准、自上次进展起的告警次数与基准时刻。"""

    digest: tuple[str, Dict[str, Any]]
    count: int = 0
    base_ms: int = 0
    own_words: str = ""
    executor: str = ""


class DelegationStallWatch:
    """委派停滞告警的计数与决断判定。非线程安全；仅在 Agent 事件循环内使用。"""

    def __init__(self, *, auto_cancel_alerts: int = 3, clock: Optional[Callable[[], int]] = None) -> None:
        """Args:
        auto_cancel_alerts: 自动撤单的告警次数阈值（第 N 次告警仍无决断即撤）；
            传 <=0 时按 1 处理（每次告警都判）。
        clock: 注入时钟（返回 Unix 毫秒）；缺省取真实时刻，测试注入假时钟。
        """
        self._threshold = max(int(auto_cancel_alerts), 1)
        self._clock = clock or now_ms
        self._entries: Dict[str, _Entry] = {}

    def note_alert(self, payload: Any) -> Optional[StallAlertOutcome]:
        """停滞告警计数：返回决策窗材料与是否判自动撤单；非委派告警返回 None。

        ``payload`` 是 ``TaskChangedPayload``（alert=True）。同一任务两次告警之间
        账面对比没变 → 累计计数；变了 → 算有进展/决断，从这次重新数。
        """
        task_id = str(payload.task_id)
        status = str(payload.status)
        snapshot = payload.snapshot if isinstance(payload.snapshot, Mapping) else {}
        digest = (status, _progress_view(snapshot))
        entry = self._entries.get(task_id)
        if entry is None:
            entry = _Entry(
                digest=digest,
                base_ms=int(payload.timestamp_ms or self._clock()),
                own_words=self._own_words(snapshot),
                executor=str(payload.executor or ""),
            )
            self._entries[task_id] = entry
        elif digest != entry.digest:
            # 账面变了（决断或进展）：从这次告警重新计数
            entry.digest = digest
            entry.base_ms = int(payload.timestamp_ms or self._clock())
            entry.own_words = self._own_words(snapshot) or entry.own_words
            entry.count = 0
        entry.count += 1
        stopped_min = max(self._clock() - entry.base_ms, 0) // 60_000
        ordinal = "首次" if entry.count == 1 else f"第 {entry.count} 次"
        material = (
            f"[停滞提醒] 派去 {entry.executor} 的委派「{entry.own_words}」已停了约 {stopped_min} 分钟"
            f"没有进展（{ordinal}提醒）"
        )
        if status in _AUTO_CANCEL_STATUSES:
            material += "，一直拿不出决断就把这件撤掉"
        auto_cancel = entry.count >= self._threshold and status in _AUTO_CANCEL_STATUSES
        if auto_cancel:
            logger.warning(
                f"委派停滞决断兜底: task_id={task_id} status={status} 已 {entry.count} 次告警无决断，判自动撤单"
            )
        return StallAlertOutcome(
            material=material, own_words=entry.own_words, executor=entry.executor, auto_cancel=auto_cancel
        )

    def note_change(self, payload: Any) -> None:
        """非告警的账面变化：终态出册；其余真变化算有进展/决断，重置计数基准。"""
        task_id = str(payload.task_id)
        entry = self._entries.get(task_id)
        if entry is None:
            return
        if str(payload.status) in TERMINAL_TASK_STATES:
            self._entries.pop(task_id, None)
            return
        snapshot = payload.snapshot if isinstance(payload.snapshot, Mapping) else {}
        entry.digest = (str(payload.status), _progress_view(snapshot))
        entry.base_ms = int(payload.timestamp_ms or self._clock())
        entry.count = 0

    @staticmethod
    def _own_words(snapshot: Mapping[str, Any]) -> str:
        """委派原话（主播自己的话）：旁注里没有就退回指令原文。"""
        own = str(snapshot.get("own_words") or "").strip()
        if own:
            return own
        return str(snapshot.get("instruction") or "").strip()
