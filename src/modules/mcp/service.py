"""
MCP Server 服务 - Model Context Protocol 服务端实现

提供 MCP 协议支持,让外部 AI 客户端可以调用 Amaidesu 的功能。
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
    """MCP Server 服务。

    新工具:
    - send_action: 触发结构化 action + emotion(参数 action_name / action_parameters /
      emotion_name / emotion_intensity / text,无 priority)
    - query_capabilities: GET /api/v1/capabilities(全限定 action 列表)
    - query_handler_names: GET /api/v1/handlers(handler 名列表)
    - get_status: GET /api/v1/system/status

    已删除(对齐 Plugin 决策):
    - send_react: 合并进 send_action(speech 参数)
    - send_intent: 旧 backward-compat shim,被 Plugin 端 send_action 替代
    """

    _DEFAULT_DASHBOARD_BASE_URL = "http://127.0.0.1:60214"

    def __init__(self, config: MCPServerConfig, output_handler_manager=None):
        self.config = config
        self.logger = get_logger("MCPServerService")
        self._initialized = False
        self._mcp: Optional[FastMCP] = None
        self._server_task: Optional[asyncio.Task] = None
        self._dashboard_base_url = self._DEFAULT_DASHBOARD_BASE_URL
        self._output_handler_manager = output_handler_manager

    async def setup(self, config: Dict[str, Any]) -> None:
        if self._initialized:
            self.logger.warning("MCPServerService 已经初始化,跳过重复初始化")
            return

        self.logger.info(
            f"MCP 服务初始化配置: enabled={config.get('enabled')}, host={config.get('host')}, port={config.get('port')}"
        )

        if not config.get("enabled", False):
            self.logger.info("MCP 服务未启用")
            self._initialized = True
            return

        self._mcp = FastMCP("amaidesu-mcp")

        self._mcp.tool(self.send_action)
        self._mcp.tool(self.query_capabilities)
        self._mcp.tool(self.query_handler_names)
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
        if not self._initialized:
            return
        self.logger.info("正在清理 MCP 服务...")
        if self._server_task:
            self._server_task.cancel()
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("MCP 服务器关闭超时,强制取消")
            except asyncio.CancelledError:
                pass
        self._initialized = False
        self.logger.info("MCP 服务已清理")

    # ============ MCP Tool 实现 ============

    async def send_action(
        self,
        action_name: Optional[str] = None,
        action_parameters: Optional[Dict[str, Any]] = None,
        emotion_name: Optional[str] = None,
        emotion_intensity: float = 0.5,
        text: Optional[str] = None,
    ) -> str:
        """向 Amaidesu 发送结构化动作和情绪。

        至少需要 `action_name` / `emotion_name` / `text` 之一。
        """
        if action_name:
            action_name = action_name.strip()
        if not any([action_name, emotion_name, text]):
            raise ValueError("至少需要提供 action_name、emotion_name 或 text 之一")

        payload: Dict[str, Any] = {
            "text": text,
            "action": {"name": action_name, "parameters": action_parameters or {}} if action_name else None,
            "emotion": {"name": emotion_name, "intensity": emotion_intensity} if emotion_name else None,
        }

        try:
            return await self._post_to_dashboard("/api/v1/maibot/action", payload, success_prefix="Action sent")
        except Exception as e:
            return f"send_action failed: {type(e).__name__}: {e}"

    async def query_capabilities(self) -> str:
        """查询 Output 阶段所有 handler 暴露的 action 列表(全限定名)。"""
        if self._output_handler_manager is None:
            return (
                '{"error": "OutputHandlerManager not injected into MCPServerService. '
                'query_capabilities 需 DI 注入 output_handler_manager,启动时检查。"}'
            )
        try:
            view = self._output_handler_manager.get_all_capabilities()
            return view.model_dump_json()
        except Exception as e:
            return f'{{"error": "{type(e).__name__}: {e}"}}'

    async def query_handler_names(self) -> str:
        """查询 Output 阶段所有已注册的 handler 名称。"""
        if self._output_handler_manager is None:
            return (
                '{"error": "OutputHandlerManager not injected into MCPServerService. '
                'query_handler_names 需 DI 注入 output_handler_manager,启动时检查。"}'
            )
        try:
            names = self._output_handler_manager.get_handler_names()
            import json

            return json.dumps({"handlers": names}, ensure_ascii=False)
        except Exception as e:
            return f'{{"error": "{type(e).__name__}: {e}"}}'

    async def get_status(self) -> str:
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
        if response.content:
            try:
                return response.json().get("error", response.text)
            except Exception as e:
                logger.debug(f"解析错误响应 JSON 失败,使用原始文本: {e}")
                return response.text
        return response.text
