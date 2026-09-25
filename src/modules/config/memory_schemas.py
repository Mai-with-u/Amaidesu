"""Memory 配置 Schema 定义

定义 ``config/storage.toml`` 中 ``[memory]`` 段的 Pydantic 模型。

段树结构（TOML 视角）::

    [memory]
    backend = "simple"
    fact_extraction_enabled = true
    profile_min_interactions = 3
    profile_max_length = 400
    profile_injection_max = 3
    facts_per_batch = 5

> 单一事实源原则：``db_path`` **不在此定义**——存储与记忆共用同一 SQLite
> 库，路径权威在 ``[sqlite].db_path``。

设计原则：
- ``backend`` 仅支持 ``"simple"``（观众事实/画像读写）；外部记忆后端属
  独立路线，不在本体系
- 画像行为参数集中在 [memory] 段：后台事实提取（写入侧）与 Planner 注入
  （读取侧）共享同一组策略值，不拆两处配置
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# 后端字面量收紧为 simple（外部记忆后端不在本体系）
MemoryBackend = Literal[
    "simple",  # 内置 SimpleMemory（SQLite 观众事实/画像读写，无 embedding）
]


class MemoryConfig(BaseConfig):
    """``[memory]`` 段（嵌入 storage.toml 顶层）

    通过 ``backend`` 字面量切换记忆后端实现（当前仅 ``"simple"``）；
    画像行为参数供主播 Agent 的事实提取与 Planner 注入消费。
    """

    model_config = ConfigDict(extra="forbid")

    backend: MemoryBackend = Field(
        default="simple",
        description="记忆后端实现：simple=内置 SimpleMemory（观众事实/画像读写）",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["simple"],
        },
    )
    fact_extraction_enabled: bool = Field(
        default=True,
        description="是否在后台话题摘要循环中顺便提取观众事实（关闭后画像将无新原料）",
    )
    profile_min_interactions: int = Field(
        default=3,
        ge=1,
        le=100,
        description="首次生成画像的互动量门槛（viewers.interaction_count ≥ N；不够格不生成、自然不注入）",
    )
    profile_max_length: int = Field(
        default=400,
        ge=100,
        le=1000,
        description="画像文本长度上限（字符；提示词约束 LLM 压缩篇幅）",
    )
    profile_injection_max: int = Field(
        default=3,
        ge=1,
        le=10,
        description="每轮决策注入画像的观众数上限",
    )
    facts_per_batch: int = Field(
        default=5,
        ge=1,
        le=20,
        description="每轮话题摘要循环最多提取的事实条数",
    )


__all__ = [
    "MemoryBackend",
    "MemoryConfig",
]
