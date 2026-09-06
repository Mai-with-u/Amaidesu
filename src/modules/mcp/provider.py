"""McpToolProvider —— 把一个 MCP server 的工具暴露为 Amaidesu 统一工具

实现 ``ToolProvider`` Protocol：
- ``list_tools()``：同步返回缓存的 ToolSpec 列表（MCP list_tools 是 async，
  与 Provider 协议同步签名之间的阻抗通过**连接时预拉缓存**化解）
- ``invoke()``：剥前缀 → 转发到 MCP client → 映射结果；永远不抛异常

## 可见性语义（★ 关键设计）
- ``provider`` 默认 = server 名（如 maicraft → "maicraft"）：注册后工具进入
  全局 ToolRegistry，任何 Agent（主播 / 游戏）通过 ``registry.invoke()``
  均可调用——与 Claude Code 的 MCP 插件语义一致："看到"即"可调"。
- 若内容层希望工具出现在指定 provider 过滤路径（如 ``list_tools(provider=
  "game")``），可在装配时显式传 ``provider="game"`` 覆盖（按需，非硬编码）。
"""

from __future__ import annotations

import time
from typing import Iterable, List, Optional

from src.modules.logging import get_logger
from src.modules.mcp import mapper
from src.modules.mcp.client import McpClient
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import ToolProvider

logger = get_logger("McpToolProvider")


class McpToolProvider(ToolProvider):
    """MCP server 工具 Provider（缓存 specs + invoke 转发）。

    Attributes:
        server_name: MCP server 别名（来自配置 key）
        prefix: 工具名前缀（默认 ``<server_name>_``）
        provider: 注册来源标记（默认 = server 名，如 "maicraft"；
            可显式覆盖如 "game"）
    """

    def __init__(
        self,
        *,
        client: McpClient,
        server_name: str,
        prefix: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> None:
        self._client = client
        self.server_name = server_name
        self.prefix = prefix if prefix is not None else f"{server_name}_"
        self._provider = provider or server_name
        self._specs: List[ToolSpec] = []
        self._synced = False

    @property
    def name(self) -> str:
        """Provider 标识（日志/去重用）。"""
        return f"McpProvider:{self.server_name}"

    async def setup(self) -> int:
        """连接并预拉工具列表 → 填充缓存 specs（装配时调用一次）。

        Returns:
            缓存的工具数量；连接失败时为 0（list_tools 返回空）
        """
        if not self._client.connected:
            ok = await self._client.connect()
            if not ok:
                logger.warning(f"MCP Provider '{self.server_name}' 连接失败，工具列表为空")
                self._specs = []
                self._synced = True
                return 0
        tools = await self._client.list_tools()
        self._specs = [mapper.to_spec(t, prefix=self.prefix, provider=self._provider) for t in tools]
        self._synced = True
        logger.info(
            f"MCP Provider '{self.server_name}' 工具缓存就绪（{len(self._specs)} 个，"
            f"prefix='{self.prefix}', provider={self._provider}）"
        )
        return len(self._specs)

    def list_tools(self) -> Iterable[ToolSpec]:
        """同步返回缓存的 ToolSpec（连接时预拉；未 setup 时为空）。"""
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """执行 MCP 工具：剥前缀 → 转发 → 映射结果。永远不抛异常。"""
        started_ms = int(time.time() * 1000)
        full_name = invocation.tool_name
        mcp_name = mapper.strip_tool_prefix(full_name, self.prefix)

        # 前置检查：工具是否属于本 Provider（不知道的工具 → 失败结果，不抛）
        known_names = [s.name for s in self._specs]
        if full_name not in known_names:
            return ToolExecutionResult(
                tool_name=full_name,
                success=False,
                error_message=f"工具 '{full_name}' 不属于 MCP Provider '{self.server_name}'",
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        result = await self._client.call_tool(mcp_name, dict(invocation.arguments or {}))
        duration_ms = int(time.time() * 1000) - started_ms
        return mapper.to_result(result, tool_name=full_name, duration_ms=duration_ms)

    async def close(self) -> None:
        """关闭底层连接（幂等）。"""
        await self._client.close()


__all__ = ["McpToolProvider"]
