"""
MaicraftEventsCollector —— 常驻长轮询 MaiCraft v1 一条事件流的采集器基类

MaiCraft v1 的 ``events`` 按 ``topic`` 分两条流：``tasks`` 是任务事件（身体事件从这里挑），``chat`` 是聊天栏
收到的消息。两种采集器做的事一样：自带一份连接、带游标长轮询、首次读取与事件流更换只建立游标（接手前的事件
不补发）、读失败游标不动并重连、服务端没按要求等就空手返回时停一下；不一样的只有读哪条流、一条事件怎么转出去，
由子类给出 ``topic`` 与 ``_forward``。

代码归属：读的是 Minecraft 的事件流，是**游戏相关**的外部世界适配器，故放在 Minecraft Agent 包内；
装配与起停走采集器框架（``config/collectors.toml`` + 工厂），生命周期挂装配期而非游戏 Agent——
游戏 Agent 空闲、重建或停机时，这些流照样有人读。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, ClassVar, Dict, Literal, Optional

from pydantic import Field

from src.agents.minecraft.maicraft import EVENTS, events_page_of, reply_of
from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.tools.models import ToolInvocation

#: 连接失败的退避上限（秒）。Mod 常年不开时按最短间隔硬重连纯属空转：
#: 退避到 60 秒一次，游戏后启动仍能在 1 分钟内接上。
_RETRY_MAX_S: float = 60.0

#: 一次长轮询空手返回得比这还快，说明服务端没有等：停这么久再读，避免空转。
_EMPTY_READ_FLOOR_S: float = 1.0

#: 任务事件流是 events 的默认 topic：读它时不带 topic，较早的 v1 也认。
_DEFAULT_TOPIC = "tasks"


class MaicraftEventsCollector(BaseCollector):
    """常驻长轮询 MaiCraft 的一条事件流；子类给出读哪条流（``topic``）与一条事件怎么转出（``_forward``）。"""

    #: 读 events 的哪条流
    topic: ClassVar[str] = _DEFAULT_TOPIC
    #: 日志里怎么称呼这条流
    stream_label: ClassVar[str] = "事件流"

    class ConfigSchema(BaseConfig):
        """采集器配置（包内权威 Schema）。

        连接参数与 ``[agents.minecraft.mcp]`` 指向同一个 Mod 服务，但本采集器
        自带独立连接；两处地址需要保持一致。
        """

        url: str = Field(
            default="http://127.0.0.1:8766/mcp",
            description="Mod 的 MCP 端点（与 agents.minecraft.mcp.url 保持一致）",
        )
        transport: Literal["http", "stdio"] = Field(default="http", description="传输方式")
        timeout_seconds: float = Field(default=30.0, ge=1.0, description="连接超时（秒）")
        reconnect: bool = Field(default=True, description="连接中断后自动重连")
        retry_interval_ms: int = Field(default=5000, ge=200, description="连接/读取失败后的重试间隔（毫秒）")
        wait_ms: int = Field(
            default=25_000,
            ge=1000,
            le=55_000,
            description="一次读事件流最多等多久（毫秒；没有新事件就空手返回再读，MaiCraft 上限 60 秒）",
        )

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self.config = config or {}
        self.logger = get_logger(self.__class__.__name__)
        self.typed_config = self.ConfigSchema.from_dict(self.config)

        self._client: Optional[Any] = None
        self._provider: Optional[Any] = None
        # 增量游标：跨读取、跨重连保留；事件流换了（换世界、Mod 重启）时重新建立
        self._stream_id: Optional[str] = None
        self._cursor: int = 0
        self._emitted_total: int = 0
        # 不可用原因特征串：Mod 长期不在时同因失败只报一次，避免常驻刷屏
        self._unavailable_reason: str = ""

    # ==================== 生命周期 ====================

    async def _on_start(self) -> None:
        """启动后台采集循环（连接在循环内按需建立，避免启动期硬依赖游戏在跑）。"""
        await self._start_collect_task()

    async def _on_stop(self) -> None:
        await self._stop_collect_task()
        await self._teardown()

    async def collect(self) -> AsyncIterator[Any]:
        """采集循环：确保连接 → 长轮询读一页事件 → 转发。

        单轮异常不外抛：基类消费任务遇异常会终止循环，采集中断会静默丢事件流；
        这里逐轮兜底并把连接拆掉，下一轮重新连接（游戏可能后启动）。
        连接失败按指数退避重试（5 → 10 → 20 → 40 → 60 秒封顶），连上一次即复位。
        """
        base_s = self.typed_config.retry_interval_ms / 1000
        retry_s = base_s
        while True:
            if not await self._ensure_ready():
                await asyncio.sleep(retry_s)
                retry_s = min(retry_s * 2, _RETRY_MAX_S)
                continue
            retry_s = base_s
            try:
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败降级重连，不终止采集
                self.logger.debug(f"{self.stream_label}读取未完成，保留读取位置后重连", exc=exc)
                self._report_unavailable(f"读取失败（{type(exc).__name__}: {exc}）")
                await self._teardown()
                await asyncio.sleep(retry_s)
            yield None

    def _report_unavailable(self, reason: str) -> None:
        """记录"流不可用"：同因只 warning 一次，重复降 debug；恢复时 ``_ensure_ready`` 复位特征串。"""
        if reason == self._unavailable_reason:
            self.logger.debug(f"{self.stream_label}仍不可用（同因）: {reason}")
            return
        self._unavailable_reason = reason
        self.logger.warning(
            f"{self.stream_label}不可用（{reason}）：暂时读不到，将退避重试（最长 {_RETRY_MAX_S:.0f} 秒一次）"
        )

    # ==================== 连接 ====================

    async def _ensure_ready(self) -> bool:
        """确保连接与工具清单就绪；未就绪返回 False（由循环稍后重试）。"""
        if self._client is not None:
            return True
        # 函数内 import：mcp 模块涉及 fastmcp 重型依赖，且仅在本采集器启用时使用
        from src.modules.mcp.client import McpClient
        from src.modules.mcp.config import McpServerConfig
        from src.modules.mcp.provider import McpToolProvider

        server = McpServerConfig(
            enabled=True,
            transport=self.typed_config.transport,
            url=self.typed_config.url,
            reconnect=self.typed_config.reconnect,
            timeout_seconds=self.typed_config.timeout_seconds,
        )
        client = McpClient(name="maicraft", config=server)
        provider = McpToolProvider(client=client, server_name="maicraft", provider="maicraft")
        try:
            count = await provider.setup()
        except Exception as exc:  # noqa: BLE001 - 连接失败只降级重试
            self._report_unavailable(f"连接失败（{type(exc).__name__}: {exc}）")
            await self._close_client(client)
            return False
        if count == 0:
            self._report_unavailable("连接成功但 Mod 未暴露任何工具")
            await self._close_client(client)
            return False
        if not any(spec.full_name == EVENTS for spec in provider.list_tools()):
            self._report_unavailable("Mod 未暴露 events 工具（不是 MaiCraft v1？），读不了事件流")
            await self._close_client(client)
            return False

        self._client = client
        self._provider = provider
        self._unavailable_reason = ""  # 接上了：失败特征串复位
        self.logger.info(f"{self.stream_label}已接入（{self.typed_config.url}）")
        return True

    async def _teardown(self) -> None:
        """拆掉连接：下一轮 collect 会重新建立；游标保留，流没变就接着读。"""
        client = self._client
        self._client = None
        self._provider = None
        if client is not None:
            await self._close_client(client)

    async def _close_client(self, client: Any) -> None:
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不阻断停机
            self.logger.warning(f"关闭{self.stream_label}连接失败（忽略）: {exc}")

    # ==================== 增量读取与转发 ====================

    async def _read(self, arguments: Dict[str, Any]) -> Any:
        """读一页事件；读失败或回复读不懂时抛出，让采集循环重连（游标不动，那一段事件不丢）。"""
        provider = self._provider
        if provider is None:
            raise RuntimeError(f"{self.stream_label}连接已不可用")
        if self.topic != _DEFAULT_TOPIC:
            arguments = {"topic": self.topic, **arguments}
        reply = reply_of(await provider.invoke(ToolInvocation(tool_name=EVENTS, arguments=arguments, source=self.name)))
        if not reply.ok:
            raise RuntimeError(f"events 读取失败：{reply.error_code} {reply.error_message}")
        page = events_page_of(reply.data)
        if page is None:
            raise ValueError(f"events 的回复读不懂：{reply.raw}")
        return page

    async def _drain(self) -> None:
        """带着游标读一页并逐条转发；首次读取与事件流更换只建立游标，接手前的事件不补发。"""
        first = self._stream_id is None
        arguments: Dict[str, Any] = {"wait_ms": 0 if first else self.typed_config.wait_ms}
        if not first:
            arguments["stream_id"] = self._stream_id
            arguments["after_cursor"] = self._cursor
        started = asyncio.get_running_loop().time()
        page = await self._read(arguments)
        if first or page.cursor_status != "valid" or page.stream_id != self._stream_id:
            # 不带游标读到的是流里最早保留的一页：一页页翻到最新再开始转发。
            self._stream_id = page.stream_id
            self._cursor = page.cursor
            while page.has_more:
                page = await self._read({"stream_id": self._stream_id, "after_cursor": self._cursor, "wait_ms": 0})
                self._cursor = page.cursor
            self.logger.info(
                f"{self.stream_label}游标{'首次建立' if first else '重新同步'}：stream={self._stream_id} "
                f"cursor={self._cursor}，之前的事件不转发"
            )
            return

        forwarded = 0
        for event in page.events:
            if await self._forward(event):
                forwarded += 1
                self._emitted_total += 1
        self._cursor = page.cursor
        if forwarded:
            self.logger.info(f"{self.stream_label}转发 {forwarded} 条（cursor={self._cursor}）")
        if not page.events and asyncio.get_running_loop().time() - started < _EMPTY_READ_FLOOR_S:
            # 服务端没按要求等就空手返回（旧版本或异常）：停一下再读，不让常驻循环空转。
            await asyncio.sleep(_EMPTY_READ_FLOOR_S)

    async def _forward(self, event: Dict[str, Any]) -> bool:
        """一条上游事件转成本系统的事件；不转发的返回 False。由子类实现。"""
        raise NotImplementedError


__all__ = ["MaicraftEventsCollector"]
