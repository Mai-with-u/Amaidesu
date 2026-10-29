"""LongMemoryStore —— 主播长期记忆的纯文本文件（MEMORY.md）。

主播跨很多场还该记得的事——答应过谁什么、老梗和口头禅、长线目标、对自己的
认识——存成一份有上限的纯文本。场次回顾一场一份、只管那一场；这份是主播自己
整理的长期笔记，收播时由主播 Agent 合并进本场的新内容，开播时整份注入。

正式场与彩排场各一份，互不改写（场次类型见 ``src.modules.types.session_kind``）：

- 正式：``<数据目录>/memory/MEMORY.md``
- 彩排：``<数据目录>/memory/MEMORY.rehearsal.md``

不进 SQLite：它是一整段给人和模型读的文本，没有可查询的结构，价值在于人能
直接用编辑器打开改。数据目录取数据库文件所在目录，测试用临时库时自然落在
临时目录里。写入先写同目录临时文件再原子替换，写到一半进程退出也不会留下
半份文件。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from src.modules.logging import get_logger
from src.modules.types.session_kind import SESSION_KIND_LIVE, SessionKind

__all__ = ["LongMemoryStore"]

logger = get_logger("LongMemoryStore")

_FILE_NAMES = {
    SESSION_KIND_LIVE: "MEMORY.md",
    "rehearsal": "MEMORY.rehearsal.md",
}


class LongMemoryStore:
    """按场次类型读写长期记忆文件；文件不存在即"还没有长期记忆"（读回空串）。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        """长期记忆文件所在目录。"""
        return self._root

    def path_of(self, kind: SessionKind) -> Path:
        """某类场次的长期记忆文件路径（不保证存在）。"""
        return self._root / _FILE_NAMES[kind]

    async def read(self, kind: SessionKind) -> str:
        """读回全文（去掉首尾空白）；文件不存在返回空串。"""
        return await asyncio.to_thread(self._read_blocking, kind)

    async def write(self, kind: SessionKind, text: str) -> str:
        """整份覆盖写，返回实际保存的全文；空文本会把文件写成空（相当于清空记忆）。"""
        return await asyncio.to_thread(self._write_blocking, kind, text)

    def _read_blocking(self, kind: SessionKind) -> str:
        path = self.path_of(kind)
        if not path.exists():
            return ""
        return path.read_bytes().decode("utf-8-sig").strip()

    def _write_blocking(self, kind: SessionKind, text: str) -> str:
        content = (text or "").strip()
        path = self.path_of(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        # 一律 LF + UTF-8 无 BOM：人用编辑器打开改、git 外的文本工具都认得
        tmp.write_bytes((content + "\n" if content else "").encode("utf-8"))
        os.replace(tmp, path)
        logger.info(f"长期记忆已写入（{kind}，{len(content)} 字）: {path}")
        return content
