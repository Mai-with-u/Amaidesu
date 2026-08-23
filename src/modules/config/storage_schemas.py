"""Storage 配置 Schema 定义（v2.0.0）

定义 ``config/storage.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [storage.sqlite]
    db_path = "data/amaidesu.db"
    wal = true

> **db_path 单一权威**：存储与 SimpleMemory 共用同一 SQLite 文件，
> 路径权威在 ``[storage.sqlite].db_path``（不在 memory.toml 重复定义，
> 避免分叉）。

设计原则：
- 目前仅支持 SQLite 后端（W3 由 storage 框架接入）
- 复用 MaiBot SQLiteConnectionManager 方案（每线程连接+WAL+SAVEPOINT，§1.39）
- 后续若增加 Postgres 等后端，可扩展为 ``[storage.postgres]`` 等子段
"""

from __future__ import annotations


from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# SQLite 后端配置
# ---------------------------------------------------------------------------


class SqliteStorageConfig(BaseConfig):
    """SQLite 存储配置

    Attributes:
        db_path: 数据库文件路径（相对项目根目录）。**单一事实源**：存储与
            SimpleMemory 共用此文件。
        wal: 是否启用 WAL 模式（Write-Ahead Logging，并发读写性能更好）
        busy_timeout_ms: 单线程占用超时（毫秒）
        foreign_keys: 是否启用外键约束
    """

    db_path: str = Field(
        default="data/amaidesu.db",
        description="数据库文件路径（相对项目根目录）。单一事实源：存储与记忆共用此库",
    )
    wal: bool = Field(
        default=True,
        description="是否启用 WAL 模式（Write-Ahead Logging，并发读写更安全）",
    )
    busy_timeout_ms: int = Field(
        default=5000,
        ge=0,
        description="数据库忙时等待超时（毫秒）",
    )
    foreign_keys: bool = Field(
        default=True,
        description="是否启用外键约束（建议保持 True）",
    )


# ---------------------------------------------------------------------------
# [storage] 段聚合
# ---------------------------------------------------------------------------


class StorageConfig(BaseConfig):
    """[storage] 段聚合

    当前仅支持 SQLite 后端；后续扩展点（如 Postgres）可加 ``[storage.postgres]`` 子段。
    """

    model_config = ConfigDict(extra="forbid")

    sqlite: SqliteStorageConfig = Field(
        default_factory=SqliteStorageConfig,
        description="SQLite 存储后端配置（存储与 SimpleMemory 共用）",
    )


# ---------------------------------------------------------------------------
# 顶层根模型（对应 config/storage.toml）
# ---------------------------------------------------------------------------


class StorageRootConfig(BaseConfig):
    """Storage 配置根类

    对应 ``config/storage.toml`` 文件。
    """

    storage: StorageConfig = Field(
        default_factory=StorageConfig,
        description="[storage] 段聚合（当前仅 SQLite）",
    )


__all__ = [
    # 后端 ConfigSchema
    "SqliteStorageConfig",
    # 聚合
    "StorageConfig",
    # 顶层根模型
    "StorageRootConfig",
]
