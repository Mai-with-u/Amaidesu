"""
MockCollector —— 确定性 JSONL 回放器（v2.0.7+ / ADR-006）

按 ADR-006 收敛"模拟"语义到唯一承载者：
- **回放（replay）**：本采集器（JSONL 模式），确定性重放，服务回归测试与 bug 复现；
- **仿真（simulation）**："LLM 驱动的生成式虚拟直播间"，归属 ``src/modules/simulator/``
  的 ``SimulatorService``（组合根按 ``[simulator].enabled`` 装配，本采集器不再承担）。

事件：
- 默认 emit ``room.message.*`` 语义事件；
- 所有事件携带 ``simulated=True`` 数据溯源标记（§1.6 / ADR-006），
  消费方用此标记过滤（如 SQL ``WHERE simulated=0``）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from pydantic import Field

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import (
    RoomMessagePayload,
    RoomMessageUser,
)
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.base.normalized_message import NormalizedMessage


class MockCollector(BaseCollector):
    """
    确定性 JSONL 回放器（v2.0.7+ / ADR-006）

    从 JSONL 文件按速率回放采集到的直播间消息流（mock_danmaku 行为，零 LLM 依赖），
    emit ``room.message.*`` 语义事件，所有事件 ``simulated=True``。

    历史背景：Wave 5 合并迁移时本采集器同时承载了 LLM 驱动的 simulator 简化版，
    由 ADR-006 翻转——"模拟"语义收敛到 ``src.modules.simulator`` 一处，本采集器
    只保留 JSONL 回放单一职责。
    """

    name = "mock"
    description = "确定性 JSONL 回放器，所有事件 simulated=True（ADR-006）"

    class ConfigSchema(BaseConfig):
        """确定性 JSONL 回放器配置"""

        # JSONL 模式（唯一保留的模式）
        log_file_path: str = Field(default="msg_default.jsonl", description="JSONL 日志文件路径")
        send_interval: float = Field(default=1.0, description="发送间隔（秒）", ge=0.1)
        loop_playback: bool = Field(default=True, description="循环播放")
        emit_semantic_events: bool = Field(default=True, description="emit room.message.* 语义事件")

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(event_bus=event_bus)
        self.config = config or {}
        self.logger = get_logger(self.__class__.__name__)
        self.typed_config = self.ConfigSchema.from_dict(self.config)

        self._emit_semantic_events = self.typed_config.emit_semantic_events

        # 数据目录：src/modules/collectors/mock/data/
        self._data_dir = Path(__file__).resolve().parent / "data"

        # JSONL 模式状态
        self._message_lines: list[str] = []
        self._current_line_index: int = 0
        self._stop_event = asyncio.Event()
        self.is_started = False

    # ------------------------------------------------------------------
    # 旧 InputCollectorManager 兼容接口
    # ------------------------------------------------------------------

    def stream(self) -> AsyncIterator[NormalizedMessage]:
        if not self.is_started:
            raise RuntimeError("Collector 未启动，请先调用 start()")

        async def _generate():
            try:
                async for message in self.collect():
                    yield message
            finally:
                self.is_started = False

        return _generate()

    async def start(self) -> None:
        """启动：重置停止事件，开后台任务消费 collect()（内部 emit room.message.*）。"""
        if not self.is_started:
            self.is_started = True
            self._stop_event.clear()
            await self._start_collect_task()

    async def stop(self) -> None:
        """停止：取消后台消费任务。"""
        if self.is_started:
            self.is_started = False
            self._stop_event.set()
            await self._stop_collect_task()

    async def cleanup(self) -> None:
        """清理资源"""
        self._stop_event.set()
        self._message_lines = []
        self._current_line_index = 0
        self.logger.info("MockCollector 已清理")

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """启动 JSONL 回放循环"""
        self.is_started = True

        try:
            async for msg in self._collect_jsonl():
                yield msg
        finally:
            self.is_started = False

    async def _collect_jsonl(self) -> AsyncIterator[NormalizedMessage]:
        """JSONL 回放模式（verbatim 自原 mock_danmaku）"""
        await self._load_message_lines()

        if not self._message_lines:
            self.logger.warning("未从 JSONL 文件加载到任何消息。")
            return

        self.logger.info("模拟弹幕（JSONL）发送循环开始 (源: msg_default.jsonl)")

        while not self._stop_event.is_set():
            if not self._message_lines:
                self.logger.warning("消息列表为空，停止发送循环。")
                break

            if self._current_line_index >= len(self._message_lines):
                if self.typed_config.loop_playback:
                    self.logger.info("到达文件末尾，循环播放已启用，重置索引。")
                    self._current_line_index = 0
                else:
                    self.logger.info("到达文件末尾，循环播放已禁用，停止发送。")
                    break

            if self._current_line_index >= len(self._message_lines):
                self.logger.warning("索引仍然超出范围，停止循环。")
                break

            line = self._message_lines[self._current_line_index]
            self._current_line_index += 1

            try:
                data = json.loads(line)
                text = data.get("text", "")
                user = data.get("user_name", "未知用户")
                user_id = data.get("user_id", "")

                normalized_msg = NormalizedMessage(
                    text=text,
                    source=self.name,
                    data_type="text",
                    importance=0.5,
                    timestamp_ms=now_ms(),
                    user_id=user_id or None,
                    user_nickname=user or None,
                    platform="simulated",
                    simulated=True,  # 数据溯源标记
                )

                if self._emit_semantic_events:
                    await self._emit_danmaku(normalized_msg)

                self.logger.debug(f"发送模拟消息 (行 {self._current_line_index}): {str(data)[:50]}...")
                yield normalized_msg

                await asyncio.sleep(self.typed_config.send_interval)

            except asyncio.CancelledError:
                break
            except json.JSONDecodeError as e:
                self.logger.error(f"JSON 解析错误: {e}. 行内容: {line[:100]}...")
            except Exception as e:
                self.logger.error(f"发送模拟消息时发生错误: {e}", exc_info=True)

        self.logger.info("模拟弹幕发送循环已结束。")

    async def _load_message_lines(self) -> None:
        """加载 JSONL 文件"""
        self._message_lines = []
        self._current_line_index = 0
        log_path = self._data_dir / self.typed_config.log_file_path
        if not log_path.exists() or not log_path.is_file():
            self.logger.error(f"日志文件未找到或不是文件: {log_path}")
            return
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                self._message_lines = [line.strip() for line in f if line.strip()]
            self.logger.info(f"成功从 '{log_path.name}' 加载 {len(self._message_lines)} 行消息。")
        except Exception as e:
            self.logger.error(f"读取日志文件时出错: {log_path}: {e}", exc_info=True)
            self._message_lines = []

    async def _emit_danmaku(self, msg: NormalizedMessage) -> None:
        """emit room.message.danmaku（JSONL 模式默认语义）"""
        try:
            payload = RoomMessagePayload(
                live_session_id=str(msg.room_id or "simulated_default"),
                message_type="danmaku",
                user=RoomMessageUser(
                    id=str(msg.user_id or "sim_unknown"),
                    name=str(msg.user_nickname or "模拟观众"),
                ),
                content=msg.text,
                timestamp_ms=msg.timestamp_ms,
                simulated=bool(msg.simulated),  # 数据溯源标记透传（§1.6 / ADR-006）
            )
            await self.emit_event(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)
        except Exception as e:
            self.logger.debug(f"emit danmaku 失败: {e}", exc_info=True)


__all__ = ["MockCollector"]
