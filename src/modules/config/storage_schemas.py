"""Storage 配置 Schema 定义

定义 ``config/storage.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [sqlite]
    db_path = "data/amaidesu.db"
    busy_timeout_ms = 5000

    [viewers]
    name_match_window_days = 0

    [retention]
    raw_days = 0
    viewer_absence_days = 0

    [privacy]
    forget_phrases = ["忘了我", "忘了我吧", "别记我", "别记我了", "不要记我", "别记住我"]
    retract_phrases = ["算了", "算了吧", "开玩笑的", "开玩笑啦", "别删", "别删了", "别忘了我", "记住我"]
    key_path = "secrets/forget.key"

> **db_path 单一权威**：所有业务表共用同一 SQLite 文件，路径权威在 ``[sqlite].db_path``。
>
> **wal / foreign_keys / secure_delete 不再配置化**：WAL、外键约束与安全删除在
> ``src/modules/storage/connection.py`` 内 PRAGMA 硬编码开启。

设计原则：
- 顶层段扁平化（无 ``[storage]`` 包裹层）
- 保留期策略交给使用者：默认 0 = 不自动删除
"""

from __future__ import annotations

from typing import List

from pydantic import ConfigDict, Field

from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig

DEFAULT_FORGET_PHRASES: tuple[str, ...] = (
    "忘了我",
    "忘了我吧",
    "别记我",
    "别记我了",
    "不要记我",
    "别记住我",
)

DEFAULT_RETRACT_PHRASES: tuple[str, ...] = (
    "算了",
    "算了吧",
    "开玩笑的",
    "开玩笑啦",
    "别删",
    "别删了",
    "别忘了我",
    "记住我",
)


class SqliteStorageConfig(BaseConfig):
    """SQLite 存储配置（``[sqlite]`` 顶层段）"""

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


class ViewerIdentityConfig(BaseConfig):
    """观众身份关联配置（``[viewers]`` 顶层段）"""

    name_match_window_days: int = Field(
        default=0,
        ge=0,
        title="昵称关联时限",
        description="新出现的观众 ID 按昵称自动关联老观众的时限：老观众的这个昵称多少天内出现过、且同名只有一位，"
        "才认作同一人；0 = 不自动关联，同名的新 ID 都先建成新观众，在待确认页由你确认。"
        "打开有冒充风险：起个同名新号就会被认成老观众，继承老观众的记录。走 B 站官方开放平台时，"
        "把不同来源的观众 ID 关联起来还可能与开放平台管理规范冲突",
    )


class RetentionConfig(BaseConfig):
    """数据保留期配置（``[retention]`` 顶层段，默认均不自动删除）"""

    raw_days: int = Field(
        default=0,
        ge=0,
        title="原始明细与备份保留天数",
        description="观众弹幕、付费明细身份与留言列、话题摘要、LLM 请求响应正文与备份的保留天数"
        "（前四项从场次结束算起，备份按备份条目自身的修改时刻算；备份包括迁移前整库快照与迁移时"
        "移入的旧记忆目录）；0 = 不自动删除。个人信息保护法第 19、47 条与开放平台协议要求保存期限为"
        "实现目的所必需的最短时间，走官方开放平台采集时建议自行设置",
    )
    viewer_absence_days: int = Field(
        default=0,
        ge=0,
        title="缺席观众清理天数",
        description="最后活跃超过多少天的观众自动删除记录（不加入不记录名单）；0 = 不自动删除。"
        "注意：设置后隔了很久回来的老观众会被当作新人",
    )


class PrivacyConfig(BaseConfig):
    """观众遗忘命令与不记录名单配置（``[privacy]`` 顶层段）"""

    forget_phrases: List[str] = Field(
        default_factory=lambda: list(DEFAULT_FORGET_PHRASES),
        title="遗忘命令短语表",
        description="观众弹幕去掉称呼并按标点/表情切分后，每个小句都在此列表中即触发遗忘命令："
        "立即停止记录该观众，并在之后第一次下播后（最迟 24 小时）删除其记录",
    )
    retract_phrases: List[str] = Field(
        default_factory=lambda: list(DEFAULT_RETRACT_PHRASES),
        title="撤回遗忘短语表",
        description="宽限删除尚未执行前，该观众本人发出命中此列表的短语即可取消删除、恢复正常记录",
    )
    key_path: str = Field(
        default="secrets/forget.key",
        min_length=1,
        title="不记录名单密钥文件路径",
        description="用于计算不记录名单外部 ID 带密钥摘要（HMAC-SHA256）的库外密钥文件路径"
        "（相对项目根目录或绝对路径）。请单独妥善备份：文件丢失且不记录名单非空时程序将拒绝启动",
        json_schema_extra={"x-ui-advanced": True},
    )


class StorageRootConfig(BaseConfig):
    """Storage 配置根类（对应 ``config/storage.toml``）"""

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
    retention: RetentionConfig = Field(
        default_factory=RetentionConfig,
        title="数据保留期",
        description="弹幕明细、备份与缺席观众的自动清理周期（默认不自动删除）",
    )
    privacy: PrivacyConfig = Field(
        default_factory=PrivacyConfig,
        title="隐私与遗忘命令",
        description="观众自主触发遗忘删除的短语、撤回短语与不记录名单密钥",
    )


__all__ = [
    "DEFAULT_FORGET_PHRASES",
    "DEFAULT_RETRACT_PHRASES",
    "PrivacyConfig",
    "RetentionConfig",
    "SqliteStorageConfig",
    "StorageRootConfig",
    "ViewerIdentityConfig",
]
