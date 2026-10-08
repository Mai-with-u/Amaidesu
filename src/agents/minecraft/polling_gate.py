"""LLM 忙等治理闸：任务失败唤醒退避与无进展决策合并。

背景（实证）：任务 failed 事件一到立即触发下一轮完整 LLM 调用，失败→重试→
再失败的循环里调用间隔仅 1-2 秒，每次 prompt 平均 28K token——纯忙等轮询。
提示词约束住了方向但模型仍会轮询，治理主体必须是框架层强制。

两个闸都在 LLM 决策**之前**：
- ``FailureBackoffGate``：任务失败对决策唤醒的退避。连续失败逐次拉长唤醒
  间隔（如 30s→60s→120s），攒到阈值次数才立即升级给 LLM 决策（升级后仍按
  封顶间隔节流，防止"升级→重试→再失败"重新缩回秒级循环）；一旦任务回到
  运行/成功等状态即复位。退避期失败事实先攒着，唤醒时一并交给模型。
- ``NoProgressGate``：短时间窗口内的重复"无进展"决策直接并入等待（让出给
  宿主/Mod 事件），不再发起推理。窗口用注入时钟度量，测试可确定性地推进。

时间统一毫秒；时钟经构造注入（返回 Unix/单调毫秒均可，只用于差值比较）。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

__all__ = ["FailureBackoffGate", "NoProgressGate", "default_clock_ms"]

# 每个任务最多保留几条近期失败事实（唤醒上下文里复述用，多了淹没重点）
_MAX_FAILURE_FACTS = 5


def default_clock_ms() -> int:
    """缺省时钟：单调毫秒，只用于同一闸内的差值比较。"""
    return int(time.monotonic() * 1000)


@dataclass
class _FailureRecord:
    """单个后台任务的连续失败账目。"""

    count: int = 0
    facts: list[str] = field(default_factory=list)
    # 是否已经发生过一次"立即升级"（升级后继续失败只按封顶间隔节流）
    escalated: bool = False
    # 退避期攒下的唤醒内容（失败事实已含），到期由宿主注入
    pending_wakeup: str | None = None


class FailureBackoffGate:
    """任务失败 → LLM 决策唤醒的退避闸（按任务号独立计数）。"""

    def __init__(
        self,
        *,
        delays_ms: Sequence[int] = (30_000, 60_000, 120_000),
        escalate_after: int = 3,
    ) -> None:
        """Args:
        delays_ms: 连续失败第 1..N 次的唤醒推迟（毫秒），超出取最后一档封顶。
        escalate_after: 连续失败达到该次数时立即升级为 LLM 决策（不再推迟）。
        """
        self._delays_ms = tuple(delays_ms)
        self._escalate_after = max(1, escalate_after)
        self._tasks: dict[str, _FailureRecord] = {}

    def on_failure(self, task_id: str, summary: str) -> tuple[int, int]:
        """记一次失败，返回 (本次唤醒推迟毫秒，0 = 立即唤醒, 连续失败次数)。

        阈值前的失败推迟唤醒（逐次拉长）；达到阈值的那次立即升级；升级之后
        的继续失败按封顶间隔节流——模型换方案或重试都要等真实间隔，不再回到
        秒级循环。失败事实（summary）留在账上，供唤醒内容与上下文使用。
        """
        record = self._tasks.setdefault(task_id, _FailureRecord())
        record.count += 1
        record.facts.append(summary or "未知原因")
        del record.facts[:-_MAX_FAILURE_FACTS]
        if record.count >= self._escalate_after and not record.escalated:
            record.escalated = True
            return 0, record.count
        delay_index = min(record.count - 1, len(self._delays_ms) - 1)
        return self._delays_ms[delay_index], record.count

    def failure_count(self, task_id: str) -> int:
        """当前连续失败次数（无账目即 0）。"""
        record = self._tasks.get(task_id)
        return record.count if record is not None else 0

    def facts(self, task_id: str) -> tuple[str, ...]:
        """近期失败事实（旧→新），供唤醒上下文复述。"""
        record = self._tasks.get(task_id)
        return tuple(record.facts) if record is not None else ()

    def store_wakeup(self, task_id: str, content: str) -> None:
        """退避期暂存唤醒内容；到期或提前核实时由宿主取走注入。"""
        record = self._tasks.setdefault(task_id, _FailureRecord())
        record.pending_wakeup = content

    def take_wakeup(self, task_id: str) -> str | None:
        """取走暂存的唤醒内容（取走即清）；无暂存返回 None。"""
        record = self._tasks.get(task_id)
        if record is None or record.pending_wakeup is None:
            return None
        content, record.pending_wakeup = record.pending_wakeup, None
        return content

    def recover(self, task_id: str) -> None:
        """任务回到非失败状态（受理/运行/成功/待决策/取消）：清账复位。"""
        self._tasks.pop(task_id, None)

    def reset(self) -> None:
        """换新任务方向：全部账目作废。"""
        self._tasks.clear()


class NoProgressGate:
    """无进展决策合并闸：窗口内的再次无进展直接并入等待，不再发起推理。"""

    def __init__(self, *, window_ms: int = 60_000) -> None:
        self._window_ms = window_ms
        self._last_mark_ms: int | None = None

    def in_window(self, now_ms: int) -> bool:
        """上一次无进展决策距今是否仍在窗口内。"""
        return self._last_mark_ms is not None and 0 <= now_ms - self._last_mark_ms <= self._window_ms

    def mark(self, now_ms: int) -> None:
        """记一次无进展决策的时刻。"""
        self._last_mark_ms = now_ms

    def reset(self) -> None:
        """取得真实进展或换新任务后清零，下一次无进展重新从提醒开始。"""
        self._last_mark_ms = None
