"""Memory 配置 Schema 定义（v2.0.0）

定义 ``config/memory.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [memory]
    backend = "simple"

    [memory.simple]
    recall_top_k = 5

> 单一事实源原则：``db_path`` **不在此定义**——存储与记忆共用同一 SQLite
> 库，路径权威在 storage.toml 的 ``[storage.sqlite].db_path``。

设计原则：
- ``backend`` 字面量（simple | amemorix），一键切换存储后端（§1.50）
- SimpleMemory 的具体字段（W3 由 MemoryProvider 实现补全）
- 遵循 v2.0.0 域分文件原则，记忆与存储解耦（共享 db_path）
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# 记忆后端类型
# ---------------------------------------------------------------------------


MemoryBackend = Literal[
    "simple",  # SimpleMemory（SQLite 关键词召回，无 embedding）
    "amemorix",  # Amemorix（外部服务，可选）
]


# ---------------------------------------------------------------------------
# SimpleMemory 配置（替代旧 [context] 段的会话存储）
# ---------------------------------------------------------------------------


class SimpleMemoryConfig(BaseConfig):
    """SimpleMemory 记忆后端配置

    SQLite 关键词召回实现，无 embedding。观众画像 + 事实条目都存在
    storage.sqlite.db_path 的同一数据库中。

    Attributes:
        recall_top_k: 单次召回条数上限
        viewer_profile_max: 单观众画像字段上限（防膨胀）
        fact_max_age_days: 事实条目最大保留天数（0 = 永久）
    """

    recall_top_k: int = Field(
        default=5,
        ge=1,
        le=100,
        description="单次召回条数上限（关键词检索后取 top-k）",
    )
    viewer_profile_max: int = Field(
        default=20,
        ge=1,
        description="单观众画像字段上限（防止画像无限膨胀）",
    )
    fact_max_age_days: int = Field(
        default=90,
        ge=0,
        description="事实条目最大保留天数（0 表示永久保留）",
    )


# ---------------------------------------------------------------------------
# Amemorix 配置（占位，W3 由外部 MemoryProvider 补全）
# ---------------------------------------------------------------------------


class AmemorixConfig(BaseConfig):
    """Amemorix 记忆后端配置（占位）

    Attributes:
        endpoint: Amemorix 服务地址
        api_key: API 密钥
        timeout: 单次请求超时（秒）
    """

    endpoint: str = Field(
        default="http://localhost:8100",
        description="Amemorix 服务地址",
    )
    api_key: str = Field(
        default="",
        description="API 密钥（留空则使用环境变量 AMAIDES_AMEMORIX_API_KEY）",
        json_schema_extra={"sensitive": True, "x-widget": "password"},
    )
    timeout: int = Field(
        default=30,
        ge=1,
        description="单次请求超时（秒）",
    )


# ---------------------------------------------------------------------------
# [memory] 段聚合
# ---------------------------------------------------------------------------


class MemoryConfig(BaseConfig):
    """[memory] 段聚合

    通过 ``backend`` 字面量切换记忆后端实现；每种后端的配置在对应子段。
    """

    model_config = ConfigDict(extra="forbid")

    backend: MemoryBackend = Field(
        default="simple",
        description="记忆后端实现：simple=内置 SimpleMemory（关键词召回）, amemorix=外部服务",
        json_schema_extra={
            "x-ui-type": "select",
            "x-options": ["simple", "amemorix"],
        },
    )

    simple: Optional[SimpleMemoryConfig] = Field(
        default=None,
        description="SimpleMemory 配置（backend='simple' 时生效）",
        json_schema_extra={"x-ui-type": "object"},
    )
    amemorix: Optional[AmemorixConfig] = Field(
        default=None,
        description="Amemorix 配置（backend='amemorix' 时生效）",
        json_schema_extra={"x-ui-type": "object"},
    )


# ---------------------------------------------------------------------------
# 顶层根模型（对应 config/memory.toml）
# ---------------------------------------------------------------------------


class MemoryRootConfig(BaseConfig):
    """Memory 配置根类

    对应 ``config/memory.toml`` 文件。
    """

    memory: MemoryConfig = Field(
        default_factory=MemoryConfig,
        description="[memory] 段聚合（backend + 各后端配置）",
    )


__all__ = [
    # 记忆后端类型
    "MemoryBackend",
    # 各后端 ConfigSchema
    "SimpleMemoryConfig",
    "AmemorixConfig",
    # 聚合
    "MemoryConfig",
    # 顶层根模型
    "MemoryRootConfig",
]
