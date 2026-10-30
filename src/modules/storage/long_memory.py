"""LongMemoryStore —— 主播长期记忆的纯文本文件（MEMORY.md）与它的历史版本。

主播跨很多场还该记得的事——答应过谁什么、老梗和口头禅、长线目标、对自己的
认识——存成一份有上限的纯文本。场次回顾一场一份、只管那一场；这份是主播自己
整理的长期笔记，收播时由主播 Agent 合并进本场的新内容，开播时读出注入。

正式场与彩排场各一份，互不改写（场次类型见 ``src.modules.types.session_kind``）：

- 正式：``<数据目录>/memory/MEMORY.md``
- 彩排：``<数据目录>/memory/MEMORY.rehearsal.md``

不进 SQLite：它是一整段给人和模型读的文本，没有可查询的结构，价值在于人能
直接用编辑器打开改。数据目录取数据库文件所在目录，测试用临时库时自然落在
临时目录里。写入先写同目录临时文件再原子替换，写到一半进程退出也不会留下
半份文件。

每次写入都留底：被替换掉的旧全文存进 ``memory/history/<类型>/<编号>.md``，这次
写入的来由和改动清单追加到同目录的 ``changes.jsonl``（一行一条）。合并改错了、
人手改坏了，都能按编号把旧版本恢复回来；只保留最近 ``history_keep`` 条。

收播合并写下的那一版另存为基线（``history/<类型>/baseline.md``，恢复旧版本时也更新）。
下一次合并拿当前文件和基线比，就知道哪些行是人后来改的——无论是在 WebUI 里改的，
还是直接用编辑器改的。

收播合并要先读、再调模型、最后写，中间隔着几十秒。为了不覆盖这期间人在 WebUI
里保存的改动，写入可以带上读到时的版本号（全文摘要），版本对不上就抛
``LongMemoryConflict``，由调用方在新版本上重做。WebUI 与主播 Agent 共用同一个
实例，核对与替换在同一把锁里完成。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from src.modules.logging import get_logger
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind
from src.modules.utils.time_utils import now_ms

__all__ = ["LongMemoryConflict", "LongMemoryHistoryEntry", "LongMemoryStore"]

logger = get_logger("LongMemoryStore")

_FILE_NAMES = {
    SESSION_KIND_LIVE: "MEMORY.md",
    "rehearsal": "MEMORY.rehearsal.md",
}
_HISTORY_DIR = "history"
_CHANGES_FILE = "changes.jsonl"
_BASELINE_FILE = "baseline.md"
_DEFAULT_HISTORY_KEEP = 50


class LongMemoryConflict(RuntimeError):
    """写入时文件已被别人改过（版本号对不上）：调用方应读新版本重做。"""


class LongMemoryHistoryEntry(BaseModel):
    """一次写入的留底记录：这次为什么写、改了什么，以及写之前的全文快照编号。"""

    id: str = Field(..., description="记录编号（写入时刻毫秒，同毫秒内加序号）；也是快照文件名")
    at_ms: int = Field(..., description="写入时刻（Unix epoch 毫秒）")
    reason: str = Field(default="", description="写入来由（收播整理 / WebUI 保存 / 恢复旧版本）")
    session_id: Optional[int] = Field(default=None, description="收播整理时的场次主键；人手保存为 None")
    changes: List[str] = Field(default_factory=list, description="这次改动的逐条说明")
    has_snapshot: bool = Field(default=False, description="写之前的全文是否留了快照（之前没有文件时为 False）")


def _version_of(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


class LongMemoryStore:
    """按场次类型读写长期记忆文件；文件不存在即"还没有长期记忆"（读回空串）。"""

    def __init__(
        self,
        root: Path,
        *,
        history_keep: int = _DEFAULT_HISTORY_KEEP,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._root = Path(root)
        self._history_keep = max(1, int(history_keep))
        self._clock = clock
        # 版本核对与替换要成为一步：WebUI 与收播合并可能在不同线程里同时写
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        """长期记忆文件所在目录。"""
        return self._root

    def path_of(self, kind: SessionKind) -> Path:
        """某类场次的长期记忆文件路径（不保证存在）。"""
        return self._root / _FILE_NAMES[kind]

    def _history_dir(self, kind: SessionKind) -> Path:
        return self._root / _HISTORY_DIR / kind

    async def read(self, kind: SessionKind) -> str:
        """读回全文（去掉首尾空白）；文件不存在返回空串。"""
        text, _version = await asyncio.to_thread(self._read_blocking, kind)
        return text

    async def read_versioned(self, kind: SessionKind) -> Tuple[str, str]:
        """读回全文与它的版本号；写入时带上版本号可防止覆盖期间别人保存的改动。"""
        return await asyncio.to_thread(self._read_blocking, kind)

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
        """整份覆盖写并留底，返回实际保存的全文；空文本会把文件写成空（相当于清空记忆）。

        ``expected_version`` 给了且与当前文件不符时抛 ``LongMemoryConflict``，文件不动。
        内容与当前完全相同时不写、不留底。``as_baseline`` 为收播合并的写入：写下的全文另存为
        基线，下次合并据此认出人后来改过的行。
        """
        return await asyncio.to_thread(
            self._write_blocking, kind, text, expected_version, reason, list(changes), session_id, as_baseline
        )

    async def read_baseline(self, kind: SessionKind) -> Optional[str]:
        """上一次收播合并写下的全文；还没合并过（或基线丢了）返回 None。"""
        return await asyncio.to_thread(self._read_baseline_blocking, kind)

    async def list_history(
        self, kind: SessionKind, *, limit: int = _DEFAULT_HISTORY_KEEP
    ) -> List[LongMemoryHistoryEntry]:
        """最近的留底记录（新的在前）。"""
        entries = await asyncio.to_thread(self._load_entries, kind)
        return list(reversed(entries))[: max(1, int(limit))]

    async def read_snapshot(self, kind: SessionKind, entry_id: str) -> Optional[str]:
        """某条留底记录里写之前的全文；记录不存在或没有快照返回 None。"""
        return await asyncio.to_thread(self._read_snapshot_blocking, kind, entry_id)

    async def restore(self, kind: SessionKind, entry_id: str) -> Optional[str]:
        """把某条记录写之前的全文恢复为当前版本（本身也留底，可再撤回）；没有这条快照返回 None。"""
        snapshot = await self.read_snapshot(kind, entry_id)
        if snapshot is None:
            return None
        # 恢复的版本当作新的基线：恢复回来的条目仍按原来的人工 / 模型身份对待
        return await self.write(kind, snapshot, reason=f"恢复到记录 {entry_id} 之前的版本", as_baseline=True)

    # ------------------------------------------------------------------
    # 阻塞实现（在线程里跑）
    # ------------------------------------------------------------------

    def _read_blocking(self, kind: SessionKind) -> Tuple[str, str]:
        path = self.path_of(kind)
        if not path.exists():
            return "", _version_of("")
        content = path.read_bytes().decode("utf-8-sig").strip()
        return content, _version_of(content)

    def _write_blocking(
        self,
        kind: SessionKind,
        text: str,
        expected_version: Optional[str],
        reason: str,
        changes: List[str],
        session_id: Optional[int],
        as_baseline: bool = False,
    ) -> str:
        content = (text or "").strip()
        path = self.path_of(kind)
        with self._lock:
            current, version = self._read_blocking(kind)
            if expected_version is not None and expected_version != version:
                raise LongMemoryConflict(f"长期记忆（{kind}）在读出之后被改过")
            if as_baseline:
                self._history_dir(kind).mkdir(parents=True, exist_ok=True)
                self._replace(self._history_dir(kind) / _BASELINE_FILE, content + "\n" if content else "")
            if content == current and path.exists():
                return content
            path.parent.mkdir(parents=True, exist_ok=True)
            self._record_history(
                kind,
                previous=current if path.exists() else None,
                reason=reason,
                changes=changes,
                session_id=session_id,
            )
            self._replace(path, content + "\n" if content else "")
        logger.info(f"长期记忆已写入（{kind}，{len(content)} 字，{reason or '未注明来由'}）: {path}")
        return content

    @staticmethod
    def _replace(path: Path, content: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        # 一律 LF + UTF-8 无 BOM：人用编辑器打开改、git 外的文本工具都认得
        tmp.write_bytes(content.encode("utf-8"))
        os.replace(tmp, path)

    def _record_history(
        self,
        kind: SessionKind,
        *,
        previous: Optional[str],
        reason: str,
        changes: List[str],
        session_id: Optional[int],
    ) -> None:
        """旧全文存成快照、追加一条留底记录，再把超出保留数的旧记录连同快照删掉。"""
        history = self._history_dir(kind)
        history.mkdir(parents=True, exist_ok=True)
        stamp = int(self._clock())
        entry_id = str(stamp)
        suffix = 1
        existing = {item.id for item in self._load_entries(kind)}
        while entry_id in existing or (history / f"{entry_id}.md").exists():
            entry_id = f"{stamp}-{suffix}"
            suffix += 1
        if previous is not None:
            self._replace(history / f"{entry_id}.md", previous + "\n" if previous else "")
        entry = LongMemoryHistoryEntry(
            id=entry_id,
            at_ms=stamp,
            reason=reason,
            session_id=session_id,
            changes=changes,
            has_snapshot=previous is not None,
        )
        entries = self._load_entries(kind) + [entry]
        dropped, kept = entries[: -self._history_keep], entries[-self._history_keep :]
        for old in dropped:
            (history / f"{old.id}.md").unlink(missing_ok=True)
        lines = "".join(json.dumps(item.model_dump(), ensure_ascii=False) + "\n" for item in kept)
        self._replace(history / _CHANGES_FILE, lines)

    def _load_entries(self, kind: SessionKind) -> List[LongMemoryHistoryEntry]:
        path = self._history_dir(kind) / _CHANGES_FILE
        if not path.exists():
            return []
        entries: List[LongMemoryHistoryEntry] = []
        for number, line in enumerate(path.read_bytes().decode("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entries.append(LongMemoryHistoryEntry.model_validate(json.loads(line)))
            except Exception as exc:  # noqa: BLE001 坏掉的一行跳过，不影响其他记录
                logger.warning(f"长期记忆留底记录第 {number} 行读不懂，已跳过（{kind}）: {exc}", exc=True)
        return entries

    def _read_baseline_blocking(self, kind: SessionKind) -> Optional[str]:
        path = self._history_dir(kind) / _BASELINE_FILE
        if not path.exists():
            return None
        return path.read_bytes().decode("utf-8-sig").strip()

    def _read_snapshot_blocking(self, kind: SessionKind, entry_id: str) -> Optional[str]:
        entry = next((item for item in self._load_entries(kind) if item.id == entry_id), None)
        if entry is None or not entry.has_snapshot:
            return None
        path = self._history_dir(kind) / f"{entry.id}.md"
        if not path.exists():
            return None
        return path.read_bytes().decode("utf-8-sig").strip()
