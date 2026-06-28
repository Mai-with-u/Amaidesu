"""
MCP Server 服务 - Model Context Protocol 服务端实现

提供 MCP 协议支持，让外部 AI 客户端可以调用 Amaidesu 的功能。

设计模式参考 LLMManager：
- __init__(): 构造函数
- setup(): 异步初始化
- cleanup(): 异步清理
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx
from fastmcp import FastMCP

from src.modules.logging import get_logger
from src.modules.mcp.config import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPServerService:
    """
    MCP Server 服务

    核心基础设施服务，提供 MCP 协议支持。

    生命周期：
        __init__() -> setup() -> [使用服务] -> cleanup()

    职责：
        - 提供 MCP 协议端点
        - 注册工具（tools）供外部调用
        - 转发调用至 Dashboard HTTP API

    使用示例：
        ```python
        # 在 main.py 中初始化
        mcp_config = MCPServerConfig(
            enabled=True,
            host="127.0.0.1",
            port=30214,
        )
        mcp_service = MCPServerService(mcp_config)
        await mcp_service.setup(mcp_config.model_dump())

        # 清理
        await mcp_service.cleanup()
        ```
    """

    # Dashboard 后端地址（Amaidesu 默认 60214 端口提供 HTTP API）
    _DEFAULT_DASHBOARD_BASE_URL = "http://127.0.0.1:60214"

    def __init__(self, config: MCPServerConfig):
        """初始化 MCPServerService

        Args:
            config: MCP 服务配置（必填）
        """
        self.config = config
        self.logger = get_logger("MCPServerService")
        self._initialized = False
        self._mcp: Optional[FastMCP] = None
        self._server_task: Optional[asyncio.Task] = None
        # Dashboard API 基地址，供 3 个 tool 共享
        self._dashboard_base_url = self._DEFAULT_DASHBOARD_BASE_URL

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

        # 注册 3 个独立 MCP 工具，对齐 Plugin 的 amaidesu_action / amaidesu_react 能力
        self._mcp.tool(self.send_action)
        self._mcp.tool(self.send_react)
        self._mcp.tool(self.get_status)

        host = config.get("host", "127.0.0.1")
        port = config.get("port", 30214)

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

    # ============ MCP Tool 实现（对齐 Plugin 工具能力） ============

    async def send_action(
        self,
        action_type: str,
        action_value: str,
        emotion: str,
        priority: int = 50,
        text: Optional[str] = None,
    ) -> str:
        """Send an action to Amaidesu via Dashboard API.

        对齐 Plugin 的 `amaidesu_action` 工具：构造 ``action_params`` 单键 dict 提交给
        ``/api/v1/maibot/action``，由 Dashboard 端构造 Intent 并触发后续渲染管线。

        Args:
            action_type: 动作类型（如 hotkey、expression、motion）。
            action_value: 动作值（如 smile、wave、nod、clap、dance、think、bow、point）。
            emotion: 情绪类型（如 happy、neutral、sad、angry、excited、shy、surprised、confused）。
            priority: 优先级 0-100，默认 50。
            text: 可选附带文本。

        Returns:
            成功消息（含 intent_id）。
        """
        if priority < 0 or priority > 100:
            raise ValueError("priority must be between 0 and 100")

        payload: Dict[str, Any] = {
            "priority": priority,
            "action": action_type,
            "action_params": {action_type: action_value},
            "emotion": emotion,
        }
        if text:
            payload["text"] = text

        return await self._post_to_dashboard("/api/v1/maibot/action", payload, success_prefix="Action sent")

    async def send_react(
        self,
        speech: str,
        emotion: Optional[str] = None,
        action: Optional[str] = None,
    ) -> str:
        """Send a structured reaction to Amaidesu via Dashboard API.

        对齐 Plugin 的 `amaidesu_react` 工具：``speech`` 必填，向 Dashboard 提交 speech +
        可选 emotion + 可选 action 的结构化反应。``message_type`` 字段在 Dashboard schema
        中不存在，刻意不提交（避免被静默丢弃）。

        Args:
            speech: AI 要说的台词内容（必填，非空）。
            emotion: 可选情绪（自然语言）。
            action: 可选动作描述（自然语言）。

        Returns:
            成功消息（含 intent_id）。
        """
        if not speech:
            raise ValueError("speech 不能为空")

        payload: Dict[str, Any] = {
            "priority": 50,
            "text": speech,
            "emotion": emotion if emotion else None,
            "action": action if action else None,
        }

        return await self._post_to_dashboard("/api/v1/maibot/action", payload, success_prefix="React sent")

    async def get_status(self) -> str:
        """Get Amaidesu system status from Dashboard API.

        返回 ``GET /api/v1/system/status`` 的原始 JSON 字符串。

        Returns:
            系统状态的原始 JSON 文本。
        """
        url = f"{self._dashboard_base_url}/api/v1/system/status"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
        except httpx.TimeoutException:
            raise Exception("MCP request timed out") from None
        except httpx.ConnectError as e:
            raise Exception(f"MCP connection error: {e}") from None

        if not response.is_success:
            error = self._extract_error(response)
            raise Exception(f"MCP API error: {error}")

        return response.text

    # ============ 内部辅助方法 ============

    async def _post_to_dashboard(self, path: str, payload: Dict[str, Any], success_prefix: str) -> str:
        """统一封装 POST 请求到 Dashboard API（供 send_action / send_react 共用）"""
        url = f"{self._dashboard_base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
        except httpx.TimeoutException:
            raise Exception("MCP request timed out") from None
        except httpx.ConnectError as e:
            raise Exception(f"MCP connection error: {e}") from None

        if not response.is_success:
            error = self._extract_error(response)
            raise Exception(f"MCP API error: {error}")

        result = response.json()
        intent_id = result.get("intent_id", "unknown")
        return f"{success_prefix} successfully: {intent_id}"

    @staticmethod
    def _extract_error(response: httpx.Response) -> str:
        """从非 2xx 响应中提取错误描述"""
        if response.content:
            try:
                return response.json().get("error", response.text)
            except Exception as e:
                logger.debug(f"解析错误响应 JSON 失败，使用原始文本: {e}")
                return response.text
        return response.text

    # ============ 兼容层（Python 公共方法，保留以避免破坏直接调用方） ============

    async def send_intent(
        self,
        action: Optional[str] = None,
        emotion: Optional[str] = None,
        speech: Optional[str] = None,
        context: Optional[str] = None,
        priority: int = 50,
    ) -> str:
        """Send Intent to Amaidesu via internal HTTP API (Python compatibility layer).

        保留为向后兼容方法；新代码请直接使用 ``send_action`` / ``send_react`` MCP tool。
        """
        if priority < 0 or priority > 100:
            raise ValueError("priority must be between 0 and 100")

        payload: Dict[str, Any] = {
            "action": action,
            "emotion": emotion,
            "text": speech,
            "priority": priority,
            "action_params": {},
        }

        return await self._post_to_dashboard("/api/v1/maibot/action", payload, success_prefix="Intent sent")
