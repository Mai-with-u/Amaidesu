"""
MemoryProvider Protocol

```python
class MemoryProvider(Protocol):
    async def recall(query, top_k) -> list[MemoryHit]      # 召回
    async def ingest(text, source) -> MemoryWriteResult    # 写入
```

接入点：
- **召回**：决策前 recall 相关长记忆（planner 预注入）+ query_memory 工具
- **写入**：事件触发（话题摘要/观众互动）→ ingest
- **配置**：`[memory] backend = "simple"`（当前唯一后端）

## 简单版的差异
- 召回 = 关键词匹配（不使用 embedding）
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
