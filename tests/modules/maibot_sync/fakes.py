"""maibot_sync 域的替身：MaiBot 插件上的共享笔记正本。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

from src.modules.maibot_sync import NotesConflict, RemoteNotes
from src.modules.storage.long_memory import content_version


class FakeNotesRemote:
    """正本的替身：存一份全文，按版本号核对写入；``down`` 时模拟 MaiBot 没开。

    ``before_push`` 在下一次推送核对版本之前执行一次，模拟"推的那一刻 QQ 里刚记下一件约定"。
    """

    def __init__(self, text: str = "") -> None:
        self.text = text.strip()
        self.down = False
        self.fetches = 0
        self.pushes: List[Dict[str, Any]] = []
        self.before_push: Optional[Callable[[], None]] = None
        self.closed = False

    def append(self, line: str, section: str = "答应过的事") -> None:
        """像插件的记约定工具那样，在某节末尾追加一行（没有这一节就新开）。"""
        header = f"## {section}"
        lines = self.text.splitlines()
        if header not in lines:
            self.text = (self.text + f"\n\n{header}\n{line}").strip()
            return
        end = lines.index(header) + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        self.text = "\n".join([*lines[:end], line, *lines[end:]]).strip()

    async def fetch(self) -> RemoteNotes:
        if self.down:
            raise ConnectionError("MaiBot 没开")
        self.fetches += 1
        return RemoteNotes(text=self.text, version=content_version(self.text))

    async def push(self, text: str, *, expected_version: str, reason: str, changes: Sequence[str]) -> RemoteNotes:
        if self.down:
            raise ConnectionError("MaiBot 没开")
        if self.before_push is not None:
            hook, self.before_push = self.before_push, None
            hook()
        if expected_version != content_version(self.text):
            raise NotesConflict("版本对不上")
        self.text = text.strip()
        self.pushes.append({"text": self.text, "reason": reason, "changes": list(changes)})
        return RemoteNotes(text=self.text, version=content_version(self.text))

    async def close(self) -> None:
        self.closed = True
