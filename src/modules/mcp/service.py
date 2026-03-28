"""
MCP Server 服务 - Model Context Protocol 服务端实现

提供 MCP 协议支持，让外部 AI 客户端可以调用 Amaidesu 的功能。

设计模式参考 LLMManager：
- __init__(): 构造函数
- setup(): 异步初始化
- cleanup(): 异步清理
"""

import asyncio
from typing import Any, Dict, Optional

import httpx
from fastmcp import FastMCP

from src.modules.logging import get_logger
from src.modules.mcp.config import MCPServerConfig


class MCPServerService:
    """
    MCP Server 服务

    核心基础设施服务，提供 MCP 协议支持。

    生命周期：
        __init__() -> setup() -> [使用服务] -> cleanup()

    职责：
        - 提供 MCP 协议端点
        - 注册工具（tools）供外部调用
        - 处理 Intent 发送请求

    使用示例：
        ```python
        # 在 main.py 中初始化
        mcp_config = MCPServerConfig(
            enabled=True,
            host="127.0.0.1",
            port=80214,
        )
        mcp_service = MCPServerService(mcp_config)
        await mcp_service.setup(mcp_config.model_dump())

        # 清理
        await mcp_service.cleanup()
        ```
    """

    def __init__(self, config: Optional[MCPServerConfig] = None):
        """初始化 MCPServerService"""
        self.config = config or MCPServerConfig()
        self.logger = get_logger("MCPServerService")
        self._initialized = False
        self._mcp: Optional[FastMCP] = None
        self._server_task: Optional[asyncio.Task] = None

    async def setup(self, config: Dict[str, Any]) -> None:
        """异步初始化 MCP 服务"""
        if self._initialized:
            self.logger.warning("MCPServerService 已经初始化，跳过重复初始化")
            return

        self.logger.info(
            f"MCP 服务初始化配置: enabled={config.get('enabled')}, host={config.get('host')}, port={config.get('port')}"
        )

        if not config.get("enabled", False):
            self.logger.info("MCP 服务未启用")
            self._initialized = True
            return

        self._mcp = FastMCP("amaidesu-mcp")

        @self._mcp.tool()
        async def send_intent_tool(
            action: str = None,
            emotion: str = None,
            speech: str = None,
            context: str = None,
            priority: int = 50,
        ) -> str:
            """Send Intent to Amaidesu via internal API."""
            return await self._send_intent_internal(action, emotion, speech, context, priority)

        host = config.get("host", "127.0.0.1")
        port = config.get("port", 8021)

        self.logger.info(f"启动 MCP 服务器: {host}:{port} (stateless_http=True)")

        self._server_task = asyncio.create_task(
            self._mcp.run_async(transport="http", host=host, port=port, stateless_http=True)
        )

        self._initialized = True
        self.logger.info("MCP 服务初始化完成")

    async def cleanup(self) -> None:
        """清理 MCP 服务资源"""
        if not self._initialized:
            return

        self.logger.info("正在清理 MCP 服务...")

        if self._server_task:
            self._server_task.cancel()
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("MCP 服务器关闭超时，强制取消")
            except asyncio.CancelledError:
                pass

        self._initialized = False
        self.logger.info("MCP 服务已清理")

    async def _send_intent_internal(
        self,
        action: str = None,
        emotion: str = None,
        speech: str = None,
        context: str = None,
        priority: int = 50,
    ) -> str:
        """内部实现 send_intent（供 MCP tool 和直接调用使用）"""
        if priority < 0 or priority > 100:
            raise ValueError("priority must be between 0 and 100")

        payload = {
            "action": action,
            "emotion": emotion,
            "text": speech,
            "priority": priority,
            "action_params": {},
        }

        url = "http://127.0.0.1:60214/api/v1/maibot/action"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
        except httpx.TimeoutException:
            raise Exception("MCP request timed out") from None

        if not response.is_success:
            error = response.json().get("error", response.text) if response.content else response.text
            raise Exception(f"MCP API error: {error}")

        result = response.json()
        intent_id = result.get("intent_id", "unknown")
        return f"Intent sent successfully: {intent_id}"

    async def send_intent(
        self,
        action: str = None,
        emotion: str = None,
        speech: str = None,
        context: str = None,
        priority: int = 50,
    ) -> str:
        """Send Intent to Amaidesu via internal HTTP API."""
        return await self._send_intent_internal(action, emotion, speech, context, priority)
