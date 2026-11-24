"""MaiGO 跨端记忆同步桥接器：开播签出、场中心跳、话题与熟人投影及下播签入。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.live import LiveEndedPayload, LiveStartedPayload
from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.logging import get_logger
from src.modules.maigo_sync.client import MaiGOClient, MaiGORejected
from src.modules.maigo_sync.pending_log import (
    CachedPersonEntry,
    CachedPromiseEntry,
    CachedTopicCard,
    PendingCheckinLog,
    extract_archive_lines,
    extract_interactions,
    extract_promises,
    extract_session_meta,
    format_brief_extension,
)
from src.modules.storage.models.talk_card import TalkCard
from src.modules.types.session_kind import SESSION_KIND_REHEARSAL
from src.modules.utils.time_utils import now_ms

__all__ = [
    "CachedPersonEntry",
    "CachedPromiseEntry",
    "CachedTopicCard",
    "MaiGOBridge",
]

logger = get_logger("MaiGOBridge")

_MAX_CARD_BODY_CHARS = 1000
_MAX_CARD_TITLE_CHARS = 120
_DEFAULT_RETRY_DELAYS_MS: Tuple[int, ...] = (5_000, 30_000)


@dataclass
class _SessionCheckoutState:
    """单场直播的跨端签出状态（贯穿开播至下播整理完成后签入）。"""

    live_session_id: int
    checkout_id: str
    started_at_ms: int
    brief_etag: str
    stable_brief: str
    live_context: str
    save_brief: str
    brief_extension_text: str
    open_promises: List[CachedPromiseEntry]
    topic_cards: List[CachedTopicCard]
    person_index: Dict[Tuple[str, str], CachedPersonEntry]
    title: str = ""
    segment_title: str = ""
    save_id: Optional[str] = None
    ended_at_ms: Optional[int] = None
    discarded: bool = False
    checked_in: bool = False


def _normalize_platform(platform: str) -> str:
    return (platform or "").strip().lower()


def _normalize_external_id(platform: str, external_id: str) -> str:
    cleaned = (external_id or "").strip()
    if _normalize_platform(platform) == "minecraft":
        return cleaned.lower()
    return cleaned


class MaiGOBridge:
    """Amaidesu 与 MaiGO 之间的薄同步桥梁。

    触发条件：由组合根或测试构建并调用 ``setup()``，随后在开播、换环节与下播整理节点同步；
    执行顺序：正式场开播签出冻结简报、鲜活话头与熟人字典驻留内存 -> 播中只读消费并随心跳
    回传已选用话头 -> 下播整理完成后回推档案、承诺与互动，失败落盘待补推并全路径降级不阻断直播。
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        client: MaiGOClient,
        branch_id: str = "amaidesu",
        heartbeat_interval_ms: int = 60_000,
        inject_topic_cards: bool = True,
        inject_person_cues: bool = True,
        pending_log_path: Optional[Path] = None,
        retry_delays_ms: Sequence[int] = _DEFAULT_RETRY_DELAYS_MS,
        clock_ms: Callable[[], int] = now_ms,
    ) -> None:
        self._event_bus = event_bus
        self._client = client
        self._branch_id = (branch_id or "amaidesu").strip().lower() or "amaidesu"
        self._heartbeat_interval_ms = max(5_000, int(heartbeat_interval_ms))
        self._inject_topic_cards = bool(inject_topic_cards)
        self._inject_person_cues = bool(inject_person_cues)
        self._pending_log = PendingCheckinLog(pending_log_path)
        self._retry_delays_ms = tuple(max(0, int(d)) for d in retry_delays_ms)
        self._clock_ms = clock_ms

        self._subscribed = False
        self._checkout_lock = asyncio.Lock()
        self._file_lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

        self._active_session_id: Optional[int] = None
        self._sessions: Dict[int, _SessionCheckoutState] = {}
        self._failed_checkout_sessions: set[int] = set()
        self._pending_consumed_seed_ids: List[int] = []
        self._all_consumed_seed_ids: set[int] = set()
        self._pending_resolved_promises: Dict[int, str] = {}
        self._current_segment_title: str = ""
        self._current_save_id: Optional[str] = None
        self._last_error_text: str = ""

    @property
    def inject_topic_cards(self) -> bool:
        """是否启用向本地话题匣注入跨端话头卡。"""
        return self._inject_topic_cards

    @property
    def inject_person_cues(self) -> bool:
        """是否启用向本地线索推送注入跨端熟人线索。"""
        return self._inject_person_cues

    @property
    def checkout_id(self) -> str:
        """当前活跃场次的 MaiGO 签出句柄；未签出时为空串。"""
        state = self._active_state()
        return state.checkout_id if state is not None else ""

    @property
    def brief_etag(self) -> str:
        """当前活跃场次签出的慢层简报 ETag；未签出时为空串。"""
        state = self._active_state()
        return state.brief_etag if state is not None else ""

    @property
    def is_live(self) -> bool:
        """当前是否处于已签出的正式开播状态。"""
        return self._active_state() is not None

    def setup(self) -> None:
        """挂载总线订阅（仅接线订阅，不在装配期发起网络请求）。"""
        if self._subscribed:
            return
        self._event_bus.on(CoreEvents.LIVE_STARTED, self._on_live_started, model_class=LiveStartedPayload)
        self._event_bus.on(CoreEvents.RUNDOWN_CHANGED, self._on_rundown_changed, model_class=RundownChangedPayload)
        self._event_bus.on(CoreEvents.LIVE_ENDED, self._on_live_ended, model_class=LiveEndedPayload)
        self._subscribed = True
        self._spawn_replay_if_loop_running()

    async def stop(self) -> None:
        """退订事件、取消心跳与后台任务并关闭 HTTP 客户端（幂等）。"""
        if self._subscribed:
            self._event_bus.off(CoreEvents.LIVE_STARTED, self._on_live_started)
            self._event_bus.off(CoreEvents.RUNDOWN_CHANGED, self._on_rundown_changed)
            self._event_bus.off(CoreEvents.LIVE_ENDED, self._on_live_ended)
            self._subscribed = False
        await self._stop_heartbeat()
        tasks = list(self._background_tasks)
        self._background_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self._client.close()
        except Exception as exc:  # noqa: BLE001 - 关闭清理阶段仅记录异常
            logger.warning(f"关闭 MaiGO HTTP 客户端失败: {exc}", exc=exc)

    async def ensure_checked_out(
        self,
        payload: LiveStartedPayload,
        *,
        save_id: Optional[str] = None,
    ) -> bool:
        """确保正式场已完成开播签出；彩排场直接跳过，服务端离线时仅记日志降级放行。"""
        if payload.kind == SESSION_KIND_REHEARSAL:
            return False

        sid = int(payload.live_session_id)
        async with self._checkout_lock:
            existing = self._sessions.get(sid)
            if existing is not None:
                self._active_session_id = sid
                return True
            if sid in self._failed_checkout_sessions and not payload.resumed:
                return False

            if save_id is not None:
                self._current_save_id = save_id.strip() or None

            request_body: Dict[str, Any] = {
                "branch_id": self._branch_id,
                "external_session_id": str(sid),
                "kind": "live",
                "visibility": "public",
                "title": payload.title or "",
                "platform": payload.platform or "bilibili",
                "room_id": payload.room_id or "",
                "segment_title": self._current_segment_title,
                "save_id": self._current_save_id,
            }
            try:
                raw = await self._client.checkout(request_body)
            except Exception as exc:  # noqa: BLE001 - 服务端不可达或拒收时安全降级为单机模式
                self._failed_checkout_sessions.add(sid)
                logger.warning(f"MaiGO 开播签出失败（场次 {sid} 降级为单机模式）: {exc}", exc=exc)
                return False

            checkout_id = str(raw.get("checkout_id") or "").strip()
            if not checkout_id:
                self._failed_checkout_sessions.add(sid)
                logger.warning(f"MaiGO 开播签出响应缺少 checkout_id（场次 {sid} 降级）")
                return False

            state = self._parse_checkout_state(sid=sid, payload=payload, raw=raw, checkout_id=checkout_id)
            self._failed_checkout_sessions.discard(sid)
            self._sessions[sid] = state
            self._active_session_id = sid
            self._pending_consumed_seed_ids.clear()
            self._all_consumed_seed_ids.clear()
            self._pending_resolved_promises.clear()
            self._last_error_text = ""
            self._start_heartbeat()
            logger.info(
                f"MaiGO 开播签出完成: session={sid}, checkout={checkout_id}, "
                f"话题卡={len(state.topic_cards)} 张, 跨端熟人={len(state.person_index)} 项"
            )
            return True

    def render_brief_extension(self) -> str:
        """返回本场冻结的跨端记忆摘要扩展段（整场逐字节稳定，未签出或关闭时为空串）。"""
        state = self._active_state()
        return state.brief_extension_text if state is not None else ""

    def virtual_cards(self) -> List[TalkCard]:
        """将签出的未用跨端话头映射为负数 ID 的内存虚拟话题卡（不落本地库）。"""
        if not self._inject_topic_cards:
            return []
        state = self._active_state()
        if state is None:
            return []
        ts = state.started_at_ms or self._clock_ms()
        cards: List[TalkCard] = []
        for entry in state.topic_cards:
            if entry.consumed or entry.seed_id <= 0:
                continue
            title = f"[今日见闻] {entry.title}".strip()[:_MAX_CARD_TITLE_CHARS]
            body_parts = [p for p in (entry.summary.strip(),) if p]
            if entry.reaction.strip():
                body_parts.append(f"感想：{entry.reaction.strip()}")
            if entry.transition_hint.strip():
                body_parts.append(entry.transition_hint.strip())
            cards.append(
                TalkCard(
                    id=-int(entry.seed_id),
                    kind="episode",
                    title=title,
                    body="；".join(body_parts)[:_MAX_CARD_BODY_CHARS],
                    asker="",
                    tags=[entry.source_branch] if entry.source_branch else [],
                    enabled=True,
                    created_at_ms=ts,
                    updated_at_ms=ts,
                )
            )
        return cards

    def mark_card_used(self, card_id: int) -> None:
        """标记负数虚拟话题卡已选用，并登记待随心跳或签入回传的话头编号。"""
        cid = int(card_id)
        if cid >= 0:
            return
        seed_id = -cid
        state = self._active_state()
        if state is not None:
            for item in state.topic_cards:
                if item.seed_id == seed_id:
                    item.consumed = True
        self._all_consumed_seed_ids.add(seed_id)
        if seed_id not in self._pending_consumed_seed_ids:
            self._pending_consumed_seed_ids.append(seed_id)

    def lookup_person_cue(self, platform: str, external_id: str) -> Optional[Tuple[int, str]]:
        """零网络 I/O 查询内存中已签出的跨端熟人映射，命中返回 ``(person_id, prompt_hint)``。"""
        if not self._inject_person_cues:
            return None
        state = self._active_state()
        if state is None:
            return None
        plat_key = _normalize_platform(platform)
        ext_key = _normalize_external_id(platform, external_id)
        if not plat_key or not ext_key:
            return None
        entry = state.person_index.get((plat_key, ext_key))
        return (entry.person_id, entry.prompt_hint) if entry is not None else None

    def mark_promise_resolved(self, anchor_id: int, *, status: str = "closed") -> None:
        """记录直播内确认兑现或作废的跨端约定，将在下播签入时一并回写。"""
        if status in ("closed", "void"):
            self._pending_resolved_promises[int(anchor_id)] = status

    def update_save_id(self, save_id: Optional[str]) -> None:
        """记录游戏当前存档标识，供下一次心跳与签入附带。"""
        cleaned = (save_id or "").strip() or None
        self._current_save_id = cleaned
        state = self._active_state()
        if state is not None:
            state.save_id = cleaned

    async def flush_heartbeat(self, *, save_id: Optional[str] = None) -> bool:
        """主动向 MaiGO 上报一次当前环节、存档与待回传的话头消费列表。"""
        state = self._active_state()
        if state is None:
            return False
        if save_id is not None:
            self.update_save_id(save_id)
        pending_seeds = list(self._pending_consumed_seed_ids)
        body: Dict[str, Any] = {
            "segment_title": self._current_segment_title or state.segment_title,
            "save_id": state.save_id if state.save_id is not None else self._current_save_id,
            "title": state.title,
            "consumed_seed_ids": pending_seeds,
        }
        try:
            await self._client.heartbeat(state.checkout_id, body)
        except Exception as exc:  # noqa: BLE001 - 心跳失败仅告警，不中断直播
            self._log_throttled_warning("MaiGO 心跳上报失败", exc)
            return False

        if pending_seeds:
            sent = set(pending_seeds)
            self._pending_consumed_seed_ids = [s for s in self._pending_consumed_seed_ids if s not in sent]
        self._last_error_text = ""
        return True

    async def push_settled_session(
        self,
        live_session_id: int,
        *,
        archive_repo: Any = None,
        commitments_repo: Any = None,
        sessions_repo: Any = None,
        game_player_repo: Any = None,
        save_id: Optional[str] = None,
    ) -> bool:
        """下播整理完成后读取本场结构化成品并执行签入；离线失败时落盘待补推。"""
        sid = int(live_session_id)
        state = self._sessions.get(sid)
        if state is None or state.checked_in:
            return False

        if state.discarded:
            return await self._submit_checkin(
                state,
                {"ended_at_ms": state.ended_at_ms or self._clock_ms(), "discarded": True},
            )

        effective_save = (save_id or "").strip() or state.save_id or self._current_save_id
        ended_ms = state.ended_at_ms or self._clock_ms()
        overview = ""
        if sessions_repo is not None:
            overview, repo_ended_ms = await extract_session_meta(sessions_repo, sid)
            if repo_ended_ms is not None and repo_ended_ms > 0:
                ended_ms = repo_ended_ms

        archive_lines = await extract_archive_lines(archive_repo, sid, fallback_save=effective_save)
        promises = await extract_promises(
            commitments_repo,
            sid=sid,
            started_at_ms=state.started_at_ms,
            open_promises=state.open_promises,
            resolved_promises=self._pending_resolved_promises,
            fallback_save=effective_save,
        )
        interactions = await extract_interactions(game_player_repo, sid)
        consumed = sorted(self._all_consumed_seed_ids | set(self._pending_consumed_seed_ids))
        payload: Dict[str, Any] = {
            "ended_at_ms": int(ended_ms),
            "overview": overview,
            "save_id": effective_save,
            "save_summary": "",
            "archive_lines": archive_lines,
            "promises": promises,
            "interactions": interactions,
            "consumed_seed_ids": consumed,
            "discarded": False,
        }
        return await self._submit_checkin(state, payload)

    async def replay_pending_checkins(self) -> int:
        """重放因离线落盘的待补推签入记录；返回成功补送条数。"""
        if not self._pending_log.enabled:
            return 0
        async with self._file_lock:
            records = await asyncio.to_thread(self._pending_log.read_all)
            if not records:
                return 0
            remaining: List[Dict[str, Any]] = []
            delivered = 0
            stop_batch = False
            for idx, item in enumerate(records):
                if stop_batch:
                    remaining.append(item)
                    continue
                checkout_id = str(item.get("checkout_id") or "").strip()
                body = item.get("payload")
                if not checkout_id or not isinstance(body, dict):
                    continue
                try:
                    await self._client.checkin(checkout_id, body)
                    delivered += 1
                except MaiGORejected as exc:
                    logger.warning(f"MaiGO 补推签入被服务端拒收（丢弃记录 {checkout_id}）: {exc}")
                except Exception as exc:  # noqa: BLE001 - 服务端仍离线则保留后续记录留待下次启动
                    logger.warning(f"MaiGO 补推签入失败（保留 {len(records) - idx} 条待下次启动）: {exc}")
                    remaining.append(item)
                    stop_batch = True
            await asyncio.to_thread(self._pending_log.rewrite, remaining)
            return delivered

    async def _on_live_started(self, event_name: str, payload: LiveStartedPayload, source: str) -> None:
        del event_name, source
        await self.ensure_checked_out(payload)

    async def _on_rundown_changed(self, event_name: str, payload: RundownChangedPayload, source: str) -> None:
        del event_name, source
        if (payload.total > 0 and payload.index >= payload.total) or payload.index < 0 or not payload.segment_id:
            self._current_segment_title = ""
        else:
            self._current_segment_title = payload.segment_title
        state = self._active_state()
        if state is None:
            return
        state.segment_title = self._current_segment_title
        await self.flush_heartbeat()

    async def _on_live_ended(self, event_name: str, payload: LiveEndedPayload, source: str) -> None:
        del event_name, source
        if payload.kind == SESSION_KIND_REHEARSAL:
            return
        sid = int(payload.live_session_id)
        if self._active_session_id == sid:
            self._active_session_id = None
        await self._stop_heartbeat()
        state = self._sessions.get(sid)
        if state is None:
            return
        state.ended_at_ms = int(payload.ended_at_ms)
        state.discarded = bool(payload.empty_discarded or payload.deleted)
        if state.discarded and not state.checked_in:
            await self._submit_checkin(state, {"ended_at_ms": state.ended_at_ms, "discarded": True})

    def _active_state(self) -> Optional[_SessionCheckoutState]:
        return self._sessions.get(self._active_session_id) if self._active_session_id is not None else None

    def _parse_checkout_state(
        self,
        *,
        sid: int,
        payload: LiveStartedPayload,
        raw: Dict[str, Any],
        checkout_id: str,
    ) -> _SessionCheckoutState:
        stable_brief = str(raw.get("stable_brief") or "").strip()
        live_context = str(raw.get("live_context") or "").strip()
        save_brief = str(raw.get("save_brief") or "").strip()
        brief_etag = str(raw.get("brief_etag") or "").strip()

        promises = [
            CachedPromiseEntry(
                anchor_id=int(item.get("id") or 0),
                content=str(item.get("content") or ""),
                status=str(item.get("status") or "open"),
                source_branch=str(item.get("source_branch") or ""),
                save_id=item.get("save_id"),
            )
            for item in (raw.get("open_promises") or ())
            if isinstance(item, dict)
        ]

        topic_cards: List[CachedTopicCard] = []
        for item in raw.get("topic_cards") or ():
            if not isinstance(item, dict):
                continue
            seed_id = int(item.get("seed_id") or item.get("id") or 0)
            if seed_id > 0:
                topic_cards.append(
                    CachedTopicCard(
                        seed_id=seed_id,
                        title=str(item.get("title") or ""),
                        summary=str(item.get("summary") or ""),
                        reaction=str(item.get("reaction") or ""),
                        source_branch=str(item.get("source_branch") or ""),
                        transition_hint=str(item.get("transition_hint") or ""),
                    )
                )

        person_index: Dict[Tuple[str, str], CachedPersonEntry] = {}
        for item in raw.get("person_directory") or ():
            if not isinstance(item, dict):
                continue
            plat = str(item.get("platform") or "")
            ext = str(item.get("external_id") or "")
            hint = str(item.get("prompt_hint") or "").strip()
            key = (_normalize_platform(plat), _normalize_external_id(plat, ext))
            if key[0] and key[1] and hint:
                person_index[key] = CachedPersonEntry(
                    person_id=int(item.get("person_id") or 0),
                    platform=plat,
                    external_id=ext,
                    display_name=str(item.get("display_name") or ""),
                    prompt_hint=hint,
                )

        extension = format_brief_extension(
            stable_brief=stable_brief,
            open_promises=promises,
            save_brief=save_brief,
        )
        return _SessionCheckoutState(
            live_session_id=sid,
            checkout_id=checkout_id,
            started_at_ms=int(payload.started_at_ms),
            brief_etag=brief_etag,
            stable_brief=stable_brief,
            live_context=live_context,
            save_brief=save_brief,
            brief_extension_text=extension,
            open_promises=promises,
            topic_cards=topic_cards,
            person_index=person_index,
            title=payload.title or "",
            segment_title=self._current_segment_title,
            save_id=self._current_save_id,
        )

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("当前无运行中事件循环，跳过启动 MaiGO 心跳任务")
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="maigo-sync-heartbeat")

    async def _stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _heartbeat_loop(self) -> None:
        while self._active_state() is not None:
            await asyncio.sleep(self._heartbeat_interval_ms / 1000)
            if self._active_state() is None:
                return
            await self.flush_heartbeat()

    def _spawn_replay_if_loop_running(self) -> None:
        if not self._pending_log.enabled:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("当前无运行中事件循环，启动时延迟补推")
            return
        task = asyncio.create_task(self.replay_pending_checkins(), name="maigo-checkin-replay")
        self._background_tasks.add(task)
        task.add_done_callback(self._on_bg_task_done)

    def _on_bg_task_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"MaiGO 后台任务 {task.get_name()} 异常退出: {exc}", exc=exc)

    def _log_throttled_warning(self, prefix: str, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        if msg != self._last_error_text:
            self._last_error_text = msg
            logger.warning(f"{prefix}（后续同因降为 debug）: {msg}")
        else:
            logger.debug(f"{prefix}（同因）: {msg}")

    async def _submit_checkin(self, state: _SessionCheckoutState, payload: Dict[str, Any]) -> bool:
        for delay_ms in (0, *self._retry_delays_ms):
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)
            try:
                await self._client.checkin(state.checkout_id, payload)
                state.checked_in = True
                self._sessions.pop(state.live_session_id, None)
                logger.info(f"MaiGO 下播签入成功: session={state.live_session_id}, checkout={state.checkout_id}")
                return True
            except MaiGORejected as exc:
                logger.warning(f"MaiGO 下播签入被服务端拒收（不再重试）: {exc}")
                state.checked_in = True
                return False
            except Exception as exc:  # noqa: BLE001 - 暂时失败继续下一档退避，最后落盘待补推
                logger.warning(f"MaiGO 下播签入失败（checkout={state.checkout_id}）: {exc}")

        state.checked_in = True
        self._sessions.pop(state.live_session_id, None)
        if self._pending_log.enabled:
            async with self._file_lock:
                try:
                    await asyncio.to_thread(self._pending_log.append, state.checkout_id, payload)
                    logger.info(f"MaiGO 下播签入已写入待补推队列: checkout={state.checkout_id}")
                except OSError as exc:
                    logger.warning(f"写入 MaiGO 待补推队列失败: {exc}", exc=exc)
        return False
