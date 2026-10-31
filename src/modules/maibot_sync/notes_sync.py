"""共享长期笔记：Amaidesu 与 MaiBot 都在用时，正本放在 MaiBot 适配器插件，本地 MEMORY.md 当缓存。

主播的长期笔记里有一层是"身份"——答应过的事、老梗、长线目标、关于我自己——QQ 那边的
麦麦也该知道，QQ 里答应的事也该进来。MaiBot 7×24 常驻，Amaidesu 用时才开，所以两个都用
时正本跟着常驻的那一边：放在插件里，QQ 麦麦随时读得到、记得进去；Amaidesu 读写都先找
正本，本地文件只是缓存。只用 Amaidesu（没开 ``[maibot_sync]`` 或没开共享）时完全不经过
这里，笔记照旧只在本地。

本模块提供与本地存储同一组方法的 ``SharedNotesStore``，收播整理与 WebUI 不知道正本在哪：

- **读**：先拉正本。本地自上次同步以来没改过就采纳正本（写进本地缓存并留底）；本地有
  没推上去的改动（MaiBot 不在线时写的）而正本没变，就把本地推上去；两边都改过，按条目做
  三方合并（共同版本 = 上次同步时的全文），合并结果推上去并写回本地。
- **写**：带版本号的写入（收播整理）直接推给正本，正本在读出之后被改过（如 QQ 里刚记下一件
  约定）就报版本冲突，由整理器在新版本上重做同一批操作；不带版本号的写入（WebUI 保存、恢复
  旧版本）先和正本自上次同步以来的改动做三方合并再推。推成功后写本地缓存。
- **正本连不上**：读用本地缓存照常开播，写只写本地（照常核对本地版本、留底），记为待推；
  后台按退避重试，下次读写也会先补推。

只同步正式场的笔记；彩排场的笔记照旧只在本地。正本和本地的版本号用同一种算法
（``content_version``），两边对同一份全文算出的版本号一致。三方合并按条目进行，由组合根
注入（主播记忆的条目格式不归本模块管）。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

import aiohttp

from src.modules.logging import get_logger
from src.modules.storage.long_memory import (
    LongMemoryBackend,
    LongMemoryConflict,
    LongMemoryHistoryEntry,
    content_version,
)
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

__all__ = [
    "HttpNotesRemote",
    "NotesConflict",
    "NotesRemote",
    "RemoteNotes",
    "SharedNotesStore",
]

logger = get_logger("SharedNotesStore")

# 正本连不上时后台补推的间隔（毫秒）；用完后按最后一档一直重试
_RETRY_DELAYS_MS = (30_000, 120_000, 600_000)
# 一次读写里正本连续被别人改的最多重来次数
_MAX_ATTEMPTS = 3

# 三方合并：(共同版本, 本地, 正本) → (合并结果, 采纳与舍弃的说明)
Reconcile = Callable[[str, str, str], Tuple[str, List[str]]]


class NotesConflict(RuntimeError):
    """推给正本时版本号对不上：正本在读出之后被改过。"""


@dataclass(frozen=True)
class RemoteNotes:
    """正本的一份全文与它的版本号。"""

    text: str
    version: str


class NotesRemote(Protocol):
    """正本所在处。连不上、对方报错时抛异常；版本对不上抛 ``NotesConflict``。"""

    async def fetch(self) -> RemoteNotes: ...

    async def push(self, text: str, *, expected_version: str, reason: str, changes: Sequence[str]) -> RemoteNotes: ...

    async def close(self) -> None: ...


class HttpNotesRemote:
    """经 HTTP 读写适配器插件上的正本（``POST /notes``，携带 ``Authorization: Bearer <令牌>``）。

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

    async def fetch(self) -> RemoteNotes:
        status, data = await self._post({"action": "get"})
        if status >= 300:
            raise RuntimeError(f"读取共享笔记失败 HTTP {status}: {str(data.get('error', ''))[:200]}")
        return self._notes_of(data)

    async def push(self, text: str, *, expected_version: str, reason: str, changes: Sequence[str]) -> RemoteNotes:
        body = {
            "action": "put",
            "text": text,
            "expected_version": expected_version,
            "reason": reason,
            "changes": list(changes),
        }
        status, data = await self._post(body)
        if status == 409:
            raise NotesConflict("共享笔记在读出之后被改过")
        if status >= 300:
            raise RuntimeError(f"写入共享笔记失败 HTTP {status}: {str(data.get('error', ''))[:200]}")
        return self._notes_of(data)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

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

    def _notes_of(self, data: Dict[str, Any]) -> RemoteNotes:
        self._check_names(data.get("names"))
        text = str(data.get("text") or "").strip()
        return RemoteNotes(text=text, version=content_version(text))

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
    """正本在别处、本地文件当缓存的长期记忆存储；对外与本地存储提供同一组方法。"""

    def __init__(
        self,
        *,
        local: LongMemoryBackend,
        remote: NotesRemote,
        reconcile: Reconcile,
        state_path: Path,
        retry_delays_ms: Sequence[int] = _RETRY_DELAYS_MS,
        shared_kind: SessionKind = SESSION_KIND_LIVE,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """``state_path`` 存上次与正本同步时的全文（三方合并的共同版本；不存在 = 还没同步过）。

        ``sleep`` 是后台补推两次尝试之间的等待（按秒），测试注入假的以免真等。
        """
        self._local = local
        self._remote = remote
        self._reconcile = reconcile
        self._state_path = Path(state_path)
        self._retry_delays_ms = tuple(retry_delays_ms) or _RETRY_DELAYS_MS
        self._kind = shared_kind
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._retry_task: Optional[asyncio.Task[None]] = None
        self._unavailable = False

    # ---- 与本地存储相同的读写面 ----

    def path_of(self, kind: SessionKind) -> Path:
        """本地缓存文件的路径（WebUI 显示用）。"""
        return self._local.path_of(kind)

    async def read(self, kind: SessionKind) -> str:
        text, _version = await self.read_versioned(kind)
        return text

    async def read_versioned(self, kind: SessionKind) -> Tuple[str, str]:
        """读当前笔记与版本号；正式场先与正本对齐，正本连不上时给本地缓存。"""
        if kind != self._kind:
            return await self._local.read_versioned(kind)
        async with self._lock:
            return await self._sync()

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
        """写入；正式场先推给正本再写本地缓存，版本对不上抛 ``LongMemoryConflict``。"""
        if kind != self._kind:
            return await self._local.write(
                kind, text, expected_version=expected_version, reason=reason, changes=changes,
                session_id=session_id, as_baseline=as_baseline,
            )  # fmt: skip
        async with self._lock:
            return await self._write_shared(
                (text or "").strip(), expected_version, reason, list(changes), session_id, as_baseline
            )

    async def read_baseline(self, kind: SessionKind) -> Optional[str]:
        return await self._local.read_baseline(kind)

    async def list_history(self, kind: SessionKind, *, limit: int = 50) -> List[LongMemoryHistoryEntry]:
        return await self._local.list_history(kind, limit=limit)

    async def read_snapshot(self, kind: SessionKind, entry_id: str) -> Optional[str]:
        return await self._local.read_snapshot(kind, entry_id)

    async def restore(self, kind: SessionKind, entry_id: str) -> Optional[str]:
        """恢复旧版本：快照取自本地留底，写入走正本（与正本这期间的改动三方合并）。"""
        snapshot = await self._local.read_snapshot(kind, entry_id)
        if snapshot is None:
            return None
        return await self.write(kind, snapshot, reason=f"恢复到记录 {entry_id} 之前的版本", as_baseline=True)

    async def flush(self) -> bool:
        """有没推上去的改动就与正本对齐一次；返回是否已经没有待推的改动（正本连不上时为 False）。"""
        async with self._lock:
            local_text, local_version = await self._local.read_versioned(self._kind)
            if local_text == self._load_synced():
                return True
            try:
                remote = await self._remote.fetch()
            except Exception as exc:  # noqa: BLE001 连不上留给下一次补推
                self._mark_unavailable(exc)
                return False
            self._mark_available()
            return await self._align(remote, local_text, local_version) is not None and not self._unavailable

    async def close(self) -> None:
        """停掉后台补推并关闭连接（待推的改动留在本地，下次启动读笔记时补推）。"""
        task, self._retry_task = self._retry_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._remote.close()

    # ---- 对齐 ----

    async def _sync(self) -> Tuple[str, str]:
        """拉正本并与本地对齐，返回对齐后的全文与版本号；正本连不上时返回本地缓存。"""
        local_text, local_version = await self._local.read_versioned(self._kind)
        try:
            remote = await self._remote.fetch()
        except Exception as exc:  # noqa: BLE001 正本连不上时用本地缓存照常开播
            self._mark_unavailable(exc)
            if local_text != self._load_synced():
                self._schedule_retry()
            return local_text, local_version
        self._mark_available()
        for _attempt in range(_MAX_ATTEMPTS):
            result = await self._align(remote, local_text, local_version)
            if result is not None:
                return result
            # 推的时候正本又被改了（QQ 里刚记下一件约定）：重新拉一次再对齐
            try:
                remote = await self._remote.fetch()
            except Exception as exc:  # noqa: BLE001 同上
                self._mark_unavailable(exc)
                break
            local_text, local_version = await self._local.read_versioned(self._kind)
        logger.warning("共享笔记连续几次在对齐时被改过，这次先用本地缓存")
        self._schedule_retry()
        return local_text, local_version

    async def _align(self, remote: RemoteNotes, local_text: str, local_version: str) -> Optional[Tuple[str, str]]:
        """对齐一次；推送时版本冲突返回 None（由调用方重拉重来）。"""
        synced = self._load_synced()
        base = synced if synced is not None else ""
        if local_text == remote.text:
            if synced != remote.text:
                self._save_synced(remote.text)
            return local_text, local_version
        if local_text == base:
            # 本地自上次同步以来没改过：采纳正本（多半是 QQ 里记下了新约定）
            _merged, changes = self._reconcile(base, base, remote.text)
            text = await self._local.write(
                self._kind, remote.text, reason="从 MaiBot 同步", changes=changes or ["正本有改动"]
            )
            self._save_synced(remote.text)
            return text, content_version(text)
        if remote.text == base:
            target, changes, reason = local_text, ["推送本地没同步过的改动"], "补推本地改动"
        else:
            target, changes = self._reconcile(base, local_text, remote.text)
            reason = "与 MaiBot 上的正本合并"
        try:
            await self._remote.push(target, expected_version=remote.version, reason=reason, changes=changes)
        except NotesConflict:
            return None
        except Exception as exc:  # noqa: BLE001 推不上去：本地先保留合并结果，之后再推
            self._mark_unavailable(exc)
            self._schedule_retry()
            if target != local_text:
                text = await self._local.write(self._kind, target, reason=f"{reason}（还没推上去）", changes=changes)
                return text, content_version(text)
            return local_text, local_version
        if target != local_text:
            await self._local.write(self._kind, target, reason=reason, changes=changes)
        self._save_synced(target)
        logger.info(f"共享笔记已与 MaiBot 对齐（{reason}，{len(changes)} 处）")
        return target, content_version(target)

    async def _write_shared(
        self,
        text: str,
        expected_version: Optional[str],
        reason: str,
        changes: List[str],
        session_id: Optional[int],
        as_baseline: bool,
    ) -> str:
        try:
            target = await self._push_write(text, expected_version, reason, changes)
        except NotesConflict as exc:
            raise LongMemoryConflict("共享笔记在读出之后被改过") from exc
        except Exception as exc:  # noqa: BLE001 正本连不上：先写本地，之后补推
            self._mark_unavailable(exc)
            saved = await self._local.write(
                self._kind, text, expected_version=expected_version, reason=reason, changes=changes,
                session_id=session_id, as_baseline=as_baseline,
            )  # fmt: skip
            self._schedule_retry()
            return saved
        self._mark_available()
        saved = await self._local.write(
            self._kind, target, reason=reason, changes=changes, session_id=session_id, as_baseline=as_baseline
        )
        self._save_synced(target)
        return saved

    async def _push_write(self, text: str, expected_version: Optional[str], reason: str, changes: List[str]) -> str:
        """推一次写入，返回正本上实际存下的全文。

        带版本号：直接按这个版本推，冲突原样抛出。不带版本号：先拉正本，把它自上次同步以来的
        改动三方合并进来再推（推的时候又被改了就重来）。
        """
        if expected_version is not None:
            await self._remote.push(text, expected_version=expected_version, reason=reason, changes=changes)
            return text
        for _attempt in range(_MAX_ATTEMPTS):
            remote = await self._remote.fetch()
            synced = self._load_synced()
            base = synced if synced is not None else ""
            target, merged_changes = text, []
            if remote.text != base:
                target, merged_changes = self._reconcile(base, text, remote.text)
            try:
                await self._remote.push(
                    target, expected_version=remote.version, reason=reason, changes=changes + merged_changes
                )
            except NotesConflict:
                continue
            changes.extend(merged_changes)
            return target
        raise NotesConflict("共享笔记连续几次在写入时被改过")

    # ---- 待推与后台补推 ----

    def _schedule_retry(self) -> None:
        if self._retry_task is not None and not self._retry_task.done():
            return
        self._retry_task = asyncio.create_task(self._retry_loop(), name="maibot-notes-retry")

    async def _retry_loop(self) -> None:
        """按退避间隔补推，直到本地没有待推的改动；间隔用完后按最后一档一直试。"""
        attempt = 0
        while True:
            delay_ms = self._retry_delays_ms[min(attempt, len(self._retry_delays_ms) - 1)]
            attempt += 1
            await self._sleep(delay_ms / 1000)
            try:
                if await self.flush():
                    return
            except Exception as exc:  # noqa: BLE001 补推失败只记日志，继续按间隔重试
                logger.warning(f"补推共享笔记出错，稍后再试: {type(exc).__name__}: {exc}", exc=True)

    def _mark_unavailable(self, exc: BaseException) -> None:
        if not self._unavailable:
            self._unavailable = True
            logger.warning(f"MaiBot 上的共享笔记连不上，先用本地缓存，之后自动补推: {type(exc).__name__}: {exc}")
        else:
            logger.debug(f"共享笔记仍然连不上: {type(exc).__name__}: {exc}")

    def _mark_available(self) -> None:
        if self._unavailable:
            self._unavailable = False
            logger.info("MaiBot 上的共享笔记已重新连上")

    # ---- 上次同步时的全文 ----

    def _load_synced(self) -> Optional[str]:
        if not self._state_path.exists():
            return None
        return self._state_path.read_bytes().decode("utf-8").strip()

    def _save_synced(self, text: str) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_name(self._state_path.name + ".tmp")
        tmp.write_bytes((text.strip() + "\n" if text.strip() else "").encode("utf-8"))
        os.replace(tmp, self._state_path)
