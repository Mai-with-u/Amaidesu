"""直播近况同步：把开播、换环节、收播、场次回顾与整场记录推给 MaiBot 上的适配器插件。

MaiBot 7×24 常驻，Amaidesu 开播时才启动；QQ 那边的麦麦要知道"自己正在直播 /
刚播完一场"，只能由直播这边在事实发生时推过去。本模块只订阅事实事件并向外推送，
不持有办事过程、不被 LLM 调用、不采集数据——属于出站基础设施（同 StorageLedger
订阅事件写库的形态），不是工具 / Agent / 采集器。

推送形态：
- **近况快照**：每次都发完整状态（是否在播、标题、开播时刻、当前环节），而非增量。
  直播期间按心跳间隔重发，插件错过任何一次都能被下一次补齐，也据此判断直播是否
  仍在进行。
- **场次回顾**：回顾写好后单独发一次，失败按 5s / 30s / 2min 重试三次；插件明确拒收
  （4xx：对应开关没开、格式不对）时不再重试。
- **整场记录**：回顾写好时一并从库里读出这场的全部对话推过去（重试同上），插件把它
  回放进 MaiBot 的直播间会话、由聊天摘要吸收进长期记忆。挂在回顾事件上而不是收播事件
  上：进程退出时收播来不及推，回顾则会在下次启动时补写，整场记录随之补推。

彩排场次（``kind="rehearsal"``：模拟、回放、调试）不对外同步：开播、收播、回顾都按
事件里带的场次类型判断，进程重启后补写的回顾也一样。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, Optional, Protocol, Sequence, Set

import aiohttp

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.recap import StreamerRecapWrittenPayload
from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.logging import get_logger
from src.modules.types.session_kind import SESSION_KIND_REHEARSAL
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

logger = get_logger("LiveStatusReporter")

# 回顾与整场记录推送失败后的重试间隔（毫秒）
_RETRY_DELAYS_MS = (5_000, 30_000, 120_000)
# 一次推送的结果：送到 / 暂时失败（可重试）/ 被插件拒收（重试也没用）
_DELIVERED, _FAILED, _REJECTED = "delivered", "failed", "rejected"


class StatusSender(Protocol):
    """把一份近况 JSON 送到适配器插件；失败抛异常（由上报器记日志与重试）。"""

    async def send(self, body: Dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class StatusRejected(RuntimeError):
    """插件明确拒收（HTTP 4xx：对应开关没开、令牌或格式不对）：原样重试也不会成功。"""


class TranscriptSource(Protocol):
    """按场次主键给出推给插件的整场记录；没有可推的（空场次、彩排场次等）返回 None。"""

    async def load(self, live_session_id: int) -> Optional[Dict[str, Any]]: ...


class HttpStatusSender:
    """经 HTTP POST 推送，携带 ``Authorization: Bearer <令牌>``。"""

    def __init__(self, *, url: str, token: str, timeout_ms: int) -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
        self._session: Optional[aiohttp.ClientSession] = None

    async def send(self, body: Dict[str, Any]) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        async with self._session.post(self._url, json=body, headers=self._headers) as response:
            if response.status >= 300:
                detail = (await response.text())[:200]
                error = StatusRejected if 400 <= response.status < 500 else RuntimeError
                raise error(f"HTTP {response.status}: {detail}")

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None


@dataclass
class _LiveState:
    """进行中场次的近况（上报器自己维护的推送底稿，不是场次事实源）。"""

    live_session_id: int
    title: str
    platform: str
    room_id: str
    started_at_ms: int
    segment: Optional[Dict[str, Any]] = None


class LiveStatusReporter:
    """订阅场次、流程单与回顾事件，把近况推给适配器插件。

    生命周期按组合根两段装配：``setup`` 只接线订阅（第①段），心跳任务随
    ``live.started`` 才启动；``stop`` 退订并收尾全部后台任务与连接。
    """

    def __init__(
        self,
        *,
        event_bus: "EventBus",
        sender: StatusSender,
        heartbeat_interval_ms: int = 60_000,
        retry_delays_ms: Sequence[int] = _RETRY_DELAYS_MS,
        transcript_source: Optional[TranscriptSource] = None,
        clock_ms: Callable[[], int] = now_ms,
    ) -> None:
        self._event_bus = event_bus
        self._sender = sender
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._retry_delays_ms = tuple(retry_delays_ms)
        self._transcript_source = transcript_source
        self._clock_ms = clock_ms
        self._live: Optional[_LiveState] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._push_tasks: Set[asyncio.Task[None]] = set()
        self._last_failure = ""
        self._subscribed = False

    def setup(self) -> None:
        """接线：订阅开播、收播、流程单与回顾事件（不触发任何推送）。"""
        if self._subscribed:
            return
        self._event_bus.on(CoreEvents.LIVE_STARTED, self._on_live_started, model_class=LiveStartedPayload)
        self._event_bus.on(CoreEvents.LIVE_ENDED, self._on_live_ended, model_class=LiveEndedPayload)
        self._event_bus.on(CoreEvents.RUNDOWN_CHANGED, self._on_rundown_changed, model_class=RundownChangedPayload)
        self._event_bus.on(
            CoreEvents.STREAMER_RECAP_WRITTEN, self._on_recap_written, model_class=StreamerRecapWrittenPayload
        )
        self._subscribed = True

    async def stop(self) -> None:
        """退订事件，取消心跳与回顾、整场记录的推送，关闭连接（幂等）。"""
        if self._subscribed:
            self._event_bus.off(CoreEvents.LIVE_STARTED, self._on_live_started)
            self._event_bus.off(CoreEvents.LIVE_ENDED, self._on_live_ended)
            self._event_bus.off(CoreEvents.RUNDOWN_CHANGED, self._on_rundown_changed)
            self._event_bus.off(CoreEvents.STREAMER_RECAP_WRITTEN, self._on_recap_written)
            self._subscribed = False
        tasks = [task for task in (self._heartbeat_task, *self._push_tasks) if task is not None]
        self._heartbeat_task = None
        self._push_tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._sender.close()

    # ---- 事件处理 ----

    async def _on_live_started(self, event_name: str, payload: LiveStartedPayload, source: str) -> None:
        """开播：记下场次信息，推一份"正在直播"快照，并开始心跳。"""
        if payload.kind == SESSION_KIND_REHEARSAL:
            return
        self._live = _LiveState(
            live_session_id=payload.live_session_id,
            title=payload.title or "",
            platform=payload.platform,
            room_id=payload.room_id,
            started_at_ms=payload.started_at_ms,
        )
        await self._send(self._snapshot(), "开播近况")
        self._start_heartbeat()

    async def _on_rundown_changed(self, event_name: str, payload: RundownChangedPayload, source: str) -> None:
        """换环节：只在直播中同步（开播前在面板里编辑流程单不是近况）。"""
        if self._live is None:
            return
        if payload.total > 0 and payload.index >= payload.total:
            segment: Optional[Dict[str, Any]] = {
                "title": "",
                "index": payload.total,
                "total": payload.total,
                "finished": True,
            }
        elif payload.index < 0 or not payload.segment_id:
            segment = None
        else:
            segment = {
                "title": payload.segment_title,
                "index": payload.index,
                "total": payload.total,
                "finished": False,
            }
        self._live.segment = segment
        await self._send(self._snapshot(), "环节近况")

    async def _on_live_ended(self, event_name: str, payload: LiveEndedPayload, source: str) -> None:
        """收播：停心跳，推一份"已下播"快照（空场次被丢弃时一并告知，插件不再提这场）。"""
        if payload.kind == SESSION_KIND_REHEARSAL:
            return
        live = self._live
        if live is None or live.live_session_id != payload.live_session_id:
            return
        self._live = None
        await self._stop_heartbeat()
        body = self._snapshot_of(live, live_flag=False)
        body["ended_at_ms"] = payload.ended_at_ms
        body["discarded"] = bool(payload.empty_discarded)
        await self._send(body, "收播近况")

    async def _on_recap_written(self, event_name: str, payload: StreamerRecapWrittenPayload, source: str) -> None:
        """回顾写好：后台推回顾与整场记录，失败按退避重试，不阻塞事件分发。"""
        if payload.kind == SESSION_KIND_REHEARSAL:
            return
        body = {
            "type": "recap",
            "live_session_id": payload.live_session_id,
            "started_at_ms": payload.started_at_ms,
            "ended_at_ms": payload.ended_at_ms,
            "text": payload.recap_text,
        }
        self._spawn(self._send_with_retry(body, "场次回顾"), "maibot-sync-recap")
        if self._transcript_source is not None:
            self._spawn(self._push_transcript(payload.live_session_id), "maibot-sync-transcript")

    # ---- 推送 ----

    def _snapshot(self) -> Dict[str, Any]:
        assert self._live is not None  # noqa: S101 调用方已 guard
        return self._snapshot_of(self._live, live_flag=True)

    @staticmethod
    def _snapshot_of(live: _LiveState, *, live_flag: bool) -> Dict[str, Any]:
        return {
            "type": "snapshot",
            "live": live_flag,
            "live_session_id": live.live_session_id,
            "title": live.title,
            "platform": live.platform,
            "room_id": live.room_id,
            "started_at_ms": live.started_at_ms,
            "ended_at_ms": 0,
            "discarded": False,
            "segment": dict(live.segment) if live.segment is not None else None,
        }

    async def _send(self, body: Dict[str, Any], what: str) -> bool:
        """推送一份近况，送到返回 True。"""
        return await self._deliver(body, what) == _DELIVERED

    async def _deliver(self, body: Dict[str, Any], what: str) -> str:
        """推送一份近况，返回送到 / 失败 / 被拒收；同一原因的失败只告警一次，恢复后再失败重新告警。"""
        body = {**body, "sent_at_ms": self._clock_ms()}
        try:
            await self._sender.send(body)
        except Exception as exc:  # noqa: BLE001 - 推送失败不影响直播主链路
            failure = f"{type(exc).__name__}: {exc}"
            if failure != self._last_failure:
                self._last_failure = failure
                logger.warning(f"{what}推送给 MaiBot 失败（后续同因失败降为 debug）: {failure}")
            else:
                logger.debug(f"{what}推送给 MaiBot 仍然失败（同因）: {failure}")
            return _REJECTED if isinstance(exc, StatusRejected) else _FAILED
        if self._last_failure:
            logger.info("直播近况推送已恢复")
        self._last_failure = ""
        return _DELIVERED

    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._push_tasks.add(task)
        task.add_done_callback(self._push_tasks.discard)

    async def _push_transcript(self, live_session_id: int) -> None:
        """读出整场记录推给插件；读库失败只记日志，不影响近况与回顾。"""
        assert self._transcript_source is not None  # noqa: S101 调用方已 guard
        try:
            body = await self._transcript_source.load(live_session_id)
        except Exception as exc:  # noqa: BLE001 - 读库失败不影响直播主链路
            logger.warning(f"读取场次 {live_session_id} 的整场记录失败，不推送: {exc}", exc=True)
            return
        if body is None:
            return
        await self._send_with_retry(body, "整场记录")

    async def _send_with_retry(self, body: Dict[str, Any], what: str) -> None:
        """推送并按退避重试；插件拒收（如没开整场记录回放）时不再重试。"""
        for delay_ms in (0, *self._retry_delays_ms):
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)
            outcome = await self._deliver(body, what)
            if outcome == _DELIVERED:
                return
            if outcome == _REJECTED:
                logger.info(f"{what}（场次 {body['live_session_id']}）被插件拒收，不再重试")
                return
        logger.warning(f"{what}（场次 {body['live_session_id']}）多次推送失败，已放弃")

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="maibot-sync-heartbeat")

    async def _stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _heartbeat_loop(self) -> None:
        """直播期间按间隔重发完整快照：补齐插件错过的推送，也让插件知道直播仍在进行。"""
        while self._live is not None:
            await asyncio.sleep(self._heartbeat_interval_ms / 1000)
            if self._live is None:
                return
            await self._send(self._snapshot(), "直播心跳")
