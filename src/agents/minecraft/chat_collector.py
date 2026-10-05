"""
MaicraftChatCollector —— 游戏内聊天采集器

上游是 MaiCraft 的聊天流资源（``maicraft://chatflow``）：游戏聊天区收到的玩家
聊天与服务器系统消息由 Mod 发布在这条流上。本采集器**常驻订阅**该资源，按游标
找出新消息，滤掉 AI 玩家自己发出、被服务器回显的话（Mod 标 ``from_self``），
把别人说的话转成通用的 ``game.chat.received`` 事件，交给主播 Agent。

代码归属与 ``maicraft_attention`` 相同：读的是 Minecraft 的聊天流，属于游戏相关的
外部世界适配器，放在 Minecraft Agent 包内；装配与起停走采集器框架，生命周期挂
装配期而非本 Agent——游戏 Agent 空闲、重建或停机时，别人在游戏里说的话照样能被
主播听到。连接自带一份，Mod 侧支持多会话并行。

为什么读资源而不是工具：聊天流只开放资源读取，每次返回最近 50 条及各自游标；
采集器记住读到的游标与流编号，只转出之后的新消息。首次接入只对齐游标、不补发
历史；Mod 换世界换了流编号后，新流里的消息都是新的，照常转出。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, Literal, Optional

from pydantic import Field

from src.agents.minecraft.attention_matrix import upstream_timestamp_ms
from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.logging import get_logger

#: 连接失败的退避上限（秒）：Mod 没开是常态，退避到 60 秒一次，游戏后启动也能在 1 分钟内接上
_RETRY_MAX_S: float = 60.0


class MaicraftChatCollector(BaseCollector):
    """游戏内聊天采集器（常驻订阅 maicraft://chatflow）。"""

    name = "maicraft_chat"
    description = "常驻订阅 MaiCraft 聊天流，把游戏里别人说的话转成 game.chat.received 事件"

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
        chatflow_uri: str = Field(default="maicraft://chatflow", description="游戏聊天所在的 MCP 资源 URI")
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
        self._unsubscribe: Optional[Any] = None
        # 通知举旗（资源通知只说明"聊天流有变化"，正文靠读取）
        self._signal = asyncio.Event()
        # 已读到的流编号与游标：跨通知、跨重连保留；Mod 换世界会换流编号
        self._stream_id: Optional[str] = None
        self._cursor: int = 0
        self._primed: bool = False
        # 不可用原因特征串：Mod 长期不在时同因失败只报一次，避免常驻刷屏
        self._unavailable_reason: str = ""

    # ==================== 生命周期 ====================

    async def _on_start(self) -> None:
        """启动后台采集循环（连接与订阅在循环内按需建立，不要求启动时游戏已在跑）。"""
        await self._start_collect_task()

    async def _on_stop(self) -> None:
        await self._stop_collect_task()
        await self._teardown()

    async def collect(self) -> AsyncIterator[Any]:
        """采集循环：确保连接 → 等通知（带兜底节拍）→ 读最新一页并转出新消息。

        单轮异常不外抛（基类消费任务遇异常会终止循环，聊天就再也听不到了）：
        拆掉连接，下一轮重新连接；连接失败按 5 → 10 → 20 → 40 → 60 秒退避，连上即复位。
        """
        base_s = self.typed_config.retry_interval_ms / 1000
        idle_s = self.typed_config.idle_poll_ms / 1000
        retry_s = base_s
        while True:
            if not await self._ensure_ready():
                await asyncio.sleep(retry_s)
                retry_s = min(retry_s * 2, _RETRY_MAX_S)
                continue
            retry_s = base_s
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
                self._report_unavailable(f"读取失败（{type(exc).__name__}: {exc}）")
                await self._teardown()
                await asyncio.sleep(retry_s)
            yield None

    def _report_unavailable(self, reason: str) -> None:
        """记录"聊天流不可用"：同因只 warning 一次，原因变了或恢复后再失败才重新提醒。"""
        if reason == self._unavailable_reason:
            self.logger.debug(f"聊天流仍不可用（同因）: {reason}")
            return
        self._unavailable_reason = reason
        self.logger.warning(
            f"聊天流不可用（{reason}）：游戏里的聊天暂时听不到，将退避重试（最长 {_RETRY_MAX_S:.0f} 秒一次）"
        )

    # ==================== 连接与订阅 ====================

    async def _ensure_ready(self) -> bool:
        """确保连接与资源订阅就绪；未就绪返回 False（由循环稍后重试）。"""
        if self._client is not None:
            return True
        # 函数内 import：mcp 模块涉及 fastmcp 重型依赖，且仅在本采集器启用时使用
        from src.modules.mcp.client import McpClient
        from src.modules.mcp.config import McpServerConfig

        server = McpServerConfig(
            enabled=True,
            transport=self.typed_config.transport,
            url=self.typed_config.url,
            reconnect=self.typed_config.reconnect,
            timeout_seconds=self.typed_config.timeout_seconds,
        )
        client = McpClient(name="maicraft", config=server)
        try:
            connected = await client.connect()
            reason = "" if connected else "连接失败（Mod 的 MCP 服务未就绪）"
        except Exception as exc:  # noqa: BLE001 - 连接失败只降级重试
            connected, reason = False, f"连接失败（{type(exc).__name__}: {exc}）"
        if not connected:
            self._report_unavailable(reason)
            await self._close_client(client)
            return False

        try:
            self._unsubscribe = await client.subscribe_resource(self.typed_config.chatflow_uri, self._on_notify)
        except Exception as exc:  # noqa: BLE001 - 订阅失败退回兜底节拍，仍可读取
            self.logger.warning(f"订阅 '{self.typed_config.chatflow_uri}' 失败，退回兜底节拍读取: {exc}")
            self._unsubscribe = None

        self._client = client
        self._unavailable_reason = ""  # 接上了：失败特征串复位
        # 接上后先读一次：首次接入只对齐游标，之后的通知才有可比较的起点
        self._signal.set()
        self.logger.info(f"聊天流已接入（{self.typed_config.url}）")
        return True

    def _on_notify(self, uri: str) -> None:
        """资源更新通知（举旗级、可丢）：唤醒采集循环去读。"""
        self._signal.set()

    async def _teardown(self) -> None:
        """拆掉连接与订阅：下一轮 collect 会重新建立；已读游标保留，重连后接着比较。"""
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if unsubscribe is not None:
            try:
                result = unsubscribe()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001 - 退订失败不影响重连
                self.logger.warning(f"退订聊天流失败（忽略）: {exc}")
        client = self._client
        self._client = None
        if client is not None:
            await self._close_client(client)

    async def _close_client(self, client: Any) -> None:
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不阻断停机
            self.logger.warning(f"关闭聊天流连接失败（忽略）: {exc}")

    # ==================== 读取与转出 ====================

    async def _drain(self) -> None:
        """读最新一页，按流编号与游标找出新消息并转出（首次接入只对齐游标）。"""
        page = await self._read_page()
        stream_id = page.get("stream_id")
        messages = page.get("messages")
        # 拿到完整一页才推进游标；页面残缺不能被当作"没有新消息"而永久跳过
        if not isinstance(stream_id, str) or not isinstance(messages, list):
            raise ValueError("聊天流未返回完整页面，游标保持不变")
        if any(not isinstance(entry, dict) or not isinstance(entry.get("cursor"), int) for entry in messages):
            raise ValueError("聊天流条目缺少游标，游标保持不变")
        latest = page.get("latest_cursor")
        latest = latest if isinstance(latest, int) else max((entry["cursor"] for entry in messages), default=0)

        if not self._primed:
            # 首次接入：之前的聊天是历史，不当成刚有人说话
            self._primed = True
            self._stream_id, self._cursor = stream_id, latest
            self.logger.info(f"聊天流游标首次建立：stream={stream_id} cursor={latest}，已有 {len(messages)} 条不转出")
            return
        if stream_id != self._stream_id:
            # Mod 换世界换了流编号：新流里的消息都发生在上次读取之后，从头比较
            self.logger.info(f"聊天流已换新（{self._stream_id} → {stream_id}），新流中的消息照常转出")
            self._stream_id, self._cursor = stream_id, 0

        fresh = [entry for entry in messages if entry["cursor"] > self._cursor]
        if fresh and fresh[0]["cursor"] > self._cursor + 1:
            # 资源只给最近 50 条：两次读取之间说得太多时，中间那段已读不到，照实记下
            self.logger.warning(
                f"聊天流两次读取间有 {fresh[0]['cursor'] - self._cursor - 1} 条消息已超出最近一页，未能转出"
            )
        forwarded = 0
        for entry in fresh:
            if await self._forward(entry):
                forwarded += 1
        self._cursor = max(latest, self._cursor)
        if forwarded:
            self.logger.debug(f"游戏聊天转出 {forwarded} 条（cursor={self._cursor}）")

    async def _read_page(self) -> Dict[str, Any]:
        """读一次聊天流资源，解析成一页 JSON；读不到或格式不对就抛错，由循环重连。"""
        client = self._client
        if client is None:
            raise ValueError("聊天流连接不可用")
        raw = await client.read_resource(self.typed_config.chatflow_uri)
        if raw is None:
            raise ValueError("聊天流资源未返回内容")
        contents = raw.get("contents", []) if isinstance(raw, dict) else getattr(raw, "contents", raw)
        if not isinstance(contents, (list, tuple)) or len(contents) != 1:
            raise ValueError("聊天流资源未返回单份页面")
        first = contents[0]
        text = first.get("text") if isinstance(first, dict) else getattr(first, "text", None)
        if not isinstance(text, str):
            raise ValueError("聊天流资源页面不是文本")
        page = json.loads(text)
        if not isinstance(page, dict):
            raise ValueError("聊天流资源页面不是 JSON 对象")
        return page

    async def _forward(self, entry: Dict[str, Any]) -> bool:
        """一条聊天流消息 → 一条 ``game.chat.received``；AI 玩家自己的回显不转出。"""
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        if data.get("from_self") is True:
            return False
        content = data.get("message")
        if not isinstance(content, str) or not content.strip():
            return False
        system = data.get("system") is True
        suppressed = data.get("suppressed_similar_or_rate_limited_messages")
        payload = GameChatPayload(
            game="minecraft",
            kind="system" if system else "player",
            sender="" if system else str(data.get("sender_name") or ""),
            sender_id="" if system else str(data.get("sender_id") or ""),
            content=content,
            truncated=data.get("message_truncated") is True,
            suppressed=suppressed if isinstance(suppressed, int) and suppressed > 0 else 0,
            occurred_at_ms=upstream_timestamp_ms(entry.get("timestamp")),
        )
        await self.emit_event(CoreEvents.GAME_CHAT_RECEIVED, payload, source=self.name)
        # 与日志同处落一条，复盘时在时间轴上能看到游戏里谁说了什么
        speaker = "系统" if system else (payload.sender or "未知玩家")
        self.logger.info(f"[游戏聊天] {speaker}: {content}")
        return True


__all__ = ["MaicraftChatCollector"]
