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
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.modules.logging import get_logger
from src.modules.mcp import mapper
from src.modules.mcp.requests import McpRequestTimeout
from src.modules.mcp.client import McpClient
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import MCP_CATEGORY, ConnectionToolProvider
from src.modules.utils.backoff import ExponentialBackoff

logger = get_logger("McpToolProvider")


class McpToolProvider(ConnectionToolProvider):
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
        attention_read_tool: 可选——server 侧注意流读取工具的**全名**；
            提供后 ``read_attention`` 按游标增量读一页事件
        attention_read_arguments: 可选——读取该工具所需的固定入参
            （绑定处声明，本类只补游标与页大小）
        on_connection_changed: 连接态变化回调槽（继承 ``ConnectionToolProvider``）——
            工具清单（重）同步成功即 ``mark_connected`` 触发 ``(True)``，断开 /
            清单拉空触发 ``(False)``；初次装配 / 降级恢复 / 手动重连三条路径
            统一经它通知绑定处（重）绑定
        switch_config: 可选——提供者开关的配置地址
            （``{"file": "agents.toml", "key": "agents.minecraft.mcp.enabled"}``，
            构造后由绑定处赋值）；Agent 私有 MCP 的开关不在 tools.toml，
            工具页据此渲染开关并把写回路由到正确文件
    """

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category = MCP_CATEGORY

    # 生命周期自管理：setup 返回工具数且在装配期自调（工具清单拉取 + 降级
    # 登记收尾都经它），不走组合根的批量启停——否则同一连接被建立两次。
    manages_own_lifecycle = True

    def __init__(
        self,
        *,
        client: McpClient,
        server_name: str,
        provider: Optional[str] = None,
        task_query_tool: Optional[str] = None,
        task_status_map: Optional[dict] = None,
        attention_uri: Optional[str] = None,
        attention_read_tool: Optional[str] = None,
        attention_read_arguments: Optional[dict] = None,
        specs: Optional[List[ToolSpec]] = None,
    ) -> None:
        self._client = client
        self.server_name = server_name
        self._provider = provider or server_name
        # 适配器声明用**公开属性**：绑定处（Agent / 采集器）要等 setup() 拉到工具清单后
        # 才认得出工具全名，只能在构造之后赋值。曾把字段写成私有、绑定处赋公开名，
        # 两边不同名 → 适配器静默失效（查询恒 None、订阅恒不建立），且测试用替身
        # provider 时完全看不见；故此处与绑定处的名字必须一致。
        self.task_query_tool = task_query_tool
        self.task_status_map = dict(task_status_map) if task_status_map else None
        self.attention_uri = attention_uri
        self.attention_read_tool = attention_read_tool
        self.attention_read_arguments = dict(attention_read_arguments) if attention_read_arguments else None
        # 通知订阅是**多订阅方**通道（跟踪循环核实任务、Agent 观察身体事件各一份）：
        # 资源订阅只建一次，最后一个订阅方退订时才真正断开。
        self._notification_callbacks: List[Any] = []
        self._notification_unsubscribe: Optional[Any] = None
        # specs 缓存的构造注入点（缺省空列表，正常路径由 setup 拉取填充；
        # 预置缓存的调用方自行负责连接态，通常配合 mark_connected 使用）
        self._specs: List[ToolSpec] = list(specs) if specs else []
        # switch_config 与回调槽同一约定：绑定处要等 setup() 拉到清单（或决定
        # 降级重试）后才能赋值，只能在构造之后声明，故为公开属性
        self.switch_config: Optional[Dict[str, str]] = None
        # 降级恢复循环（start_recovery 启动；拉到工具即止，close 时取消）
        self._recover_task: Optional[asyncio.Task[None]] = None

    @property
    def name(self) -> str:
        """提供者短名（与 spec.provider 同值同源；日志/重连键用）。"""
        return self._provider

    async def setup(self) -> int:
        """连接并预拉工具列表 → 填充缓存 specs（装配时调用一次）。

        Returns:
            缓存的工具数量；连接失败时为 0（list_tools 返回空）。
            数量 > 0 时经 ``mark_connected`` 回调绑定处——适配器绑定与降级
            恢复收尾统一走这一个入口，初次装配与后台重试不分叉。
        """
        if not self._client.connected:
            ok = await self._client.connect()
            if not ok:
                # 原因已由 McpClient 记过（含同因去重）；此处只补"因此工具列表为空"这一后果
                logger.debug(f"MCP Provider '{self.server_name}' 连接失败，工具列表为空")
                self._specs = []
                self.last_error = self._client.last_connect_error or "连接失败"
                return 0
        self.last_error = ""
        count = await self._sync_specs()
        if count > 0:
            self.mark_connected()
        return count

    async def _sync_specs(self) -> int:
        """拉取 server 工具清单并刷新本地 specs 缓存。返回缓存数量。"""
        tools = await self._client.list_tools()
        self._specs = [mapper.to_spec(t, provider=self._provider) for t in tools]
        logger.info(
            f"MCP Provider '{self.server_name}' 工具缓存就绪（{len(self._specs)} 个，provider={self._provider}）"
        )
        return len(self._specs)

    def _connected_tools(self) -> Iterable[ToolSpec]:
        """连接建立（工具清单同步成功）后暴露的全量清单 = 本地 specs 缓存。"""
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
        except McpRequestTimeout as exc:
            # 没有回执不能宣称远端未执行；把不确定性完整交给调用者，通道不代替它重试动作。
            return ToolExecutionResult(
                tool_name=full_name,
                success=False,
                failure_kind="execution",
                error_message=str(exc),
                structured_content={
                    "error": {
                        "code": "mcp_request_timeout",
                        "message": str(exc),
                        "outcome_known": False,
                        "request_id": exc.request_id,
                        "timeout_ms": exc.timeout_ms,
                        "elapsed_ms": exc.elapsed_ms,
                    }
                },
                duration_ms=int(time.time() * 1000) - started_ms,
            )
        except ToolError as exc:
            # server 业务错误透传给调用方（LLM 据此自纠）；连接由 client 层保持，不断开
            duration_ms = int(time.time() * 1000) - started_ms
            return ToolExecutionResult(
                tool_name=full_name,
                success=False,
                error_message=f"MCP 业务错误: {exc}",
                # SDK 已确认服务返回业务拒绝，让调用者自纠而不触发服务熔断。
                failure_kind="business",
                duration_ms=duration_ms,
            )
        duration_ms = int(time.time() * 1000) - started_ms
        return mapper.to_result(result, tool_name=full_name, duration_ms=duration_ms)

    def start_recovery(self, *, base_delay_ms: int = 5000, cap_delay_ms: int = 60000) -> None:
        """降级登记后的后台恢复：按退避间隔重试 ``setup``，拉到工具即止（幂等）。

        恢复成功沿 ``setup`` 内的 ``mark_connected`` 回调绑定处补注册，
        与初次装配、手动重连走同一个出口；``close`` 时随连接一并取消。
        """
        if self._recover_task is not None and not self._recover_task.done():
            return
        backoff = ExponentialBackoff(base_delay_ms=base_delay_ms, cap_delay_ms=cap_delay_ms)
        self._recover_task = asyncio.create_task(self._recover_loop(backoff), name=f"mcp-recover:{self.server_name}")

    @property
    def recovering(self) -> bool:
        """降级恢复循环是否仍在重试。"""
        return self._recover_task is not None and not self._recover_task.done()

    async def _recover_loop(self, backoff: ExponentialBackoff) -> None:
        while True:
            await asyncio.sleep(backoff.next_delay_ms() / 1000)
            if self._specs:
                # 期间已被手动重连恢复（工具集已由那条路径刷新），本循环无事可做
                return
            try:
                count = await self.setup()
            except Exception as exc:  # noqa: BLE001 - 单轮失败安静重试，原因留给下一轮
                logger.debug(f"MCP Provider '{self.server_name}' 恢复重试异常: {type(exc).__name__}: {exc}")
                continue
            if count > 0:
                logger.info(f"MCP Provider '{self.server_name}' 降级恢复：{count} 个工具")
                return

    async def close(self) -> None:
        """取消降级恢复循环并关闭底层连接（幂等）。"""
        task = self._recover_task
        self._recover_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._client.close()

    # ----- 任务适配器（可选；绑定处声明，见构造参数） -----

    async def query_task(self, task_id: str) -> Optional[dict]:
        """查询适配器：经任务查询工具（``task_query_tool`` 全名）查快照。

        server 原始状态经 ``task_status_map`` 映射为任务词表状态；未声明
        查询工具或查询失败返回 ``None``（跟踪循环按无新事实处理）。
        """
        if not self.task_query_tool:
            return None
        spec = next((s for s in self._specs if s.full_name == self.task_query_tool), None)
        if spec is None:
            return None
        try:
            result = await self._client.call_tool(spec.name, {"action": "get", "task_id": task_id})
        except Exception as exc:  # noqa: BLE001 - 查询异常按无新事实上抛给调用方记日志
            raise RuntimeError(f"MCP 任务查询失败（{spec.name}）: {type(exc).__name__}: {exc}") from exc
        exec_result = mapper.to_result(result, tool_name=self.task_query_tool)
        if not exec_result.success:
            return None  # server 业务错误（如任务不存在）→ 无新事实
        structured = exec_result.structured_content if isinstance(exec_result.structured_content, dict) else {}
        raw_status = str(structured.get("state") or structured.get("status") or "")
        mapped = (self.task_status_map or {}).get(raw_status, raw_status)
        snapshot = dict(structured)
        return {"status": mapped, "snapshot": snapshot, "summary": f"{raw_status} -> {mapped}" if raw_status else ""}

    async def read_attention(
        self,
        *,
        stream_id: Optional[str] = None,
        after_cursor: int = 0,
        limit: int = 10,
    ) -> Optional[dict]:
        """身体事件适配器：经注意流读取工具（``attention_read_tool`` 全名）增量读一页。

        固定入参由绑定处声明（``attention_read_arguments``），本方法只补游标、
        页大小与"不等新事件"：调用方拿到的是**增量**，不是每次重读最新一页。

        Returns:
            读取包（含 ``stream_id`` / ``cursor`` / ``events`` 等键）；未声明读取
            工具、工具缺失或读取失败返回 ``None``——调用方据此按"这一轮没读到"
            处理，绝不能读成"没有事件"（等待期与失败期必须区分开）。
        """
        if not self.attention_read_tool:
            return None
        spec = next((s for s in self._specs if s.full_name == self.attention_read_tool), None)
        if spec is None:
            return None
        arguments = dict(self.attention_read_arguments or {})
        arguments["after_cursor"] = int(after_cursor)
        arguments["limit"] = int(limit)
        arguments["wait_ms"] = 0
        if stream_id:
            arguments["stream_id"] = stream_id
        try:
            result = await self._client.call_tool(spec.name, arguments)
        except Exception as exc:  # noqa: BLE001 - 读取异常按"没读到"上抛给调用方记日志
            raise RuntimeError(f"MCP 注意流读取失败（{spec.name}）: {type(exc).__name__}: {exc}") from exc
        exec_result = mapper.to_result(result, tool_name=self.attention_read_tool)
        if not exec_result.success:
            logger.warning(f"注意流读取返回业务错误（{spec.name}）: {exec_result.error_message}")
            return None
        structured = exec_result.structured_content
        return structured if isinstance(structured, dict) else None

    def subscribe_task_notifications(self, callback: Callable[[str], None]) -> Optional[Any]:
        """通知适配器：订阅 ``attention_uri`` 资源（举旗级；返回退订句柄）。

        多订阅方共用一条资源订阅：每个订阅方拿到自己的退订句柄，最后一个退订时
        才真正断开。通知本身不带内容（举旗级），拿事实的一方各自去读。
        """
        if not self.attention_uri:
            return None
        self._notification_callbacks.append(callback)
        self._ensure_notification_subscription()

        def _unsubscribe() -> None:
            try:
                self._notification_callbacks.remove(callback)
            except ValueError:
                return
            if self._notification_callbacks:
                return
            self._drop_notification_subscription()

        return _unsubscribe

    def _ensure_notification_subscription(self) -> None:
        """首次订阅方到达时建立资源订阅；已有订阅或无法取到事件循环则跳过。"""
        if self._notification_unsubscribe is not None:
            return

        def _on_notify(uri: str) -> None:
            # 资源通知不知道具体任务号——空串举旗，事实由各自读取获得。
            for callback in list(self._notification_callbacks):
                try:
                    callback("")
                except Exception as exc:  # noqa: BLE001 - 单个订阅方异常不影响其它订阅方
                    logger.warning(f"attention 通知回调异常（忽略）: {type(exc).__name__}: {exc}")

        pending: dict = {}

        async def _subscribe() -> None:
            pending["unsub"] = await self._client.subscribe_resource(self.attention_uri, _on_notify)  # type: ignore[arg-type]

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._notification_callbacks.pop()
            return
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

        self._notification_unsubscribe = _unsubscribe

    def _drop_notification_subscription(self) -> None:
        """最后一个订阅方离开后断开资源订阅。"""
        unsubscribe = self._notification_unsubscribe
        self._notification_unsubscribe = None
        if unsubscribe is None:
            return
        unsubscribe()

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
            self.last_error = ""
            return True
        ok = await self._client.connect()
        if ok:
            self.last_error = ""
            logger.info(f"MCP Provider '{self.server_name}' 手动连接成功")
        else:
            self.last_error = self._client.last_connect_error or "连接失败"
            logger.warning(f"MCP Provider '{self.server_name}' 手动连接失败（详见 McpClient 日志）")
        return ok

    async def disconnect(self) -> bool:
        """断开 MCP 通道连接（手动重连的"断开"半步）。

        委托 ``McpClient.close``：底层幂等且异常兜底，本层不另捕。断开即
        ``mark_disconnected``（清单视为失效，绑定处据此摘除工具），与
        ConnectionToolProvider 中间层的换血语义一致。返回值保留为 True
        以表达"断开动作已发出"语义——即使原本未连接，调用语义仍是"已断
        开状态"，上层无须据此判定重连成败（看 ``connect`` 返回值）。
        """
        logger.info(f"MCP Provider '{self.server_name}' 手动断开连接")
        await self._client.close()
        self.mark_disconnected()
        return True

    async def reconnect(self) -> bool:
        """手动重连：断开通道 → 重建连接 → 重拉工具清单（specs 缓存换血）。

        基类默认组合只管通道；MCP 的工具清单是**连接时预拉缓存**——降级
        登记（0 工具）与 server 侧清单变化都靠本方法重拉，重拉成功经
        ``mark_connected``（registry 侧 refresh 与绑定侧适配器重绑定都由
        接线回调驱动）；清单拉到但为空视为重连未成（server 在但未暴露工
        具），置断开态并返回 False 交上层按失败处理。
        """
        await self.disconnect()
        if not await self.connect():
            return False
        count = await self._sync_specs()
        if count == 0:
            self._specs = []
            self.mark_disconnected()
            self.last_error = "server 已连接但未暴露任何工具"
            logger.warning(f"MCP Provider '{self.server_name}' 重连成功但 server 未暴露工具")
            return False
        self.mark_connected()
        return True


__all__ = ["McpToolProvider"]
