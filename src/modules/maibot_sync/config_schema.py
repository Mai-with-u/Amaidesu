"""直播近况同步的配置 Schema（infra.toml ``[maibot_sync]``）。"""

from urllib.parse import urljoin

from pydantic import Field, field_validator

from src.modules.config.schemas.base import BaseConfig


class MaiBotSyncConfigSchema(BaseConfig):
    """把直播近况推给 MaiBot 适配器插件（MaiBot-Amaidesu-Adapter）的配置。

    地址与令牌对应插件 ``[server]`` 段：与 MCP 服务同一端口、同一令牌，路径为
    ``/live-status``；共享笔记走同一服务的 ``/notes``（由近况地址推出）。默认关闭；
    空串表示未配置，开启后地址或令牌缺失时不装配。
    """

    enabled: bool = Field(
        default=False,
        title="是否启用",
        description="是否把开播、换环节、收播与场次回顾推给 MaiBot 适配器插件",
    )
    url: str = Field(
        default="",
        title="接收地址",
        description="适配器插件的近况接收地址（如 http://127.0.0.1:8767/live-status）；空串 = 未设置",
    )
    token: str = Field(
        default="",
        title="访问令牌",
        description="与适配器插件 server.auth_token 相同的令牌；空串 = 未设置",
    )
    share_notes: bool = Field(
        default=True,
        title="共享长期笔记",
        description=(
            "是否与 MaiBot 共用主播的长期笔记（答应过的事、老梗、长线目标、关于我自己）："
            "正本放在插件里，QQ 那边的麦麦也看得到、QQ 里答应的事也会记进来；本地 MEMORY.md 当缓存，"
            "MaiBot 不在线时照常开播、之后自动补推。只同步正式场的笔记"
        ),
    )
    send_transcript: bool = Field(
        default=True,
        title="推送整场记录",
        description=(
            "回顾写好后，是否把这场的全部对话（弹幕、醒目留言、主播发言）一并推给插件，"
            "由插件回放进 MaiBot 的直播间会话、进长期记忆；插件侧还要打开 live_memory.enabled"
        ),
    )
    heartbeat_interval_ms: int = Field(
        default=60_000,
        ge=5_000,
        title="心跳间隔",
        description="直播期间重发完整近况的间隔（毫秒）；插件据此判断直播是否仍在进行",
        json_schema_extra={"x-ui-advanced": True},
    )
    request_timeout_ms: int = Field(
        default=5_000,
        ge=500,
        title="请求超时",
        description="单次上报的超时（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )

    @property
    def notes_url(self) -> str:
        """共享笔记的接口地址：与近况接口同一服务，路径末段换成 ``notes``。"""
        return urljoin(self.url, "notes") if self.url else ""

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        # 空串 = 未设置，放行；其余必须是 http(s) 地址
        if value and not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头")
        return value
