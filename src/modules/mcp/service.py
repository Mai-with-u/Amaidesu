"""
MCP Server 服务 - Model Context Protocol 服务端实现

提供 MCP 协议支持，让外部 AI 客户端可以调用 Amaidesu 的功能。

设计模式参考 LLMManager：
- __init__(): 构造函数
- setup(): 异步初始化
- cleanup(): 异步清理

Wave 2 将实现 FastMCP 逻辑。
"""

from typing import Any, Dict, Optional

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
        """
        初始化 MCPServerService

        Args:
            config: 服务配置，如果为 None 则使用默认配置
        """
        self.config = config or MCPServerConfig()
        self.logger = get_logger("MCPServerService")
        self._initialized = False

    async def setup(self, config: Dict[str, Any]) -> None:
        """
        异步初始化 MCP 服务

        Args:
            config: 配置字典（包含 enabled, host, port 等）

        Note:
            Wave 2 将实现 FastMCP 逻辑：
            - 创建 FastMCP 实例
            - 注册工具（send_intent 等）
            - 启动 MCP 服务器
        """
        if self._initialized:
            self.logger.warning("MCPServerService 已经初始化，跳过重复初始化")
            return

        self.logger.info(
            f"MCP 服务初始化配置: enabled={config.get('enabled')}, host={config.get('host')}, port={config.get('port')}"
        )

        # Wave 2: 实现 FastMCP 逻辑
        # 1. 创建 FastMCP 实例
        # 2. 注册 send_intent 工具
        # 3. 启动 MCP 服务器

        self._initialized = True
        self.logger.info("MCPServerService 初始化完成")

    async def cleanup(self) -> None:
        """
        清理 MCP 服务资源

        Note:
            Wave 2 将实现：
            - 停止 MCP 服务器
            - 清理 FastMCP 资源
        """
        if not self._initialized:
            return

        self.logger.info("正在清理 MCPServerService...")

        # Wave 2: 实现 FastMCP 清理逻辑
        # if self._server_task:
        #     self._server_task.cancel()
        #     try:
        #         await self._server_task
        #     except asyncio.CancelledError:
        #         pass

        self._initialized = False
        self.logger.info("MCPServerService 已清理")
