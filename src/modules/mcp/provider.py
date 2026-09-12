"""McpToolProvider —— 把一个 MCP server 的工具暴露为 Amaidesu 统一工具

实现 ``ToolProvider`` Protocol：
- ``list_tools()``：同步返回缓存的 ToolSpec 列表（MCP list_tools 是 async，
  与 Provider 协议同步签名之间的阻抗通过**连接时预拉缓存**化解）
- ``invoke()``：按派生全名找到 spec → 用 spec.name（server 原始名）直呼
  server → 映射结果；永远不抛异常

## 命名模型
- spec.name 存 **server 原始名**；对外全名 = ``<provider>_<原始名>``
  （``ToolSpec.full_name`` 唯一派生实现，无映射表、无名字解析）
- ``provider`` 默认 = server 名（如 maicraft → "maicraft"）：注册后工具进入
  全局 ToolRegistry，任何 Agent（主播 / 游戏）通过 ``registry.invoke()``
  均可调用——与 Claude Code 的 MCP 插件语义一致："看到"即"可调"。
- 若内容层希望工具出现在指定 provider 过滤路径（如 ``list_tools(provider=
  "game")``），可在装配时显式传 ``provider="game"`` 覆盖（按需，非硬编码）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable, List, Optional

from src.modules.logging import get_logger
from src.modules.mcp import mapper
from src.modules.mcp.client import McpClient
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider

logger = get_logger("McpToolProvider")


class McpToolProvider(BaseToolProvider):
    """MCP server 工具 Provider（缓存 specs + invoke 转发）。

    Attributes:
        server_name: MCP server 别名（来自配置 key）
        provider: 提供者短名（默认 = server 名，如 "maicraft"；可显式覆盖）。
            与 ``name`` 属性同值，与全部 spec 的 ``provider`` 同值同源
        task_query_tool: 可选——server 侧任务查询工具的**全名**（声明
            适配器时由绑定处传入）；提供后 ``query_task`` 经该工具查快照
        task_status_map: 可选——server 原始状态 → 任务词表状态的映射
            （绑定处声明，server 特有知识不进本类）
        attention_uri: 可选——任务通知资源 URI（如 ``maicraft://attention``）；
            提供后 ``subscribe_task_notifications`` 订阅该资源
    """

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = "mcp"

    def __init__(
        self,
        *,
        client: McpClient,
        server_name: str,
        provider: Optional[str] = None,
        task_query_tool: Optional[str] = None,
        task_status_map: Optional[dict] = None,
        attention_uri: Optional[str] = None,
    ) -> None:
        self._client = client
        self.server_name = server_name
        self._provider = provider or server_name
        self._task_query_tool = task_query_tool
        self._task_status_map = dict(task_status_map) if task_status_map else None
        self._attention_uri = attention_uri
        self._specs: List[ToolSpec] = []
        self._synced = False

    @property
    def name(self) -> str:
        """提供者短名（与 spec.provider 同值同源；日志/重连键用）。"""
        return self._provider

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
        self._specs = [mapper.to_spec(t, provider=self._provider) for t in tools]
        self._synced = True
        logger.info(
            f"MCP Provider '{self.server_name}' 工具缓存就绪（{len(self._specs)} 个，provider={self._provider}）"
        )
        return len(self._specs)

    def list_tools(self) -> Iterable[ToolSpec]:
        """同步返回缓存的 ToolSpec（连接时预拉；未 setup 时为空）。"""
        return list(self._specs)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """执行 MCP 工具：按派生全名对照 spec → 原始名直呼 server。永远不抛异常。"""
        started_ms = int(time.time() * 1000)
        full_name = invocation.tool_name

        # 前置检查：工具是否属于本 Provider（不知道的工具 → 失败结果，不抛）
        spec = next((s for s in self._specs if s.full_name == full_name), None)
        if spec is None:
            return ToolExecutionResult(
                tool_name=full_name,
                success=False,
                error_message=f"工具 '{full_name}' 不属于 MCP Provider '{self.server_name}'",
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        # fastmcp 为可选重型依赖：业务异常类在调用点延迟获取（与 client 延迟导入同策略）
        from fastmcp.exceptions import ToolError

        try:
            # spec.name = server 原始名，原样直呼（无任何名字解析）
            result = await self._client.call_tool(spec.name, dict(invocation.arguments or {}))
        except ToolError as exc:
            # server 业务错误透传给调用方（LLM 据此自纠）；连接由 client 层保持，不断开
            duration_ms = int(time.time() * 1000) - started_ms
            return ToolExecutionResult(
                tool_name=full_name,
                success=False,
                error_message=f"MCP 业务错误: {exc}",
                duration_ms=duration_ms,
            )
        duration_ms = int(time.time() * 1000) - started_ms
        return mapper.to_result(result, tool_name=full_name, duration_ms=duration_ms)

    async def close(self) -> None:
        """关闭底层连接（幂等）。"""
        await self._client.close()

    # ----- 任务适配器（可选；绑定处声明，见构造参数） -----

    async def query_task(self, task_id: str) -> Optional[dict]:
        """查询适配器：经任务查询工具（``task_query_tool`` 全名）查快照。

        server 原始状态经 ``task_status_map`` 映射为任务词表状态；未声明
        查询工具或查询失败返回 ``None``（跟踪循环按无新事实处理）。
        """
        if not self._task_query_tool:
            return None
        spec = next((s for s in self._specs if s.full_name == self._task_query_tool), None)
        if spec is None:
            return None
        try:
            result = await self._client.call_tool(spec.name, {"action": "get", "task_id": task_id})
        except Exception as exc:  # noqa: BLE001 - 查询异常按无新事实上抛给调用方记日志
            raise RuntimeError(f"MCP 任务查询失败（{spec.name}）: {type(exc).__name__}: {exc}") from exc
        from src.modules.mcp.mapper import to_result

        exec_result = to_result(result, tool_name=self._task_query_tool)
        if not exec_result.success:
            return None  # server 业务错误（如任务不存在）→ 无新事实
        structured = exec_result.structured_content if isinstance(exec_result.structured_content, dict) else {}
        raw_status = str(structured.get("state") or structured.get("status") or "")
        mapped = (self._task_status_map or {}).get(raw_status, raw_status)
        snapshot = dict(structured)
        return {"status": mapped, "snapshot": snapshot, "summary": f"{raw_status} -> {mapped}" if raw_status else ""}

    def subscribe_task_notifications(self, callback) -> Optional[Any]:
        """通知适配器：订阅 ``attention_uri`` 资源（举旗级；返回退订句柄）。"""
        if not self._attention_uri:
            return None

        def _on_notify(uri: str) -> None:
            callback("")  # 资源通知不知道具体任务号——空串举旗，核实交给跟踪循环

        pending: dict = {}

        async def _subscribe() -> None:
            pending["unsub"] = await self._client.subscribe_resource(self._attention_uri, _on_notify)  # type: ignore[arg-type]

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        loop.create_task(_subscribe())

        def _unsubscribe() -> None:
            unsub = pending.get("unsub")
            if unsub is None:
                pending["cancelled"] = True
                return
            try:
                result = unsub()
                if hasattr(result, "__await__"):
                    loop.create_task(result)
            except Exception as exc:  # noqa: BLE001 - 退订失败不阻断
                logger.warning(f"attention 资源退订异常（忽略）: {exc}")

        return _unsubscribe

    async def health_check(self) -> bool:
        """探活钩子：MCP server 粒度——同一 server 的全部工具共用一条连接，
        同生共死（探活通过即整组恢复）。委托给 ``McpClient.probe``。"""
        return await self._client.probe()

    async def connect(self) -> bool:
        """建立 MCP 通道连接（手动重连的"建立"半步）。

        已连接短路返回 True（手动重连场景下"双开"无意义，避免拉起第二条
        transport 抢占 server 资源）。未连接时委托 ``McpClient.connect``——
        底层已对断连 / 超时 / transport 异常做了兜底，本层不另捕异常，仅
        记录失败原因供上层日志对照。返回 bool 与客户端一致（True=已建立）。
        """
        if self._client.connected:
            logger.info(f"MCP Provider '{self.server_name}' 已连接，跳过手动 connect")
            return True
        ok = await self._client.connect()
        if ok:
            logger.info(f"MCP Provider '{self.server_name}' 手动连接成功")
        else:
            logger.warning(f"MCP Provider '{self.server_name}' 手动连接失败（详见 McpClient 日志）")
        return ok

    async def disconnect(self) -> bool:
        """断开 MCP 通道连接（手动重连的"断开"半步）。

        委托 ``McpClient.close``：底层幂等且异常兜底，本层不另捕。返回值保留
        为 True 以表达"断开动作已发出"语义——即使原本未连接，调用语义仍
        是"已断开状态"，上层无须据此判定重连成败（看 ``connect`` 返回值）。
        """
        logger.info(f"MCP Provider '{self.server_name}' 手动断开连接")
        await self._client.close()
        return True


__all__ = ["McpToolProvider"]
