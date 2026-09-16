"""
MaicraftAttentionCollector —— AI 玩家身体事件采集器

上游是 MaiCraft 的注意流资源（``maicraft://attention``）：AI 玩家挨打、死亡、
紧急反应这类身体侧事实由 Mod 发布在该流上。本采集器**常驻订阅**该资源，
按游标增量读取，把 ``important`` 事件转成 ``game.body.*`` 事件。

为什么是采集器而不是让 Agent 去轮询：
- 它是**持续流**（无目标、无起止、生命周期挂装配期）——按三问判据归采集器；
- 主播侧此前只能靠 LLM 主动调 ``perceive(view=attention)``，于是"主播随时知道
  游戏里发生了什么"并不成立，且每个决策窗重建、跨窗游标无处存放；
  把游标交给长驻采集器，跨窗问题自然消失。

连接自带一份（不共用游戏 Agent 的 MCP 连接）：采集器的生命周期独立于任何 Agent，
游戏 Agent 重建/停机不应带走这条身体事件流；Mod 侧支持多会话并行。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, Literal, Optional

from pydantic import Field

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.payloads.body import (
    BodyEventPayload,
    body_event_name,
    upstream_timestamp_ms,
)
from src.modules.logging import get_logger


class MaicraftAttentionCollector(BaseCollector):
    """AI 玩家身体事件采集器（常驻订阅 maicraft://attention）。"""

    name = "maicraft_attention"
    description = "常驻订阅 MaiCraft 注意流，把 AI 玩家身体事件转成 game.body.* 事件"

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
        attention_uri: str = Field(default="maicraft://attention", description="身体事件所在的 MCP 资源 URI")
        page_limit: int = Field(default=20, ge=1, le=100, description="单次增量读取的事件条数上限")
        retry_interval_ms: int = Field(default=5000, ge=200, description="连接/读取失败后的重试间隔（毫秒）")
        idle_poll_ms: int = Field(
            default=15000,
            ge=1000,
            description="没有通知时的兜底读取节拍（毫秒）：通知是提示、可丢，靠它兜底",
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
        self._unsubscribe: Optional[Any] = None
        # 通知举旗（资源通知只说明"有更新"，具体事实靠读取）
        self._signal = asyncio.Event()
        # 增量游标：跨通知、跨重连保留；换流（换世界/重启）时重置
        self._stream_id: Optional[str] = None
        self._cursor: int = 0
        self._primed: bool = False
        self._emitted_total: int = 0

    # ==================== 生命周期 ====================

    async def _on_start(self) -> None:
        """启动后台采集循环（连接与订阅在循环内按需重建，避免启动期硬依赖游戏在跑）。"""
        await self._start_collect_task()

    async def _on_stop(self) -> None:
        await self._stop_collect_task()
        await self._teardown()

    async def collect(self) -> AsyncIterator[Any]:
        """采集循环：确保就绪 → 等通知（带兜底节拍）→ 增量读取并转发。

        单轮异常不外抛：基类消费任务遇异常会终止循环，采集中断会静默丢事件流；
        这里逐轮兜底并把连接拆掉，下一轮重新连接（游戏可能后启动）。
        """
        retry_s = self.typed_config.retry_interval_ms / 1000
        idle_s = self.typed_config.idle_poll_ms / 1000
        while True:
            if not await self._ensure_ready():
                await asyncio.sleep(retry_s)
                continue
            try:
                await asyncio.wait_for(self._signal.wait(), timeout=idle_s)
            except asyncio.TimeoutError:
                pass
            self._signal.clear()
            try:
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败降级重连，不终止采集
                self.logger.warning(f"身体事件读取失败，将重连后重试: {exc}")
                await self._teardown()
            yield None

    # ==================== 连接与订阅 ====================

    async def _ensure_ready(self) -> bool:
        """确保连接、工具缓存与资源订阅就绪；未就绪返回 False（由循环稍后重试）。"""
        if self._client is not None:
            return True
        # 函数内 import：mcp 模块涉及 fastmcp 重型依赖，且仅在本采集器启用时使用
        from src.modules.mcp.client import McpClient
        from src.modules.mcp.provider import McpToolProvider
        from src.modules.mcp.config import McpServerConfig

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
            self.logger.warning(f"注意流连接失败（{self.typed_config.url}），稍后重试: {exc}")
            await self._close_client(client)
            return False
        if count == 0:
            self.logger.warning(f"注意流连接不可用或 Mod 未暴露工具（{self.typed_config.url}），稍后重试")
            await self._close_client(client)
            return False

        # 上游工具名与固定入参在装配处声明（server 特有知识不进通用 MCP 层）
        for spec in provider.list_tools():
            if spec.name.endswith("perceive"):
                provider.attention_read_tool = spec.full_name
                provider.attention_read_arguments = {"view": "attention"}
                break
        if not getattr(provider, "attention_read_tool", None):
            self.logger.warning("Mod 未暴露 perceive 工具，注意流无法读取")
            await self._close_client(client)
            return False

        try:
            self._unsubscribe = await client.subscribe_resource(self.typed_config.attention_uri, self._on_notify)
        except Exception as exc:  # noqa: BLE001 - 订阅失败退回兜底节拍，仍可增量读取
            self.logger.warning(f"订阅 '{self.typed_config.attention_uri}' 失败，退回兜底节拍读取: {exc}")
            self._unsubscribe = None

        self._client = client
        self._provider = provider
        self.logger.info(f"注意流已接入（{self.typed_config.url}，页面上限 {self.typed_config.page_limit}）")
        return True

    def _on_notify(self, uri: str) -> None:
        """资源更新通知（举旗级、可丢）：唤醒采集循环去增量读取。"""
        self._signal.set()

    async def _teardown(self) -> None:
        """拆掉连接与订阅：下一轮 collect 会重新建立（订阅登记随连接走）。"""
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            try:
                result = unsubscribe()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001 - 退订失败不影响重连
                self.logger.warning(f"退订注意流失败（忽略）: {exc}")
        client = self._client
        self._client = None
        self._provider = None
        if client is not None:
            await self._close_client(client)

    async def _close_client(self, client: Any) -> None:
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不阻断停机
            self.logger.warning(f"关闭注意流连接失败（忽略）: {exc}")

    # ==================== 增量读取与转发 ====================

    async def _drain(self) -> None:
        """按游标读一页并转发重要事件（首读与换流只对游标，不补发历史）。"""
        provider = self._provider
        if provider is None:
            return
        page = await provider.read_attention(
            stream_id=self._stream_id,
            after_cursor=self._cursor,
            limit=self.typed_config.page_limit,
        )
        if not isinstance(page, dict):
            # 读取失败/无结构：按"这一轮没读到"处理，游标不动（否则那一段事件永久丢失）
            self.logger.warning("注意流本轮未返回可读页（游标保持不变）")
            return

        resync = bool(page.get("resync_required") or page.get("history_lost") or page.get("stream_reset"))
        stream_id = page.get("stream_id")
        if isinstance(stream_id, str) and stream_id:
            self._stream_id = stream_id
        cursor = page.get("cursor")
        if isinstance(cursor, int):
            self._cursor = cursor

        events = page.get("events")
        events = [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []
        if resync or not self._primed:
            self._primed = True
            self.logger.info(
                f"注意流游标{'重新同步' if resync else '首次建立'}："
                f"stream={self._stream_id} cursor={self._cursor} 本页 {len(events)} 条不转发"
            )
            return

        forwarded = 0
        for event in events:
            if event.get("priority") != "important":
                continue  # 任务事件由任务通道承载；background 只是世界时间/天气
            await self._forward(event)
            forwarded += 1
        if forwarded:
            self.logger.info(f"身体事件转发 {forwarded} 条（cursor={self._cursor}）")

    async def _forward(self, event: Dict[str, Any]) -> None:
        """一条上游事件 → ``game.body.<类型>`` 事件。"""
        event_type = str(event.get("type") or "")
        payload = BodyEventPayload(
            game="minecraft",
            event_type=event_type,
            priority=str(event.get("priority") or "important"),
            message=str(event.get("message") or ""),
            facts=event.get("data") if isinstance(event.get("data"), dict) else {},
            cursor=int(event.get("cursor") or 0),
            stream_id=str(event.get("stream_id") or self._stream_id or ""),
            occurred_at_ms=upstream_timestamp_ms(event.get("timestamp")),
        )
        await self.emit_event(body_event_name(event_type), payload, source=self.name)
        self._emitted_total += 1


__all__ = ["MaicraftAttentionCollector"]
