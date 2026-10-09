"""
MaicraftAttentionCollector —— AI 玩家身体事件采集器

上游是 MaiCraft v1 的任务事件流（``events``）：生存需求插进来的临时任务（自卫、夜里封顶自保、
退离边沿）开始与结束、角色自己处理不了的需求、角色死亡，都写在这条流上。本采集器**常驻**带着游标长轮询，
把其中的身体遭遇转成 ``game.body.*`` 事件；目标运行的处境变化归游戏 Agent 的任务通道，不转。

代码归属：它读的是 Minecraft 的事件流，是**游戏相关**的外部世界适配器，
故按"游戏内容逻辑内聚 ``src/agents/<名>/``"放在 Minecraft Agent 包内；
但装配与起停走采集器框架（``config/collectors.toml`` + 工厂），
生命周期挂装配期而非本 Agent——这样"待机时也有身体事件流"成立，
且游戏 Agent 重建/停机不会带走这条流。

为什么是独立的流而不是让游戏 Agent 兼职转发：
- 它是**持续流**（无目标、无起止、生命周期挂装配期）——按三问判据归采集器；
- 游戏 Agent 只在**干活时**需要身体事实（用来调整任务），主播侧**一直**需要
  叙事素材；把后者绑在前者的忙碌状态与重建上，等于让主播的叙事随时断档。

连接与游标自带一份（不共用游戏 Agent 的 MCP 连接）：事件流按读者各自的游标读，
两边互不影响；Mod 侧支持多会话并行。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, Literal, Optional

from pydantic import Field

from src.agents.minecraft.attention_matrix import KIND_TO_EVENT, RESOLVED_KINDS, classify, summarize
from src.agents.minecraft.maicraft import EVENTS, events_page_of, reply_of
from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.body import BodyEventPayload
from src.modules.logging import get_logger
from src.modules.tools.models import ToolInvocation

#: 连接失败的退避上限（秒）。Mod 常年不开时按最短间隔硬重连纯属空转：
#: 退避到 60 秒一次，游戏后启动仍能在 1 分钟内接上。
_RETRY_MAX_S: float = 60.0

#: 一次长轮询空手返回得比这还快，说明服务端没有等：停这么久再读，避免空转。
_EMPTY_READ_FLOOR_S: float = 1.0


class MaicraftAttentionCollector(BaseCollector):
    """AI 玩家身体事件采集器（常驻长轮询 MaiCraft 的任务事件流）。"""

    name = "maicraft_attention"
    description = "常驻读 MaiCraft 的任务事件流，把身体先处理的急事、处理不了的需求与角色死亡转成 game.body.* 事件"

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
        """采集循环：确保连接 → 长轮询读一页事件 → 转发身体遭遇。

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
                self.logger.debug("事件流读取未完成，保留读取位置后重连", exc=exc)
                self._report_unavailable(f"读取失败（{type(exc).__name__}: {exc}）")
                await self._teardown()
                await asyncio.sleep(retry_s)
            yield None

    def _report_unavailable(self, reason: str) -> None:
        """记录"事件流不可用"：同因只 warning 一次，重复降 debug；恢复时 ``_ensure_ready`` 复位特征串。"""
        if reason == self._unavailable_reason:
            self.logger.debug(f"事件流仍不可用（同因）: {reason}")
            return
        self._unavailable_reason = reason
        self.logger.warning(
            f"事件流不可用（{reason}）：身体事件暂时采不到，将退避重试（最长 {_RETRY_MAX_S:.0f} 秒一次）"
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
            self._report_unavailable("Mod 未暴露 events 工具（不是 MaiCraft v1？），事件流无法读取")
            await self._close_client(client)
            return False

        self._client = client
        self._provider = provider
        self._unavailable_reason = ""  # 接上了：失败特征串复位
        self.logger.info(f"事件流已接入（{self.typed_config.url}）")
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
            self.logger.warning(f"关闭事件流连接失败（忽略）: {exc}")

    # ==================== 增量读取与转发 ====================

    async def _read(self, arguments: Dict[str, Any]) -> Any:
        """读一页事件；读失败或回复读不懂时抛出，让采集循环重连（游标不动，那一段事件不丢）。"""
        provider = self._provider
        if provider is None:
            raise RuntimeError("事件流连接已不可用")
        reply = reply_of(await provider.invoke(ToolInvocation(tool_name=EVENTS, arguments=arguments, source=self.name)))
        if not reply.ok:
            raise RuntimeError(f"events 读取失败：{reply.error_code} {reply.error_message}")
        page = events_page_of(reply.data)
        if page is None:
            raise ValueError(f"events 的回复读不懂：{reply.raw}")
        return page

    async def _drain(self) -> None:
        """带着游标读一页并转发身体遭遇；首次读取与事件流更换只建立游标，接手前的事件不补发。"""
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
                f"事件流游标{'首次建立' if first else '重新同步'}：stream={self._stream_id} cursor={self._cursor}，之前的事件不转发"
            )
            return

        forwarded = 0
        for event in page.events:
            if await self._forward(event):
                forwarded += 1
        self._cursor = page.cursor
        if forwarded:
            self.logger.info(f"身体事件转发 {forwarded} 条（cursor={self._cursor}）")
        if not page.events and asyncio.get_running_loop().time() - started < _EMPTY_READ_FLOOR_S:
            # 服务端没按要求等就空手返回（旧版本或异常）：停一下再读，不让常驻循环空转。
            await asyncio.sleep(_EMPTY_READ_FLOOR_S)

    async def _forward(self, event: Dict[str, Any]) -> bool:
        """一条上游事件 → 一条叙事化 ``game.body.*`` 事件；目标运行的处境变化返回 False。"""
        source_type = str(event.get("kind") or "")
        kind = classify(source_type)
        if kind is None:
            return False
        payload = BodyEventPayload(
            game="minecraft",
            kind=kind,
            summary=summarize(kind, source_type, str(event.get("message") or "")),
            source_event_type=source_type,
            resolved=kind in RESOLVED_KINDS,
        )
        await self.emit_event(KIND_TO_EVENT[kind], payload, source=self.name)
        # 与日志同处落一条：事件面给程序看，日志给人复盘看。
        self.logger.info(f"[身体事件] {payload.summary}（{payload.source_event_type}）")
        self._emitted_total += 1
        return True


__all__ = ["MaicraftAttentionCollector"]
