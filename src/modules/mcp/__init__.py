"""
MCP Server Module - Model Context Protocol 服务端模块

提供 MCP 协议支持，让外部 AI 客户端（如 MaiBot MCP Bridge）可以调用 Amaidesu 的功能。

设计模式参考 LLMManager/ContextService：
- __init__(): 构造函数，接收配置
- setup(): 初始化服务
- cleanup(): 清理资源
"""

from src.modules.mcp.config import MCPServerConfig
from src.modules.mcp.service import MCPServerService

# 模块导出
MCPServerModule = MCPServerService

__all__ = [
    "MCPServerModule",
    "MCPServerService",
    "MCPServerConfig",
]
