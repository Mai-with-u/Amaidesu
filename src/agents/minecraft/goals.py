"""后台目标跟踪：本 Agent 下达给 MaiCraft 的目标，靠 ``events`` 长轮询知道它们何时需要处理。

MaiCraft v1 把目标运行的处境变化写进一条事件流（``events``），宿主带着上次读到的游标去读，
没有新事件时服务端最多等一会儿再空手返回。这里只盯本 Agent 自己下达、还没结束的目标：

- 目标提问、被暂停、恢复、结束时，再用 ``task(get)`` 取它此刻的完整样子交给回调——
  事件只说"变了"，完整结果（变化、问题、剩下的部分）以查询为准；
- 与目标无关的事件（生存需求插进来的临时任务、角色自己处理不了的需求、角色死亡）原样交给身体事件回调；
- Mod 自己挂出的决策（死亡恢复：编号是负数，不是谁下达的目标）提问时，读一次交给决策回调，等模型按选项回答；
- 事件流换了（换世界、重进世界）时游标作废，逐个重新查询在跟踪的目标：查得到就照常交回调，
  查不到或已经不是原来那个目标，就按"不在了"交给回调，不让它挂在待办里永远等不到结果。

连接没就绪或读取失败时退避重试，不抛出；跟踪循环本身不调用模型，空闲时只是一条挂着的长轮询。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.modules.logging import get_logger

from .maicraft import AWAITING_ANSWER, EVENTS, TASK, GoalRun, MaicraftReply, events_page_of, goal_run_of

logger = get_logger("MinecraftGoals")

# 一次长轮询最多等多久：远低于 MCP 请求超时（默认 90 秒），服务端上限是 60 秒。
DEFAULT_WAIT_MS = 25_000
# 连接没就绪或读取失败后，隔多久再试；连续失败时逐步放慢，最慢半分钟一次。
_RETRY_DELAYS_S = (1.0, 2.0, 5.0, 10.0, 30.0)
# 一次长轮询空手返回得比这还快，说明服务端没有等：停这么久再读，避免空转。
_EMPTY_READ_FLOOR_S = 1.0
# 这些事件说明目标的处境变了，需要取它此刻的完整样子。
_GOAL_CHANGE_KINDS = frozenset({"asked", "paused", "resumed", "finished"})
# 与目标无关的身体事件：生存需求的临时任务开始与结束、处理不了的需求、角色死亡。
_BODY_EVENT_KINDS = frozenset({"temporary_task_started", "temporary_task_finished", "need_unhandled", "character_died"})

McpCall = Callable[[str, Dict[str, Any]], Awaitable[MaicraftReply]]
GoalChanged = Callable[[GoalRun], Awaitable[None]]
GoalGone = Callable[[int, str], Awaitable[None]]
DecisionAsked = Callable[[GoalRun], Awaitable[None]]
BodyEvent = Callable[[Dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class TrackedGoal:
    """一个在跟踪的目标：编号、下达它时是哪种能力（换世界后用来认出是不是同一个）。"""

    goal_id: int
    ability: str
    purpose: str


class GoalWatch:
    """读事件流、认出本 Agent 的目标、在它们需要处理时交给回调。"""

    def __init__(
        self,
        *,
        call: McpCall,
        connected: Callable[[], bool],
        on_goal_changed: GoalChanged,
        on_goal_gone: GoalGone,
        on_body_event: BodyEvent,
        on_decision_asked: Optional[DecisionAsked] = None,
        wait_ms: int = DEFAULT_WAIT_MS,
    ) -> None:
        self._call = call
        self._connected = connected
        self._on_goal_changed = on_goal_changed
        self._on_goal_gone = on_goal_gone
        self._on_body_event = on_body_event
        self._on_decision_asked = on_decision_asked
        self._wait_ms = wait_ms
        self._goals: Dict[int, TrackedGoal] = {}
        self._stream_id: Optional[str] = None
        self._cursor = 0
        self._failures = 0
        self._task: Optional[asyncio.Task[None]] = None

    # ----- 跟踪名单 -----

    def track(self, run: GoalRun) -> None:
        """登记一个刚下达（或恢复）的目标；已经结束的不登记。"""
        if run.finished:
            return
        self._goals[run.goal_id] = TrackedGoal(run.goal_id, run.ability, run.purpose)

    def untrack(self, goal_id: int) -> None:
        self._goals.pop(goal_id, None)

    def tracking(self, goal_id: int) -> bool:
        return goal_id in self._goals

    def tracked_ids(self) -> List[int]:
        return sorted(self._goals)

    def clear(self) -> None:
        """换了逻辑任务或连接重建：名单清空，游标保留（流没变时不重读旧事件）。"""
        self._goals.clear()

    # ----- 运行 -----

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 - 停机时的循环异常只记日志
            logger.warning(f"目标跟踪循环退出异常: {type(exc).__name__}: {exc}")

    def forget_stream(self) -> None:
        """连接重建后服务端可能换了进程：下次读取当作首次，重新建立游标并核对在跟踪的目标。"""
        self._stream_id = None
        self._cursor = 0

    async def _loop(self) -> None:
        while True:
            if not self._connected():
                await self._back_off()
                continue
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单次读取异常不杀死跟踪循环
                logger.warning(f"读取任务事件出错，稍后重试: {type(exc).__name__}: {exc}", exc=True)
                await self._back_off()

    async def _back_off(self) -> None:
        delay = _RETRY_DELAYS_S[min(self._failures, len(_RETRY_DELAYS_S) - 1)]
        self._failures += 1
        await asyncio.sleep(delay)

    async def step(self) -> None:
        """读一页事件并处理；首次读取与事件流更换时只建立游标，在跟踪的目标逐个重新查询。"""
        first = self._stream_id is None
        arguments: Dict[str, Any] = {"wait_ms": 0 if first else self._wait_ms}
        if not first:
            arguments["stream_id"] = self._stream_id
            arguments["after_cursor"] = self._cursor
        started = asyncio.get_running_loop().time()
        reply = await self._call(EVENTS, arguments)
        if not reply.ok:
            logger.warning(f"读取任务事件失败：{reply.error_code} {reply.error_message}")
            await self._back_off()
            return
        page = events_page_of(reply.data)
        if page is None:
            logger.warning(f"任务事件的回复读不懂，稍后重试：{reply.raw}")
            await self._back_off()
            return
        self._failures = 0
        if first or page.cursor_status != "valid" or page.stream_id != self._stream_id:
            # 首次读取：之前的事件发生在本 Agent 接手之前，不补发；事件流换了：旧游标作废，
            # 在跟踪的目标可能随换世界不在了，逐个查清楚。
            changed = not first
            self._stream_id = page.stream_id
            self._cursor = page.cursor
            await self._skip_backlog(page.has_more)
            if changed or self._goals:
                await self._resync()
            return
        for event in page.events:
            await self._absorb(event)
        self._cursor = page.cursor
        if not page.events and asyncio.get_running_loop().time() - started < _EMPTY_READ_FLOOR_S:
            # 服务端没按要求等就空手返回（旧版本或异常）：停一下再读，不让跟踪循环空转。
            await asyncio.sleep(_EMPTY_READ_FLOOR_S)

    async def _skip_backlog(self, has_more: bool) -> None:
        """不带游标读到的是流里最早保留的一页：一页页翻到最新，旧事件不补发。"""
        while has_more:
            reply = await self._call(EVENTS, {"stream_id": self._stream_id, "after_cursor": self._cursor, "wait_ms": 0})
            page = events_page_of(reply.data) if reply.ok else None
            if page is None or page.stream_id != self._stream_id:
                # 翻页途中流又换了或读失败：下一轮按首次读取重来。
                self.forget_stream()
                return
            self._cursor = page.cursor
            has_more = page.has_more

    async def _absorb(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        goal_id = event.get("task_id")
        if kind in _BODY_EVENT_KINDS:
            await self._on_body_event(event)
            return
        if kind == "asked" and isinstance(goal_id, int) and goal_id < 0 and goal_id not in self._goals:
            # 负数编号是 Mod 自己挂出的决策（死亡恢复），不是谁下达的目标：不跟踪，读一次交给决策回调。
            await self._decision_asked(goal_id)
            return
        if not isinstance(goal_id, int) or goal_id not in self._goals or kind not in _GOAL_CHANGE_KINDS:
            return
        await self._refresh(goal_id)

    async def _refresh(self, goal_id: int) -> None:
        """取目标此刻的完整样子交给回调；结束了就不再跟踪。查询失败留着，等下一条事件或重新同步。"""
        reply = await self._call(TASK, {"operation": "get", "task_id": goal_id})
        tracked = self._goals.get(goal_id)
        if tracked is None:
            return
        if not reply.ok:
            if reply.error_code == "unknown_id":
                self.untrack(goal_id)
                await self._on_goal_gone(goal_id, "MaiCraft 里查不到这个目标了")
            else:
                logger.warning(f"查询目标 {goal_id} 失败，等下一条事件再查：{reply.error_code} {reply.error_message}")
            return
        run = goal_run_of(reply.data)
        if run is None or run.goal_id != goal_id:
            logger.warning(f"目标 {goal_id} 的查询结果读不懂：{reply.raw}")
            return
        if tracked.ability and run.ability and run.ability != tracked.ability:
            # 换了世界后编号可能撞上别的目标：能力都不一样，说明原来那个目标已经不在这个世界里。
            self.untrack(goal_id)
            await self._on_goal_gone(goal_id, f"现在这个编号是另一个目标（{run.ability}），原来的目标不在这个世界里")
            return
        if run.finished:
            self.untrack(goal_id)
        await self._on_goal_changed(run)

    async def _decision_asked(self, decision_id: int) -> None:
        """取 Mod 挂出的决策此刻的样子：还在等回答就交给决策回调；读不到或已答复就不打扰模型。"""
        if self._on_decision_asked is None:
            return
        reply = await self._call(TASK, {"operation": "get", "task_id": decision_id})
        run = goal_run_of(reply.data) if reply.ok else None
        if run is None or run.state != AWAITING_ANSWER:
            logger.info(
                f"决策 {decision_id} 已经不在等回答，不唤醒任务：{reply.error_code or (run.state if run else '读不懂')}"
            )
            return
        await self._on_decision_asked(run)

    async def _resync(self) -> None:
        for goal_id in list(self._goals):
            await self._refresh(goal_id)


__all__ = ["DEFAULT_WAIT_MS", "GoalWatch", "TrackedGoal"]
