"""Storage 配置 Schema 定义

定义 ``config/storage.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [sqlite]
    db_path = "data/amaidesu.db"
    busy_timeout_ms = 5000

    [memory]
    backend = "simple"

> **db_path 单一权威**：存储与 SimpleMemory 共用同一 SQLite 文件，
> 路径权威在 ``[sqlite].db_path``。
>
> **wal / foreign_keys 不再配置化**：WAL 与外键约束在
> ``src/modules/storage/connection.py`` 内 PRAGMA 硬编码（写死开启），
> 配置侧只保留 ``db_path`` 与 ``busy_timeout_ms``——保留 dead
> 字段会导致开关分叉，故废除。

设计原则：
- 顶层段扁平化（无 ``[storage]`` 包裹层）
- 当前仅支持 SQLite 后端；wal/foreign_keys 由 SQLite 连接模块保证
- 后续若增加 Postgres，可加 ``[postgres]`` 顶层段
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# SQLite 后端配置
# ---------------------------------------------------------------------------


class SqliteStorageConfig(BaseConfig):
    """SQLite 存储配置（``[sqlite]`` 顶层段）

    Attributes:
        db_path: 数据库文件路径（相对项目根目录）。**单一事实源**：存储与
            SimpleMemory 共用此文件。
        busy_timeout_ms: 单线程占用超时（毫秒；实际生效 5000ms，对齐 PRAGMA）
    """

    db_path: str = Field(
        default="data/amaidesu.db",
        description="数据库文件路径（相对项目根目录）。单一事实源：存储与记忆共用此库",
    )
    busy_timeout_ms: int = Field(
        default=5000,
        ge=0,
        description="数据库忙时等待超时（毫秒；连接模块读此值注入 PRAGMA busy_timeout）",
    )


# ---------------------------------------------------------------------------
# 顶层根模型（对应 config/storage.toml）
# ---------------------------------------------------------------------------


class StorageRootConfig(BaseConfig):
    """Storage 配置根类

    对应 ``config/storage.toml`` 文件。顶层扁平结构：
    - ``[sqlite]``：数据库连接配置
    - ``[memory]``：记忆子系统装配（backend 行切换）

    注：记忆子模块（bootstrap.py）按新扁平结构从 ``config["sqlite"]`` /
    ``config["memory"]`` 直接读取；旧的 ``[storage]`` 包裹层已废除。
    """

    model_config = ConfigDict(extra="forbid")

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, description="文件元数据")
    sqlite: SqliteStorageConfig = Field(
        default_factory=SqliteStorageConfig,
        description="SQLite 存储后端配置（存储与 SimpleMemory 共用）",
    )


__all__ = [
    "SqliteStorageConfig",
    "StorageRootConfig",
]
