"""MCP 客户端封装（基于 FastMCP）

职责：只做"通道"——按配置建立连接、拉取工具元数据、转发工具调用。
**不含任何具体 MCP server 的知识**（不硬编码工具名 / 能力名 / 资源名）。

- transport 选择：http（Streamable HTTP；MaiCraft 等远程服务）
  / stdio（子进程拉起，如 npx）
- 生命周期：``connect()`` 建立连接并预拉工具列表；``close()`` 关闭；
  ``call_tool()`` 转发调用并返回 mcp 原始结果（由 mapper 转换）
- 资源订阅（标准 MCP resources/subscribe）：``subscribe_resource(uri, callback)``
  登记"举旗"回调，server 推送资源更新通知时分发；活跃订阅集合持久于实例，
  重连成功后自动重发订阅请求（尽力而为）。通知只表达"资源变了"，内容
  核实由消费方负责（通知是提示，不是事实源）。

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
from typing import Any, Callable, Dict, List, Optional

from src.modules.logging import get_logger
from src.modules.mcp.config import McpServerConfig

logger = get_logger("McpClient")

# 资源更新通知回调（参数 = 资源 URI）。约定"举旗"级极简动作（set 事件/入队），
# 业务核实留在消费方自己的协程——本回调跑在 fastmcp 消息循环内。
ResourceUpdateCallback = Callable[[str], None]


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
        # 活跃资源订阅（uri → 举旗回调）：持久于实例，重连成功后据此重发订阅请求
        self._subscriptions: Dict[str, ResourceUpdateCallback] = {}

    @staticmethod
    def _build_message_handler(subscriptions: Dict[str, ResourceUpdateCallback]) -> Any:
        """构造 fastmcp MessageHandler 实例：资源更新通知按 URI 分发到注册表。

        以工厂函数封闭注册表引用（同一 dict 对象与实例共享，增删即时可见）；
        回调异常兜底吞掉——通知处理抛错会干扰 fastmcp 消息循环。
        """
        # 延迟 import：fastmcp 为可选重型依赖（与 Client 同策略）
        from fastmcp.client.client import MessageHandler

        class _ResourceUpdateHandler(MessageHandler):
            async def on_resource_updated(self, message: Any) -> None:
                uri = str(getattr(getattr(message, "params", None), "uri", ""))
                callback = subscriptions.get(uri)
                if callback is None:
                    return
                try:
                    callback(uri)
                except Exception as exc:  # noqa: BLE001 - 通知回调边界兜底
                    logger.warning(f"MCP 资源更新回调执行失败（uri={uri}）: {type(exc).__name__}: {exc}")

        return _ResourceUpdateHandler()

    async def _resubscribe_all(self) -> None:
        """重连后对活跃订阅集合逐个重发订阅请求（尽力而为，失败记日志）。

        失败的订阅保留在集合中——下次重连再试；断连窗口内的资源变化由
        消费方的周期兜底覆盖（通知本就是可丢的提示）。
        """
        if not self._subscriptions or self._client is None:
            return
        session = getattr(self._client, "session", None)
        if session is None:
            logger.warning(f"MCP server '{self.name}' session 不可用，重订阅跳过")
            return
        for uri in list(self._subscriptions):
            try:
                await session.subscribe_resource(uri)
                logger.info(f"MCP server '{self.name}' 重连后重发订阅 '{uri}'")
            except Exception as exc:  # noqa: BLE001 - 单订阅失败不阻断其余
                logger.warning(f"MCP server '{self.name}' 重连后重订阅 '{uri}' 失败: {type(exc).__name__}: {exc}")

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

            if self._client is not None:
                # 重连前先释放旧实例，避免上下文泄漏
                try:
                    await self._client.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001 - 旧实例释放失败不阻断重连
                    pass
            transport = self._build_transport()
            client = Client(transport, message_handler=self._build_message_handler(self._subscriptions))
            await client.__aenter__()
            self._client = client
            self._connected = True
            logger.info(f"MCP server '{self.name}' 已连接（transport={self.config.transport}）")
            await self._resubscribe_all()
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

    async def probe(self) -> bool:
        """主动探活：先看本地连接状态，未连接则尝试一次重连。

        Returns:
            当前连接是否可用（True=健康）

        语义：
        - 已连接 + client 实例存在 → 立即返回 True（不走网络，纯本地判据）
        - 否则记 info 日志并复用 ``self.connect()``（不会抛异常）做一次重连，
          返回其 bool 结果。复用 connect 即可保持"释放旧实例 / 失败兜底"等
          现有生命周期行为一致
        """
        if self._connected and self._client is not None:
            return True
        logger.info(f"MCP server '{self.name}' 探活：未连接，尝试重连")
        return await self.connect()

    async def subscribe_resource(self, uri: str, callback: ResourceUpdateCallback) -> Callable[[], Any]:
        """订阅资源更新通知（标准 MCP resources/subscribe），返回退订句柄。

        通知到达时按 URI 分发调用 ``callback(uri)``（举旗级，见模块 docstring）。
        订阅登记持久于实例：断连重连后自动重发订阅请求。

        Args:
            uri: 资源 URI（如 "maicraft://attention"）
            callback: 更新通知回调（参数 = uri 字符串）

        Returns:
            退订句柄（async callable）：移除本地登记 + 发标准退订请求；
            网络退订失败仅记日志（本地登记已除名，通知不再分发）。

        Raises:
            RuntimeError: 未连接或 fastmcp session 不可用——订阅是显式动作，
                失败让消费方感知（可降级为周期轮询兜底）。
        """
        if not self._connected or self._client is None:
            raise RuntimeError(f"MCP server '{self.name}' 未连接，无法订阅资源 '{uri}'")
        session = getattr(self._client, "session", None)
        if session is None:
            raise RuntimeError(f"MCP server '{self.name}' fastmcp session 不可用，无法订阅资源 '{uri}'")
        await session.subscribe_resource(uri)
        self._subscriptions[uri] = callback
        logger.info(f"MCP server '{self.name}' 已订阅资源 '{uri}'")

        async def unsubscribe() -> None:
            self._subscriptions.pop(uri, None)
            if self._connected and self._client is not None:
                current = getattr(self._client, "session", None)
                if current is not None:
                    try:
                        await current.unsubscribe_resource(uri)
                    except Exception as exc:  # noqa: BLE001 - 退订失败不影响本地除名
                        logger.warning(f"MCP server '{self.name}' 退订 '{uri}' 失败: {type(exc).__name__}: {exc}")
            logger.info(f"MCP server '{self.name}' 已退订资源 '{uri}'")

        return unsubscribe

    async def read_resource(self, uri: str) -> Any:
        """读取资源内容（直通 fastmcp，返回 ReadResourceResult）。

        Returns:
            fastmcp 原始结果；未连接/读取失败返回 None（由消费方兜底）
        """
        if not self._connected or self._client is None:
            logger.warning(f"MCP server '{self.name}' 未连接，read_resource 返回 None")
            return None
        try:
            return await self._client.read_resource(uri)
        except Exception as exc:  # noqa: BLE001 - 通道边界兜底
            logger.warning(f"MCP server '{self.name}' read_resource '{uri}' 失败: {type(exc).__name__}: {exc}")
            return None

    async def list_resources(self) -> List[Any]:
        """列出 server 暴露的资源元数据（直通 fastmcp）。

        Returns:
            mcp.types.Resource 列表；未连接/失败返回空列表
        """
        if not self._connected or self._client is None:
            logger.warning(f"MCP server '{self.name}' 未连接，list_resources 返回空")
            return []
        try:
            resources = await self._client.list_resources()
            return list(resources or [])
        except Exception as exc:  # noqa: BLE001 - 通道边界兜底
            logger.warning(f"MCP server '{self.name}' list_resources 失败: {type(exc).__name__}: {exc}")
            return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """调用 server 上的工具，返回 FastMCP CallToolResult（原始结果）。

        Args:
            tool_name: MCP 侧工具原名（Provider 从注册名→原名映射表查得）
            arguments: 工具参数 dict

        Returns:
            CallToolResult；未连接 / 传输层调用失败时返回 None（由 Provider 转换为错误结果）

        Raises:
            ToolError: server 正常应答的业务错误（参数错/状态冲突等）——
                round-trip 完整、连接无恙，不标记断连；由 Provider 转为失败结果
        """
        if not self._connected or self._client is None:
            # 长时间运行后连接可能静默断开：惰性重连一次，失败才放弃
            logger.info(f"MCP server '{self.name}' 未连接，尝试重连后调用 '{tool_name}'")
            if not await self.connect():
                return None
        # fastmcp 为可选重型依赖：业务异常类在调用点延迟获取（与 connect 的延迟导入同策略）
        from fastmcp.exceptions import ToolError

        try:
            started = time.time()
            result = await self._client.call_tool(tool_name, arguments)
            duration_ms = int((time.time() - started) * 1000)
            logger.debug(f"MCP 调用 {self.name}.{tool_name} 完成（{duration_ms}ms）")
            return result
        except ToolError as exc:
            # server 正常应答的业务错误（参数错/状态冲突等）：round-trip 完整，连接无恙——
            # 保持连接不断开，错误上抛由 Provider 转述给调用方（LLM 据此自纠）
            logger.warning(f"MCP server '{self.name}' 工具 '{tool_name}' 业务错误（连接保持）: {exc}")
            raise
        except Exception as exc:  # noqa: BLE001 - 通道边界兜底
            # 传输层故障（断线/服务重启）：标记断开，下次调用触发重连
            logger.warning(f"MCP server '{self.name}' 调用工具 '{tool_name}' 失败: {type(exc).__name__}: {exc}")
            self._connected = False
            self._client = None
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


__all__ = ["McpClient", "ResourceUpdateCallback"]
