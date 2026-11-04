"""直播近况同步：把开播、换环节、收播与整场记录推给 MaiBot 上的适配器插件。

MaiBot 7×24 常驻，Amaidesu 开播时才启动；QQ 那边的麦麦要知道"自己正在直播 /
刚播完一场"，只能由直播这边在事实发生时推过去。本模块只订阅事实事件并向外推送，
不持有办事过程、不被 LLM 调用、不采集数据——属于出站基础设施（同 StorageLedger
订阅事件写库的形态），不是工具 / Agent / 采集器。

推送形态：
- **近况快照**：每次都发完整状态（是否在播、标题、开播时刻、当前环节），而非增量。
  直播期间按心跳间隔重发，插件错过任何一次都能被下一次补齐，也据此判断直播是否
  仍在进行。
- **整场记录**：这场结束后从库里读出全部对话推过去（失败按 5s / 30s / 2min 重试
  三次；插件明确拒收时不再重试），插件把它回放进 MaiBot 的直播间会话、由聊天摘要
  吸收进长期记忆。收播事件先到、道别台词后落库，所以推送要等收播道别那一轮收场的
  ``planner.decision`` 再读库（事件到即读，不靠固定等待；等不到按上限兜底）。
  空场次被丢弃、或运营者删除的场次不推：内容已不存在或本意就是清掉它。

彩排场次（``kind="rehearsal"``：模拟、回放、调试）不对外同步：开播、收播都按事件
里带的场次类型判断，整场记录由来源侧按场次类型决定是否返回。

- **补推**：整场记录推成功（或被插件明确拒收）后写一条"已处理"标记（库文件旁的
  文本文件，按场次主键与结束时刻一起记）。启动时扫最近几天结束的正式场，标记里
  没有的补推一次——下播后很快关程序的场次不会永远丢。只补推正式场、已结束、有
  真正播出台词的场次；推送失败不写标记，留待下次，MaiBot 连不上时整批停下。标记
  不在数据库事务与备份恢复的管辖内：恢复旧库备份后可能对同一场重复补推一次（QQ
  里多一条），不会丢数据。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, List, Optional, Protocol, Sequence, Set, Tuple

import aiohttp

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.planner import PlannerDecisionPayload
from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.logging import get_logger
from src.modules.types.session_kind import SESSION_KIND_REHEARSAL
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.maibot_sync.transcript import TranscriptCandidate

logger = get_logger("LiveStatusReporter")

# 整场记录推送失败后的重试间隔（毫秒）
_RETRY_DELAYS_MS = (5_000, 30_000, 120_000)
# 收播道别那一轮收场的 planner.decision 触发原因串（TriggerCause.to_reason_string 的
# "proactive:signoff" 形态）：整场记录等这一轮跑完再读库，道别台词才在里面
_SIGNOFF_TRIGGER_REASON = "proactive:signoff"
# 等道别轮的兜底上限（秒）：道别轮没跑（异常、Agent 已停等）时也把记录推出去
_SIGNOFF_WAIT_TIMEOUT_S = 120.0
# 道别轮信号只在决策收场时发出，道别台词随后才派发；等一小段让 storage 侧写
# live_chat 的并发 handler 提交完。可注入（测试传 0），默认值只在生产路径生效
_TRANSCRIPT_SETTLE_DELAY_S = 0.3
# 一次推送的结果：送到 / 暂时失败（可重试）/ 被插件拒收（重试也没用）
_DELIVERED, _FAILED, _REJECTED = "delivered", "failed", "rejected"
# 启动补推最多看最近多少场正式场（不一次把历史全推出去）
_CATCH_UP_LIMIT = 20
# 启动补推只补这么久以内结束的场次（毫秒）：第一次启用标记或标记文件丢了时，
# 不把几个月前的场次整批推给 MaiBot
_CATCH_UP_WINDOW_MS = 3 * 24 * 3600 * 1000


class StatusSender(Protocol):
    """把一份近况 JSON 送到适配器插件；失败抛异常（由上报器记日志与重试）。"""

    async def send(self, body: Dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class StatusRejected(RuntimeError):
    """插件明确拒收（HTTP 4xx：对应开关没开、令牌或格式不对）：原样重试也不会成功。"""


class TranscriptSource(Protocol):
    """整场记录的来源：读一场的记录，以及启动补推要用的候选与"她说过话没有"。"""

    async def load(self, live_session_id: int) -> Optional[Dict[str, Any]]:
        """按场次主键给出推给插件的整场记录；没有可推的（空场次、彩排场次等）返回 None。"""
        ...

    async def list_catch_up_sessions(self, *, limit: int) -> List["TranscriptCandidate"]:
        """最近若干已结束的正式场（按开播倒序），供启动补推比对已处理标记。"""
        ...

    async def has_spoken_lines(self, live_session_id: int) -> bool:
        """这一场有没有真正播出的台词；一句没说过的场次不补推。"""
        ...


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


class TranscriptPushLog:
    """整场记录"已处理"标记：库文件旁的一个文本文件，随库走、不占数据库事务。

    每行一个 JSON：``{"session_id": <主键>, "ended_at_ms": <场次结束时刻>}``。主键与结束
    时刻一起判：恢复旧库备份后同一场的结束时刻可能不同，只比主键会把该补推的场次
    判成已推。写入只在文件末尾追加一行、不重写已有内容：读失败或有坏行时也不会把
    已有标记覆盖掉；读到半行、坏行时跳过那一行。

    这条边界要知道：标记在数据库之外，不受事务与备份恢复管辖。恢复旧库备份后，
    标记里可能还记着"已推过"的场次，也可能只有旧标记——最多导致同一场重复补推
    一次（QQ 里多一条整场记录），不会丢数据。
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    async def read(self) -> Set[Tuple[int, int]]:
        """全部已处理标记（场次主键, 结束时刻）；文件不存在为空，读不了抛 ``OSError``。"""
        return await asyncio.to_thread(self._read_marks)

    async def contains(self, live_session_id: int, ended_at_ms: int) -> bool:
        """这一场（按主键与结束时刻）是否已处理过；读不了抛 ``OSError``。"""
        return (int(live_session_id), int(ended_at_ms)) in await self.read()

    async def mark(self, live_session_id: int, ended_at_ms: int) -> None:
        """记下这一场已处理（推送成功或被插件明确拒收；暂时失败时不要调）。写不下抛 ``OSError``。"""
        async with self._lock:
            await asyncio.to_thread(self._append_mark, int(live_session_id), int(ended_at_ms))

    def _read_marks(self) -> Set[Tuple[int, int]]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return set()
        marks: Set[Tuple[int, int]] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
                marks.add((int(item["session_id"]), int(item["ended_at_ms"])))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.warning(f"整场记录推送标记有一行读不懂，跳过: {stripped[:80]}")
        return marks

    def _append_mark(self, live_session_id: int, ended_at_ms: int) -> None:
        """在文件末尾追加一行；上次写到一半（末尾没有换行）时先补一个换行，不和半行粘在一起。"""
        line = json.dumps({"session_id": live_session_id, "ended_at_ms": ended_at_ms}, ensure_ascii=False) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    line = "\n" + line
            handle.write(line.encode("utf-8"))


