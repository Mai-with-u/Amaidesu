"""
MemoryProvider Protocol（Wave 3 / §1.50 接入点）

```python
class MemoryProvider(Protocol):
    async def recall(query, top_k) -> list[MemoryHit]      # 召回（关键词→后续可语义）
    async def ingest(text, source) -> MemoryWriteResult    # 写入
    async def get_person_profile(person_id)                # 观众画像
    async def maintain()                                   # 维护（可空实现）
```

接入点现在定死（后补=推倒重来）：
- **召回**：ContextAssembler（§1.44）加"记忆召回面"——决策前 recall 观众画像+相关长记忆
- **写入**：事件触发（观众互动/开播关播/里程碑）→ ingest
- **配置**：`[memory] backend = "simple" | "amemorix"`——一行切换

## 简单版的差异（§1.50）
- 召回 = 关键词匹配（不使用 embedding）
- 接 A_Memorix 时在 adapter 层毫秒→秒（time_utils.ms_to_s/s_to_ms，§1.53 9d）
"""

from __future__ import annotations

from typing import Any, List, Protocol, runtime_checkable

from src.modules.memory.models import MemoryHit, MemoryWriteResult, PersonProfile


@runtime_checkable
class MemoryProvider(Protocol):
    """记忆能力后端协议（接口稳定，后插 AMemorixProvider 不动接口）"""

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

    async def get_person_profile(self, person_id: str) -> PersonProfile:
        """读取某观众的语义画像（无记录 → 默认空画像）。"""
        ...

    async def maintain(self) -> None:
        """维护（衰减 / LRU / 重算摘要 / …）。可空实现。"""
        ...


__all__ = ["MemoryProvider"]
