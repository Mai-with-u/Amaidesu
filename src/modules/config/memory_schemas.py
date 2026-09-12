"""Memory 配置 Schema 定义

定义 ``config/storage.toml`` 中 ``[memory]`` 段的 Pydantic 模型。

段树结构（TOML 视角）::

    [memory]
    backend = "simple"

> 单一事实源原则：``db_path`` **不在此定义**——存储与记忆共用同一 SQLite
> 库，路径权威在 ``[sqlite].db_path``。

设计原则：
- ``backend`` 仅支持 ``"simple"``（关键词召回）；amemorix 外部服务
  段位已废除（F7 + §13 S4 决策：外部依赖解耦期迁出，待独立路线再引入）
- SimpleMemory 子段已废除（三字段 recall_top_k / viewer_profile_max /
  fact_max_age_days 在 SimpleMemory 实现中为常量，不再暴露配置面）
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# 后端字面量收紧为 simple（amemorix 段废除，bootstrap fail-fast 分支对应删除）
MemoryBackend = Literal[
    "simple",  # 内置 SimpleMemory（SQLite 关键词召回，无 embedding）
]


class MemoryConfig(BaseConfig):
    """``[memory]`` 段（嵌入 storage.toml 顶层）

    通过 ``backend`` 字面量切换记忆后端实现（当前仅 ``"simple"``）。
    """

    model_config = ConfigDict(extra="forbid")

    backend: MemoryBackend = Field(
        default="simple",
        description="记忆后端实现：simple=内置 SimpleMemory（关键词召回）",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["simple"],
        },
    )


__all__ = [
    "MemoryBackend",
    "MemoryConfig",
    "MemoryRootConfig",
]


# 向后兼容壳：原 memory.toml 独立文件根——已并入 storage.toml [memory] 段
# 旧导入路径仍允许（dashboard/api/config.py 等占位）
class MemoryRootConfig(BaseConfig):
    """向后兼容壳——§6.2 重构后 memory 段已并入 storage.toml，无独立 memory.toml。
    本壳保留供旧 _SECTION_TO_ROOT_MODEL 占位 key。"""

    memory: MemoryConfig = Field(default_factory=MemoryConfig, description="占位")
