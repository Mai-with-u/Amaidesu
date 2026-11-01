"""共享长期笔记：正本只在 Amaidesu 本地，MaiBot 适配器插件那边只放"发布出来的东西"。

主播的长期笔记里有一层是"身份"——答应过的事、老梗、长线目标、关于我自己——QQ 那边的
麦麦也该知道，QQ 里答应的事也该进来。两边各管各的内部格式，连接只交换发布出来的东西，
插件不认识笔记格式，这边也不依赖插件怎么存：

- **发布**：正式场的笔记每次写入后（以及启动后第一次读时），用组合根注入的渲染函数生成
  "给 QQ 麦麦看的笔记"，整份推给插件；插件原样存、原样注入。
- **收约定**：QQ 麦麦记下的约定先放在插件自己的待收清单里。每次读正式场笔记前先去取清单，
  用组合根注入的收约定函数并进本地笔记（留底），发布新版本成功后再告诉插件这些已收，插件
  从清单里删掉。没回报成功的下次还会交来，收约定函数按正文去重，同一件只并一次。
- **插件连不上**：读写照常走本地，笔记本身不受影响；发布、收约定与回报留给后台按退避补做。

只同步正式场；彩排场的笔记照旧只在本地。只用 Amaidesu（没开 ``[maibot_sync]`` 或没开共享）
时完全不经过这里。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

import aiohttp

from src.modules.logging import get_logger
from src.modules.storage.long_memory import LongMemoryBackend, LongMemoryConflict, LongMemoryHistoryEntry
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

__all__ = [
    "HttpNotesRemote",
    "NotesRemote",
    "PendingPromise",
    "SharedNotesStore",
]

logger = get_logger("SharedNotesStore")

# 插件连不上时后台补做的间隔（毫秒）；用完后按最后一档一直重试
_RETRY_DELAYS_MS = (30_000, 120_000, 600_000)
# 插件交来的约定没写来处时的说法
_UNKNOWN_SOURCE = "MaiBot"

# 笔记全文 → 给 QQ 麦麦看的笔记
Render = Callable[[str], str]
# (笔记全文, [(约定正文, 来处)]) → (并进约定后的全文, 收下了哪些的说明)
Absorb = Callable[[str, Sequence[Tuple[str, str]]], Tuple[str, List[str]]]


@dataclass(frozen=True)
class PendingPromise:
    """插件那边记下、这边还没收的一件约定。``id`` 只用来回报"收了哪几件"。"""

    id: str
    text: str
    source: str


class NotesRemote(Protocol):
    """插件那头：收发布的笔记、交出待收的约定、删掉已收的。连不上或对方报错时抛异常。"""

    async def publish(self, text: str) -> None: ...

    async def promises(self) -> List[PendingPromise]: ...

    async def acknowledge(self, ids: Sequence[str]) -> None: ...

    async def close(self) -> None: ...


class HttpNotesRemote:
    """经 HTTP 与适配器插件打交道（``POST /notes``，携带 ``Authorization: Bearer <令牌>``）。

    每次请求都带上主播的名字，插件拿它和 MaiBot 的昵称比对后在应答里给出结果；两边
    名字对不上时这里告警一次（人设只有名字与性格底色需要两边一致，靠人手动对齐）。
    """

    def __init__(self, *, url: str, token: str, timeout_ms: int, persona_name: str = "") -> None:
        self._url = url
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
        self._persona_name = persona_name.strip()
        self._session: Optional[aiohttp.ClientSession] = None
        self._reported_names: Optional[Tuple[str, ...]] = None

    async def publish(self, text: str) -> None:
        await self._call({"action": "publish", "text": text}, "发布共享笔记")

    async def promises(self) -> List[PendingPromise]:
        data = await self._call({"action": "promises"}, "取 MaiBot 那边记下的约定")
        raw_items = data.get("promises")
        if not isinstance(raw_items, list):
            raise RuntimeError("取 MaiBot 那边记下的约定失败：应答里没有 promises 列表")
        result: List[PendingPromise] = []
        for raw in raw_items:
            item = raw if isinstance(raw, dict) else {}
            promise_id, text = str(item.get("id") or "").strip(), str(item.get("text") or "").strip()
            if not (promise_id and text):
                # 缺编号或正文的条目没法收也没法回报，跳过并留个记录
                logger.warning(f"MaiBot 那边交来一件读不懂的约定，跳过: {str(raw)[:200]}")
                continue
            source = str(item.get("source") or "").strip() or _UNKNOWN_SOURCE
            result.append(PendingPromise(id=promise_id, text=text, source=source))
        return result

    async def acknowledge(self, ids: Sequence[str]) -> None:
        await self._call({"action": "ack", "ids": list(ids)}, "回报已收的约定")

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _call(self, body: Dict[str, Any], what: str) -> Dict[str, Any]:
        status, data = await self._post(body)
        if status >= 300:
            raise RuntimeError(f"{what}失败 HTTP {status}: {str(data.get('error', ''))[:200]}")
        self._check_names(data.get("names"))
        return data

    async def _post(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        payload = {**body, "persona_name": self._persona_name}
        async with self._session.post(self._url, json=payload, headers=self._headers) as response:
            try:
                data = await response.json(content_type=None)
            except ValueError:
                data = {"error": (await response.text())[:200]}
            return response.status, data if isinstance(data, dict) else {}

    def _check_names(self, names: Any) -> None:
        """两边名字对不上时告警；同一组名字只说一次。"""
        if not isinstance(names, dict) or names.get("match") is not False:
            return
        maibot_names = tuple(str(name) for name in names.get("maibot") or [] if str(name).strip())
        if maibot_names == self._reported_names:
            return
        self._reported_names = maibot_names
        logger.warning(
            f"主播叫「{self._persona_name}」，MaiBot 那边叫「{'、'.join(maibot_names) or '（没取到）'}」："
            "两个身体共用一份笔记，名字要一致——请手动对齐 Amaidesu 人设的 bot_name 与 MaiBot 的 bot.nickname"
        )


class SharedNotesStore:
    """本地长期记忆存储外面包一层：正式场读之前收约定、写之后发布；对外与本地存储同一组方法。"""

    def __init__(
        self,
        *,
        local: LongMemoryBackend,
        remote: NotesRemote,
        render: Render,
        absorb: Absorb,
        retry_delays_ms: Sequence[int] = _RETRY_DELAYS_MS,
        shared_kind: SessionKind = SESSION_KIND_LIVE,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """``render`` / ``absorb`` 由组合根注入主播记忆的条目格式（本模块不认识笔记格式）。

        ``sleep`` 是后台补做两次尝试之间的等待（按秒），测试注入假的以免真等。
        """
        self._local = local
        self._remote = remote
        self._render = render
        self._absorb = absorb
        self._retry_delays_ms = tuple(retry_delays_ms) or _RETRY_DELAYS_MS
        self._kind = shared_kind
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._retry_task: Optional[asyncio.Task[None]] = None
        self._unavailable = False
        # 插件那边现在挂着的发布文字（None = 这次启动还没发布过，第一次读或写时发布一次）
        self._published: Optional[str] = None
        # 已并进本地笔记、还没回报插件的约定编号
        self._unacked: List[str] = []

    # ---- 与本地存储相同的读写面 ----

    def path_of(self, kind: SessionKind) -> Path:
        return self._local.path_of(kind)

    async def read(self, kind: SessionKind) -> str:
        text, _version = await self.read_versioned(kind)
        return text

    async def read_versioned(self, kind: SessionKind) -> Tuple[str, str]:
        """读当前笔记与版本号；正式场先收下插件那边待收的约定，插件连不上时直接读本地。"""
        if kind != self._kind:
            return await self._local.read_versioned(kind)
        async with self._lock:
            await self._align()
            return await self._local.read_versioned(kind)

    async def write(
        self,
        kind: SessionKind,
        text: str,
        *,
        expected_version: Optional[str] = None,
        reason: str = "",
        changes: Sequence[str] = (),
        session_id: Optional[int] = None,
        as_baseline: bool = False,
    ) -> str:
        """写本地（版本对不上照常抛 ``LongMemoryConflict``）；正式场写完把新版本发布给插件。"""
        if kind != self._kind:
            return await self._local.write(
                kind, text, expected_version=expected_version, reason=reason, changes=changes,
                session_id=session_id, as_baseline=as_baseline,
            )  # fmt: skip
        async with self._lock:
            saved = await self._local.write(
                kind, text, expected_version=expected_version, reason=reason, changes=changes,
                session_id=session_id, as_baseline=as_baseline,
            )  # fmt: skip
            if not await self._publish_pending():
                self._schedule_retry()
            return saved

    async def read_baseline(self, kind: SessionKind) -> Optional[str]:
        return await self._local.read_baseline(kind)

    async def list_history(self, kind: SessionKind, *, limit: int = 50) -> List[LongMemoryHistoryEntry]:
        return await self._local.list_history(kind, limit=limit)

    async def read_snapshot(self, kind: SessionKind, entry_id: str) -> Optional[str]:
        return await self._local.read_snapshot(kind, entry_id)

    async def restore(self, kind: SessionKind, entry_id: str) -> Optional[str]:
        """恢复旧版本（本地留底里取）；正式场恢复后把恢复的版本发布给插件。"""
        if kind != self._kind:
            return await self._local.restore(kind, entry_id)
        async with self._lock:
            restored = await self._local.restore(kind, entry_id)
            if restored is not None and not await self._publish_pending():
                self._schedule_retry()
            return restored

    async def flush(self) -> bool:
        """收一次约定、补一次发布与回报；返回是否都做完了（插件连不上时为 False）。"""
        async with self._lock:
            return await self._align()

    async def close(self) -> None:
        """停掉后台补做并关闭连接（没回报的约定插件那边还留着，下次启动读笔记时再收）。"""
        task, self._retry_task = self._retry_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._remote.close()

    # ---- 收约定与发布 ----

    async def _align(self) -> bool:
        """先收约定、再补发布与回报；插件连不上时安排后台补做。"""
        done = await self._collect() and await self._publish_pending()
        if not done:
            self._schedule_retry()
        return done

    async def _collect(self) -> bool:
        """取插件那边待收的约定并进本地笔记；返回插件是否连得上。

        并进笔记失败（本地读写出错、写的那一刻文件被人改过）时这批约定不回报，下次读时还会交来。
        """
        try:
            promises = await self._remote.promises()
        except Exception as exc:  # noqa: BLE001 插件连不上时照常读本地
            self._mark_unavailable(exc)
            return False
        self._mark_available()
        if not promises:
            return True
        try:
            text, version = await self._local.read_versioned(self._kind)
            merged, changes = self._absorb(text, [(item.text, item.source) for item in promises])
            if merged != text:
                await self._local.write(
                    self._kind, merged, expected_version=version, reason="收下 MaiBot 那边记下的约定", changes=changes
                )
                logger.info(f"收下 MaiBot 那边记下的约定 {len(changes)} 件")
        except LongMemoryConflict:
            logger.warning("收约定时本地笔记刚被改过，这批约定留到下次读笔记时再收")
            return True
        except Exception as exc:  # noqa: BLE001 并进笔记出错不影响读笔记，约定留在插件那边
            logger.error(f"把 MaiBot 那边记下的约定并进笔记失败，留到下次再收: {exc}", exc=True)
            return True
        self._unacked = list(dict.fromkeys([*self._unacked, *(item.id for item in promises)]))
        return True

    async def _publish_pending(self) -> bool:
        """插件那边的发布文字落后于本地时重新发布，发布好了再回报已收的约定；返回是否都做完了。"""
        try:
            view = self._render(await self._local.read(self._kind))
        except Exception as exc:  # noqa: BLE001 渲染出错时这次不发布，等下次写入
            logger.error(f"渲染要发布给 MaiBot 的笔记失败，这次不发布: {exc}", exc=True)
            return True
        try:
            if view != self._published:
                await self._remote.publish(view)
                self._published = view
                logger.info(f"共享笔记已发布给 MaiBot（{len(view)} 字）")
            if self._unacked:
                await self._remote.acknowledge(self._unacked)
                self._unacked = []
        except Exception as exc:  # noqa: BLE001 发布不上去：笔记在本地不受影响，之后补发
            self._mark_unavailable(exc)
            return False
        self._mark_available()
        return True

    # ---- 后台补做 ----

    def _schedule_retry(self) -> None:
        if self._retry_task is not None and not self._retry_task.done():
            return
        self._retry_task = asyncio.create_task(self._retry_loop(), name="maibot-notes-retry")

    async def _retry_loop(self) -> None:
        """按退避间隔补做，直到收约定、发布与回报都做完；间隔用完后按最后一档一直试。"""
        attempt = 0
        while True:
            delay_ms = self._retry_delays_ms[min(attempt, len(self._retry_delays_ms) - 1)]
            attempt += 1
            await self._sleep(delay_ms / 1000)
            try:
                async with self._lock:
                    if await self._collect() and await self._publish_pending():
                        return
            except Exception as exc:  # noqa: BLE001 补做出错只记日志，继续按间隔重试
                logger.warning(f"补发共享笔记出错，稍后再试: {type(exc).__name__}: {exc}", exc=True)

    def _mark_unavailable(self, exc: BaseException) -> None:
        if not self._unavailable:
            self._unavailable = True
            logger.warning(
                f"MaiBot 适配器插件连不上，笔记照常用本地的，发布与收约定之后自动补做: {type(exc).__name__}: {exc}"
            )
        else:
            logger.debug(f"MaiBot 适配器插件仍然连不上: {type(exc).__name__}: {exc}")

    def _mark_available(self) -> None:
        if self._unavailable:
            self._unavailable = False
            logger.info("MaiBot 适配器插件已重新连上")
