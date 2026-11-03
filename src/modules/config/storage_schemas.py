"""Storage 配置 Schema 定义

定义 ``config/storage.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [sqlite]
    db_path = "data/amaidesu.db"
    busy_timeout_ms = 5000

    [viewers]
    name_match_window_days = 0

> **db_path 单一权威**：所有业务表（含主播记忆的观众画像与场次回顾）共用
> 同一 SQLite 文件，路径权威在 ``[sqlite].db_path``。
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
        db_path: 数据库文件路径（相对项目根目录）。**单一事实源**：全部
            业务表共用此文件。
        busy_timeout_ms: 单线程占用超时（毫秒；实际生效 5000ms，对齐 PRAGMA）
    """

    db_path: str = Field(
        default="data/amaidesu.db",
        title="数据库路径",
        description="数据库文件路径（相对项目根目录）。单一事实源：存储与记忆共用此库",
    )
    busy_timeout_ms: int = Field(
        default=5000,
        ge=0,
        title="忙等待超时",
        description="数据库被占用时最多等多久（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )


# ---------------------------------------------------------------------------
# 观众身份关联
# ---------------------------------------------------------------------------


class ViewerIdentityConfig(BaseConfig):
    """观众身份关联配置（``[viewers]`` 顶层段）

    同一平台出现没见过的外部 ID 时，记账器按当前昵称找老观众。默认不自动关联
    （时限 0）：同名的新 ID 先建成新观众，挂一条并入建议等运营者在待确认页确认——
    起个同名新号就能被认成老观众、继承老观众的记录，自动关联把这个口子开给了
    任何人。使用者自己填了天数，才在"昵称时限内出现过、且只有一位候选"时自动
    关联；超出时限说明昵称可能已经易主（老观众改名后旧名被别人拿走），同样转人工。

    Attributes:
        name_match_window_days: 自动关联的时限（天；0 = 不自动关联）
    """

    name_match_window_days: int = Field(
        default=0,
        ge=0,
        title="昵称关联时限",
        description="新出现的观众 ID 按昵称自动关联老观众的时限：老观众的这个昵称多少天内出现过、且同名只有一位，"
        "才认作同一人；0 = 不自动关联，同名的新 ID 都先建成新观众，在待确认页由你确认。"
        "打开有冒充风险：起个同名新号就会被认成老观众，继承老观众的记录。走 B 站官方开放平台时，"
        "把不同来源的观众 ID 关联起来还可能与开放平台管理规范冲突",
    )


# ---------------------------------------------------------------------------
# 顶层根模型（对应 config/storage.toml）
# ---------------------------------------------------------------------------


class StorageRootConfig(BaseConfig):
    """Storage 配置根类

    对应 ``config/storage.toml`` 文件。顶层扁平结构：
    - ``[sqlite]``：数据库连接配置
    - ``[viewers]``：观众身份关联

    注：存储装配（``storage/bootstrap.py``）从 ``config["sqlite"]`` 直接读取
    （顶层扁平段位，无包裹层）。
    """

    model_config = ConfigDict(extra="forbid")

    __file_name__ = "storage.toml"
    __section_label__ = "存储"

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, title="文件元数据", description="文件元数据")
    sqlite: SqliteStorageConfig = Field(
        default_factory=SqliteStorageConfig,
        title="SQLite 配置",
        description="SQLite 存储后端配置（全部业务表共用同一库）",
    )
    viewers: ViewerIdentityConfig = Field(
        default_factory=ViewerIdentityConfig,
        title="观众身份",
        description="观众身份关联（按昵称把新出现的 ID 关联到老观众）",
    )


__all__ = [
    "SqliteStorageConfig",
    "StorageRootConfig",
    "ViewerIdentityConfig",
]
