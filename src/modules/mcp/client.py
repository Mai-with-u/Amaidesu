"""MCP 客户端封装（基于 FastMCP）

职责：只做"通道"——按配置建立连接、拉取工具元数据、转发工具调用。
**不含任何具体 MCP server 的知识**（不硬编码工具名 / 能力名 / 资源名）。

- transport 选择：http（Streamable HTTP；MaiCraft 等远程服务）
  / stdio（子进程拉起，如 npx）
- 生命周期：``connect()`` 建立连接并预拉工具列表；``close()`` 关闭；
  ``call_tool()`` 转发调用并返回 mcp 原始结果（由 mapper 转换）
- 所有方法不抛异常给上层通道语义之外的调用方？——由 Provider 层统一
  转换为 ``ToolExecutionResult``；本类保持轻量，仅在连接层面兜底。

设计要点：
- FastMCP 的 ``Client`` 用 ``async with`` 上下文管理连接；本类内部持有
  client 实例，由 ``connect()`` / ``close()`` 显式开关（不用 with 语法，
  便于 Provider 长期持有并使用）。
- 连接超时 / 自动重连：FastMCP transport 层内建基础重试，本类只管理
  生命周期与工具缓存；更复杂的重连策略（退避/jitter）留给未来
  （v1 有参考实现，第一步保持精简）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src.modules.logging import get_logger
from src.modules.mcp.config import McpServerConfig

logger = get_logger("McpClient")


class McpClient:
    """单个 MCP server 的客户端封装。

    Attributes:
        name: server 别名（来自配置的 key；日志/诊断用）
        config: 该 server 的配置（McpServerConfig）
    """

    def __init__(self, name: str, config: McpServerConfig) -> None:
        self.name = name
        self.config = config
        self._client: Any = None  # fastmcp.Client（延迟构造）
        self._connected = False

    @property
    def connected(self) -> bool:
        """是否已建立连接。"""
        return self._connected

    def _build_transport(self) -> Any:
        """构造 FastMCP transport 对象（按配置的传输方式）。

        Returns:
            fastmcp.client.transports 的 transport 实例
        """
        # 延迟 import：避免模块加载时强制依赖 fastmcp（可选重型依赖惯例）
        from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

        cfg = self.config
        if cfg.transport == "http":
            if not cfg.url:
                raise ValueError(f"MCP server '{self.name}' transport=http 需要配置 url")
            return StreamableHttpTransport(
                url=cfg.url,
                headers=cfg.headers or None,
            )
        # stdio
        if not cfg.command:
            raise ValueError(f"MCP server '{self.name}' transport=stdio 需要配置 command")
        return StdioTransport(
            command=cfg.command,
            args=list(cfg.args or []),
            env=dict(cfg.env or {}),
        )

    async def connect(self, timeout_seconds: Optional[float] = None) -> bool:
        """建立连接并预拉工具列表（连接成功与否的核心判据）。

        Returns:
            是否连接成功（连接/预拉失败均返回 False，不抛异常）
        """
        try:
            # 延迟 import FastMCP Client
            from fastmcp import Client

            transport = self._build_transport()
            client = Client(transport)
            await client.__aenter__()
            self._client = client
            self._connected = True
            logger.info(f"MCP server '{self.name}' 已连接（transport={self.config.transport}）")
            return True
        except Exception as exc:  # noqa: BLE001 - 连接边界兜底
            logger.warning(f"MCP server '{self.name}' 连接失败: {type(exc).__name__}: {exc}")
            self._client = None
            self._connected = False
            return False

    async def list_tools(self) -> List[Any]:
        """拉取 server 暴露的工具元数据列表（FastMCP Tool 对象）。

        Returns:
            mcp.types.Tool 列表；未连接时返回空列表
        """
        if not self._connected or self._client is None:
            logger.warning(f"MCP server '{self.name}' 未连接，list_tools 返回空")
            return []
        try:
            tools = await self._client.list_tools()
            return list(tools or [])
        except Exception as exc:  # noqa: BLE001 - 通道边界兜底
            logger.warning(f"MCP server '{self.name}' list_tools 失败: {type(exc).__name__}: {exc}")
            return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """调用 server 上的工具，返回 FastMCP CallToolResult（原始结果）。

        Args:
            tool_name: MCP 侧工具原名（**不带前缀**——前缀是 Amaidesu 侧
                命名空间，调用前由 Provider 剥掉）
            arguments: 工具参数 dict

        Returns:
            CallToolResult；未连接 / 调用失败时返回 None（由 Provider 转换为错误结果）
        """
        if not self._connected or self._client is None:
            logger.warning(f"MCP server '{self.name}' 未连接，无法调用工具 '{tool_name}'")
            return None
        try:
            started = time.time()
            result = await self._client.call_tool(tool_name, arguments)
            duration_ms = int((time.time() - started) * 1000)
            logger.debug(f"MCP 调用 {self.name}.{tool_name} 完成（{duration_ms}ms）")
            return result
        except Exception as exc:  # noqa: BLE001 - 通道边界兜底
            logger.warning(f"MCP server '{self.name}' 调用工具 '{tool_name}' 失败: {type(exc).__name__}: {exc}")
            return None

    async def close(self) -> None:
        """关闭连接（幂等；未连接时无事可做）。"""
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - 关闭边界兜底
                logger.warning(f"MCP server '{self.name}' 关闭连接时异常: {type(exc).__name__}: {exc}")
        self._client = None
        self._connected = False
        logger.info(f"MCP server '{self.name}' 已关闭")


__all__ = ["McpClient"]
