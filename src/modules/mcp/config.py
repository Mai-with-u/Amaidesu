"""MCP 外部工具源配置 Schema

定义 ``[tools.mcp].config`` 段（tools.toml）的 Pydantic 校验模型，
描述接入哪些 MCP server、以什么 transport 连接。

配置结构（TOML 视角）::

    [tools.mcp]
    enabled = true

    [tools.mcp.config]
    # 每个 MCP server 一个条目，key = server 名（也是工具名前缀来源）
    [tools.mcp.config.servers.my_server]
    transport = "http"  # http（Streamable HTTP）| stdio
    url = "http://127.0.0.1:8766/mcp"  # http 传输时的端点
    # command / args 用于 stdio 传输
    # command = "npx"
    # args = ["-y", "some-mcp-server"]
    enabled = true

设计要点：
- 服务端能力契约（tools 的 JSON Schema 等）由运行时 ``list_tools`` 动态拉取，
  不在配置内硬编码；配置只描述"连谁、怎么连"。
- 配置段的权威 Schema 在 ``src/modules/config/tools_schemas.py``（McpProviderConfig）；
  本模块的 McpExternalConfig 仅为 bind 路径的宽松解析器。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.modules.config.schemas.base import BaseConfig

# transport 类型：http（Streamable HTTP，生产推荐）/ stdio（子进程）
McpTransportType = Literal["http", "stdio"]


class McpServerConfig(BaseModel):
    """单个 MCP server 的连接配置。

    Attributes:
        enabled: 是否连接此 server（False 时跳过）
        transport: 传输方式，"http"=Streamable HTTP（MaiCraft 等远程服务），
            "stdio"=子进程拉起（如 npx 启动的 MCP server）
        url: http 传输时的端点 URL（transport="http" 必填）
        command: stdio 传输时的启动命令（transport="stdio" 必填）
        args: stdio 传输时的命令行参数
        env: stdio 传输时的环境变量（可选）
        headers: http 传输时附加的 HTTP 头（如 Authorization）
        reconnect: 是否启用自动重连（连接中断后按退避策略重试）
        timeout_seconds: 连接超时（秒）
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="是否连接此 MCP server")
    transport: McpTransportType = Field(
        default="http",
        description="传输方式：http（Streamable HTTP）/ stdio（子进程）",
    )
    url: str = Field(
        default="",
        description="http 传输的端点 URL（如 http://127.0.0.1:8766/mcp）；空串 = 未设置",
    )
    command: str = Field(default="", description="stdio 传输的启动命令；空串 = 未设置")
    args: List[str] = Field(default_factory=list, description="stdio 传输的命令行参数")
    env: Dict[str, str] = Field(default_factory=dict, description="stdio 传输的环境变量")
    headers: Dict[str, str] = Field(default_factory=dict, description="http 传输附加请求头")
    reconnect: bool = Field(default=True, description="连接中断后自动重连")
    timeout_seconds: float = Field(default=30.0, ge=1.0, description="连接超时（秒）")

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        # 空串 = 未设置（http 传输未配置），放行
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头")
        return v


class McpExternalConfig(BaseConfig):
    """[tools.external] 段聚合模型（装载进 ``ToolPackMeta.config``）。

    Attributes:
        servers: 键名 = server 别名；值 = McpServerConfig
    """

    model_config = ConfigDict(extra="allow")

    servers: Dict[str, McpServerConfig] = Field(
        default_factory=dict,
        description="MCP server 注册表（key = server 别名）",
    )

    def enabled_servers(self) -> List[str]:
        """返回启用（enabled=true）的 server 别名列表（按配置顺序）。"""
        return [name for name, cfg in self.servers.items() if cfg.enabled]

    # 允许任意额外的字段容错（如未来扩展 grep / prompts 等不在本模块范围），
    # 但不静默吞掉拼写错误——由调用方在装配时校验未知键并记日志。
    @classmethod
    def parse_extra(
        cls,
        raw: Optional[Dict[str, Any]] = None,
    ) -> "McpExternalConfig":
        """从 ``[tools.external].config`` 原始 dict 解析（容忍 None / 非 dict）。"""
        if not isinstance(raw, dict):
            raw = {}
        return cls.model_validate(raw)


__all__ = ["McpServerConfig", "McpExternalConfig", "McpTransportType"]
