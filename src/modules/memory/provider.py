"""
MemoryProvider Protocol

```python
class MemoryProvider(Protocol):
    async def recall(query, top_k) -> list[MemoryHit]      # 召回
    async def ingest(text, source) -> MemoryWriteResult    # 写入
```

**当前状态**：内置 ``SimpleMemory`` 承载观众**事实/画像**读写
（``viewer_facts`` / ``viewer_profiles``），不实现本 Protocol；本 Protocol 为
**将来的外部记忆后端**预留——接入时实现 ``recall`` / ``ingest`` 即可替换。

调用面（现状，均不经本 Protocol）：
- **事实**：``query_memory`` 工具查 ``viewer_facts``
- **画像**：planner 预注入 + ``query_viewer_profile`` 工具查 ``viewer_profiles``

## 时间单位
- Amaidesu 内部全毫秒，无秒↔毫秒转换
"""

from __future__ import annotations

from typing import Any, List, Protocol, runtime_checkable

from src.modules.memory.models import MemoryHit, MemoryWriteResult


@runtime_checkable
class MemoryProvider(Protocol):
    """记忆能力后端协议（接口稳定，未来换后端不动接口）"""

    async def recall(self, query: str, top_k: int = 5) -> List[MemoryHit]:
        """按文本/标签/关键词召回。子类可覆盖（关键词 / 语义 / 混合）。"""
        ...

    async def ingest(
        self,
        text: str,
        source: str = "",
        *,
        tags: Any = None,
        timestamp_ms: int = 0,
    ) -> MemoryWriteResult:
        """写入一条记忆。子类负责持久化与去重策略。"""
        ...


__all__ = ["MemoryProvider"]
