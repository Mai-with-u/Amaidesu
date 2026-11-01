"""maibot_sync 域的替身：MaiBot 适配器插件上的共享笔记接口，以及组合根注入的笔记格式函数。"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from src.modules.maibot_sync import PendingPromise


class FakeNotesRemote:
    """插件那头的替身：记下发布的文字、存待收的约定、按编号删掉已收的；``down`` 时模拟 MaiBot 没开。

    ``fail_publish`` 为真时只有发布失败（取约定照常），模拟"刚取完约定插件就断了"。
    """

    def __init__(self) -> None:
        self.published: Optional[str] = None
        self.publishes: List[str] = []
        self.inbox: List[PendingPromise] = []
        self.acks: List[List[str]] = []
        self.calls = 0
        self.down = False
        self.fail_publish = False
        self.closed = False
        self._next_id = 0

    def record(self, text: str, source: str = "QQ群") -> str:
        """像插件的记约定工具那样记下一件，返回它的编号。"""
        self._next_id += 1
        promise = PendingPromise(id=f"p{self._next_id}", text=text, source=source)
        self.inbox.append(promise)
        return promise.id

    async def publish(self, text: str) -> None:
        self._reach()
        if self.fail_publish:
            raise ConnectionError("插件刚断了")
        self.published = text
        self.publishes.append(text)

    async def promises(self) -> List[PendingPromise]:
        self._reach()
        return list(self.inbox)

    async def acknowledge(self, ids: Sequence[str]) -> None:
        self._reach()
        self.acks.append(list(ids))
        self.inbox = [item for item in self.inbox if item.id not in set(ids)]

    async def close(self) -> None:
        self.closed = True

    def _reach(self) -> None:
        if self.down:
            raise ConnectionError("MaiBot 没开")
        self.calls += 1


def render_view(text: str) -> str:
    """发布渲染的替身：给全文加个记号，验证发布出去的是渲染后的那份。"""
    return f"给 QQ 看：{text}" if text else ""


def append_promises(text: str, promises: Sequence[Tuple[str, str]]) -> Tuple[str, List[str]]:
    """收约定的替身：全文里没出现过的正文按行追加到末尾，标未兑现与来处。"""
    lines = text.splitlines()
    changes: List[str] = []
    for body, source in promises:
        if any(body in line for line in lines):
            continue
        lines.append(f"- {body}〔未兑现 · 来自 {source}〕")
        changes.append(f"收下：{body}")
    return "\n".join(lines).strip(), changes
