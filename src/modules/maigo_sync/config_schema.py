"""跨端记忆与生活流交换（MaiGO）配置契约（infra.toml ``[maigo_sync]``）。"""

from __future__ import annotations

from pydantic import Field, field_validator

from src.modules.config.schemas.base import BaseConfig


class MaiGOSyncConfigSchema(BaseConfig):
    """连接 MaiGO 跨端记忆交换服务的配置。

    默认关闭；空串表示未配置服务端地址。启用后在正式场开播签出今日跨端简报、
    鲜活话头与熟人映射，下播完成本地整理后将本场档案与承诺变更签入回推。
    """

    enabled: bool = Field(
        default=False,
        title="是否启用",
        description="是否启用 MaiGO 跨端记忆与生活流同步",
    )
    url: str = Field(
        default="",
        title="服务端地址",
        description="MaiGO HTTP 服务根地址（如 http://127.0.0.1:8768）；空串表示未设置",
    )
    token: str = Field(
        default="",
        title="访问令牌",
        description="与 MaiGO server.auth_token 一致的 Bearer 令牌；本机未开启鉴权时可留空",
    )
    branch_id: str = Field(
        default="amaidesu",
        title="分支标识",
        description="向 MaiGO 自报的稳定分支标识（默认 amaidesu）",
    )
    heartbeat_interval_ms: int = Field(
        default=60_000,
        ge=5_000,
        title="心跳间隔",
        description="直播期间向 MaiGO 上报在播状态、当前环节与已选话头的间隔（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    request_timeout_ms: int = Field(
        default=5_000,
        ge=500,
        title="请求超时",
        description="单次签出、心跳与签入请求的超时上限（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    inject_topic_cards: bool = Field(
        default=True,
        title="注入跨端话头卡",
        description="开播签出时是否将 MaiGO 鲜活话头合并进本地杂谈话题匣",
    )
    inject_person_cues: bool = Field(
        default=True,
        title="注入跨端熟人线索",
        description="直播间或游戏里命中显式绑定的跨端熟人时，是否在本地线索块补充脱敏近况",
    )

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        """校验地址前缀并去除尾部多余斜杠，空串表示未设置直接放行。"""
        cleaned = (value or "").strip().rstrip("/")
        if cleaned and not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头")
        return cleaned

    @field_validator("branch_id")
    @classmethod
    def _check_branch_id(cls, value: str) -> str:
        """规范化分支标识，缺省回落为 amaidesu。"""
        cleaned = (value or "").strip().lower()
        return cleaned or "amaidesu"