class LiveStatusReporter:
    """订阅场次、流程单与决策轮事件，把近况与整场记录推给适配器插件。

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
        push_log_path: Optional[Path] = None,
        catch_up_limit: int = _CATCH_UP_LIMIT,
        catch_up_window_ms: int = _CATCH_UP_WINDOW_MS,
        signoff_settle_s: float = _TRANSCRIPT_SETTLE_DELAY_S,
        clock_ms: Callable[[], int] = now_ms,
    ) -> None:
        """构造上报器。

        Args:
            event_bus: 事件总线，订阅开播、收播、流程单与决策轮事件。
            sender: 把近况 JSON 送到适配器插件的发送器。
            heartbeat_interval_ms: 直播期间重发完整近况的间隔（毫秒）。
            retry_delays_ms: 整场记录推送失败后的重试间隔（毫秒）。
            transcript_source: 整场记录来源；None = 不推整场记录。
            push_log_path: "已处理"标记文件的路径；None = 不记标记、也不做启动补推。
            catch_up_limit: 启动补推最多看最近多少场正式场。
            catch_up_window_ms: 启动补推只补这么久以内结束的场次（毫秒）。
            signoff_settle_s: 收到道别轮信号后等台词行落库的余量（秒）；测试可传 0。
            clock_ms: 当前时刻（毫秒），测试注入假时钟。
        """
        self._event_bus = event_bus
        self._sender = sender
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._retry_delays_ms = tuple(retry_delays_ms)
        self._transcript_source = transcript_source
        self._push_log = TranscriptPushLog(push_log_path) if push_log_path is not None else None
        self._catch_up_limit = max(1, int(catch_up_limit))
        self._catch_up_window_ms = max(0, int(catch_up_window_ms))
        self._signoff_settle_s = max(0.0, float(signoff_settle_s))
        self._clock_ms = clock_ms
        self._live: Optional[_LiveState] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._push_tasks: set[asyncio.Task[None]] = set()
        self._subscribed = False
        self._last_failure = ""
        # 挂起的整场记录：（场次主键, 道别轮收场信号）；None = 当前没有要推的记录
        self._pending_transcript: Optional[tuple[int, asyncio.Event]] = None

    def setup(self) -> None:
        """接线：订阅开播、收播、流程单与决策轮事件（不触发任何推送）。

        决策轮事件只用来等"收播道别那一轮跑完"的信号，据此决定整场记录何时读库。
        """
        if self._subscribed:
            return
        self._event_bus.on(CoreEvents.LIVE_STARTED, self._on_live_started, model_class=LiveStartedPayload)
        self._event_bus.on(CoreEvents.LIVE_ENDED, self._on_live_ended, model_class=LiveEndedPayload)
        self._event_bus.on(CoreEvents.RUNDOWN_CHANGED, self._on_rundown_changed, model_class=RundownChangedPayload)
        self._event_bus.on(
            CoreEvents.PLANNER_DECISION,
            self._on_planner_decision,
            model_class=PlannerDecisionPayload,
        )
        self._subscribed = True
        self._spawn_catch_up()

    async def stop(self) -> None:
        """退订事件，取消心跳与推送任务，关闭连接（幂等）。"""
        if self._subscribed:
            self._event_bus.off(CoreEvents.LIVE_STARTED, self._on_live_started)
            self._event_bus.off(CoreEvents.LIVE_ENDED, self._on_live_ended)
            self._event_bus.off(CoreEvents.RUNDOWN_CHANGED, self._on_rundown_changed)
            self._event_bus.off(CoreEvents.PLANNER_DECISION, self._on_planner_decision)
            self._subscribed = False
        self._pending_transcript = None
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
        # 空场次被丢弃、或运营者在 WebUI 删掉了进行中的场次，这一场都不再留档：
        # 告诉插件这一场作废，免得 QQ 群里的她还提起一场已经不存在的直播
        discarded = bool(payload.empty_discarded or payload.deleted)
        body["discarded"] = discarded
        await self._send(body, "收播近况")
        if self._transcript_source is None or discarded:
            # 不推整场记录的两种情形：没配记录来源；场次被丢弃/删除（内容已不存在，
            # 或运营者本意就是清掉它，不该再外流到 MaiBot）
            return
        if payload.superseded:
            # 被新场次顶掉不是真下播，没有道别轮可等：直接按现状推
            self._spawn(self._push_transcript(int(payload.live_session_id)), "maibot-transcript")
            return
        # 真下播：道别那一轮要等模型好几秒，台词落库晚于收播事件——此刻读库会缺
        # 收尾台词。等那一轮的 planner.decision（trigger_reason=proactive:signoff）
        # 再读：事件到即读、不靠固定等待；道别轮没跑时按上限兜底
        self._arm_transcript_after_signoff(int(payload.live_session_id))

    def _arm_transcript_after_signoff(self, live_session_id: int) -> None:
        """挂起整场记录推送：等道别轮收场信号，超时兜底。"""
        pending = self._pending_transcript
        if pending is not None and not pending[1].is_set():
            # 上一场还在等（连续收播的极少见情形）：先放行，避免它的记录永远压着不推
            pending[1].set()
        signoff_done = asyncio.Event()
        self._pending_transcript = (int(live_session_id), signoff_done)
        self._spawn(
            self._push_transcript_after_signoff(int(live_session_id), signoff_done),
            "maibot-transcript",
        )

    async def _on_planner_decision(self, event_name: str, payload: PlannerDecisionPayload, source: str) -> None:
        """道别轮收场：放行挂起的整场记录（此刻道别台词已落库）。"""
        del event_name, source
        pending = self._pending_transcript
        if pending is None or payload.trigger_reason != _SIGNOFF_TRIGGER_REASON:
            return
        pending[1].set()

    async def _push_transcript_after_signoff(self, live_session_id: int, signoff_done: asyncio.Event) -> None:
        """等道别轮跑完再推整场记录；等不到就按上限兜底推。"""
        try:
            await asyncio.wait_for(signoff_done.wait(), timeout=_SIGNOFF_WAIT_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.info(f"场次 {live_session_id} 等道别轮超时（{_SIGNOFF_WAIT_TIMEOUT_S:.0f} 秒），按现状推整场记录")
        finally:
            pending = self._pending_transcript
            if pending is not None and pending[0] == live_session_id:
                self._pending_transcript = None
        # 道别台词在决策收场之后才派发，等线程池写库落地再读，避免读在
        # "道别轮已收场、台词行还没提交"的缝里。等待量可注入（测试传 0）
        if self._signoff_settle_s > 0:
            await asyncio.sleep(self._signoff_settle_s)
        await self._push_transcript(live_session_id)

    def _spawn_catch_up(self) -> None:
        """启动补推：上次下播后很快关程序、整场记录没推成功的场次，这次启动补上。

        没有事件循环（同步装配、单元测试）时静默跳过；没有标记文件或记录来源时不做。
        """
        if self._push_log is None or self._transcript_source is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("启动补推跳过：当前没有运行中的事件循环")
            return
        self._spawn(self._catch_up_pending_transcripts(), "maibot-transcript-catchup")

    async def _catch_up_pending_transcripts(self) -> None:
        """扫最近结束的正式场，把没有"已处理"标记的整场记录补推一次。

        只补推同时满足这些条件的场次：正式场、已结束、结束在补推时间窗内、有真正播出的
        台词；彩排场永不补推。时间窗挡住"第一次启用标记"或"标记文件被删"时把历史整批推给
        MaiBot。推成功或被插件明确拒收才写标记；某一场多次重试仍失败（多半是 MaiBot 没起来）
        就停下，剩下的留给下次启动，不在 MaiBot 离线时逐场等满重试。标记文件读不了时整次
        跳过：分不清哪些推过，宁可这次不补，也不把最近若干场重推一遍。
        """
        assert self._push_log is not None and self._transcript_source is not None  # noqa: S101 调用方已判空
        source = self._transcript_source
        try:
            candidates = list(await source.list_catch_up_sessions(limit=self._catch_up_limit))
        except Exception as exc:  # noqa: BLE001 - 补推失败不影响本次运行的近况推送
            logger.warning(f"启动补推：读待补推场次失败（本次跳过）: {exc}", exc=True)
            return
        earliest_ms = self._clock_ms() - self._catch_up_window_ms
        recent = [item for item in candidates if item.ended_at_ms >= earliest_ms]
        try:
            marks = await self._push_log.read()
        except OSError as exc:
            logger.warning(f"启动补推：读推送标记失败，本次不补推（分不清哪些推过）: {exc}", exc=True)
            return
        pending: List["TranscriptCandidate"] = [
            item for item in recent if (item.live_session_id, item.ended_at_ms) not in marks
        ]
        if not pending:
            logger.debug("启动补推：最近场次都推过了")
            return
        logger.info(f"启动补推：最近结束的 {len(recent)} 场正式场里有 {len(pending)} 场没推成功，开始补推")
        delivered = 0
        # 候选按开播倒序，补推按时间正序来（旧场先到 QQ）
        ordered = list(reversed(pending))
        for index, candidate in enumerate(ordered):
            try:
                if not await source.has_spoken_lines(candidate.live_session_id):
                    logger.info(f"启动补推：场次 {candidate.live_session_id} 没有播出的台词，不推整场记录")
                    continue
                body = await source.load(candidate.live_session_id)
            except Exception as exc:  # noqa: BLE001 - 单场读库失败只跳过这一场
                logger.warning(f"启动补推：读场次 {candidate.live_session_id} 的整场记录失败（跳过）: {exc}", exc=True)
                continue
            if body is None:
                continue
            outcome = await self._send_with_retry(body, "整场记录补推")
            if outcome == _FAILED:
                logger.warning(
                    f"启动补推：场次 {candidate.live_session_id} 多次推送失败，"
                    f"剩下 {len(ordered) - index} 场留到下次启动"
                )
                break
            await self._write_mark(candidate.live_session_id, candidate.ended_at_ms)
            if outcome == _DELIVERED:
                delivered += 1
        logger.info(f"启动补推结束：送到 {delivered} 场")

    def _snapshot(self) -> Dict[str, Any]:
        assert self._live is not None  # noqa: S101 调用方已 guard
        return self._snapshot_of(self._live, live_flag=True)

    @staticmethod
    def _snapshot_of(live: _LiveState, *, live_flag: bool) -> Dict[str, Any]:
        """把一场的内部状态渲染成推送报文（开播 / 更换环节时的快照）。"""
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
        task.add_done_callback(self._on_push_task_done)

    def _on_push_task_done(self, task: "asyncio.Task[None]") -> None:
        """后台推送任务收尾：移出任务集；漏出来的异常记日志，不让它无声中止。"""
        self._push_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(f"后台推送任务 {task.get_name()} 异常退出: {exc}", exc=exc)

    async def _push_transcript(self, live_session_id: int) -> None:
        """读出整场记录推给插件；读库失败只记日志，不影响近况推送。"""
        assert self._transcript_source is not None  # noqa: S101 调用方已 guard
        try:
            body = await self._transcript_source.load(live_session_id)
        except Exception as exc:  # noqa: BLE001 - 读库失败不影响直播主链路
            logger.warning(f"读取场次 {live_session_id} 的整场记录失败，不推送: {exc}", exc=True)
            return
        if body is None:
            return
        if await self._send_with_retry(body, "整场记录") == _FAILED:
            return
        ended_at_ms = int(body.get("ended_at_ms") or 0)
        if ended_at_ms <= 0:
            logger.warning(f"场次 {live_session_id} 的整场记录没有结束时刻，不写已处理标记")
            return
        await self._write_mark(int(body.get("live_session_id") or live_session_id), ended_at_ms)

    async def _write_mark(self, live_session_id: int, ended_at_ms: int) -> None:
        """写"已处理"标记（送到或被拒收；暂时失败不写，留给下次启动补推）；没有标记文件时不动。

        拒收也写：插件明确拒收（例如没开整场记录回放）时原样重推也不会成功，不写的话
        每次启动都要把这些场次再推一遍。
        """
        if self._push_log is None:
            return
        try:
            await self._push_log.mark(live_session_id, ended_at_ms)
        except OSError as exc:  # 标记写不下最多下次重推一次，不挡推送
            logger.warning(f"整场记录推送标记写入失败（下次启动可能重推场次 {live_session_id}）: {exc}", exc=True)

    async def _send_with_retry(self, body: Dict[str, Any], what: str) -> str:
        """推送并按退避重试，返回送到 / 失败 / 被拒收；插件拒收（如没开整场记录回放）时不再重试。"""
        for delay_ms in (0, *self._retry_delays_ms):
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)
            outcome = await self._deliver(body, what)
            if outcome == _DELIVERED:
                return _DELIVERED
            if outcome == _REJECTED:
                logger.info(f"{what}（场次 {body['live_session_id']}）被插件拒收，不再重试")
                return _REJECTED
        logger.warning(f"{what}（场次 {body['live_session_id']}）多次推送失败，已放弃")
        return _FAILED

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
