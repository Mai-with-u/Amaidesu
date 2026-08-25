"""
MockCollector —— 模拟采集器（v2 / Wave 5 合并迁移）

合并 ``src/stages/input/collectors/mock_danmaku/`` + ``src/modules/simulator/``：
- 继承 ``BaseCollector``（流型感知者）
- 双模式：``jsonl``（默认，简单 JSONL 回放）+ ``simulator``（LLM 驱动完整模拟）
- emit ``room.message.*`` 语义事件（默认开启）
- 所有事件携带 ``simulated=True`` 数据溯源标记

verbatim 边界：JSONL 解析、PersonaPool/CadenceGenerator/GiftGenerator 行为 —— 保留。
仅调整：模块归属（stages/input → modules/collectors）、父类（BaseCollector）、
事件发送（input.message.received → room.message.*）。
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
    GiftInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.base.normalized_message import NormalizedMessage


class MockCollector(BaseCollector):
    """
    模拟采集器（v2）

    支持两种模式：
    - ``jsonl``：从 JSONL 文件按速率回放（mock_danmaku 行为，零 LLM 依赖）
    - ``simulator``：LLM 驱动的全功能模拟（persona_pool + cadence + gift_generator）

    所有事件 ``simulated=True``，消费方用此标记过滤（如 SQL ``WHERE simulated=0``）。
    """

    name = "mock"
    description = "模拟采集器（JSONL 回放或 LLM 驱动全模拟），所有事件 simulated=True"

    class ConfigSchema(BaseConfig):
        """模拟采集器配置"""

        # 通用
        mode: str = Field(default="jsonl", description="模式: jsonl | simulator")
        emit_semantic_events: bool = Field(default=True, description="emit room.message.* 语义事件")

        # JSONL 模式（默认）
        log_file_path: str = Field(default="msg_default.jsonl", description="JSONL 日志文件路径")
        send_interval: float = Field(default=1.0, description="发送间隔（秒）", ge=0.1)
        loop_playback: bool = Field(default=True, description="循环播放")

        # Simulator 模式
        base_rate_per_minute: float = Field(default=6.0, ge=0.1, le=60.0, description="基础消息率（条/分钟）")
        burst_multiplier: float = Field(default=3.0, ge=1.0, le=10.0, description="突发期倍率")
        warmup_duration_s: float = Field(default=0.0, ge=0.0, description="启动暖场期时长（秒）")
        gift_probability: float = Field(default=0.05, ge=0.0, le=0.5, description="礼物概率")
        sc_probability: float = Field(default=0.01, ge=0.0, le=0.1, description="SC 概率")
        enable_hater: bool = Field(default=False, description="是否启用黑粉人设")

        # 素材池路径（相对 collectors/mock/data/）
        gifts_toml: str = Field(default="simulator_gifts.toml", description="礼物清单文件名")
        residents_toml: str = Field(default="simulator_residents.toml", description="常驻人设文件名")

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(event_bus=event_bus)
        self.config = config or {}
        self.logger = get_logger(self.__class__.__name__)
        self.typed_config = self.ConfigSchema.from_dict(self.config)

        self._mode = self.typed_config.mode
        self._emit_semantic_events = self.typed_config.emit_semantic_events

        # 数据目录：src/modules/collectors/mock/data/
        self._data_dir = Path(__file__).resolve().parent / "data"

        # JSONL 模式状态
        self._message_lines: list[str] = []
        self._current_line_index: int = 0
        self._stop_event = asyncio.Event()
        self.is_started = False

        # Simulator 模式状态（延迟初始化）
        self._persona_pool = None
        self._cadence = None
        self._gift_generator = None

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
        """启动模拟消息发送循环"""
        self.is_started = True

        try:
            if self._mode == "jsonl":
                async for msg in self._collect_jsonl():
                    yield msg
            elif self._mode == "simulator":
                async for msg in self._collect_simulator():
                    yield msg
            else:
                self.logger.error(f"未知的 MockCollector 模式: {self._mode}")
                return
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

    async def _collect_simulator(self) -> AsyncIterator[NormalizedMessage]:
        """Simulator 模式（基于原 LiveStreamSimulator 简化版）

        简化策略：默认 base_rate_per_minute 按泊松间隔产出基础消息（文本/礼物/SC）。
        不引入 LLM（避免与原 simulator 服务重复）；仅做调度与素材池逻辑展示。
        完整 LLM 模拟功能仍由 ``src.modules.simulator`` 服务承载（不在本采集器范围内）。
        """
        import random

        # 加载礼物清单
        gifts = await self._load_gifts()
        if not gifts:
            self.logger.warning("未加载到礼物清单，将只生成文本消息。")

        # 简化节奏：每分钟 N 条 → 间隔 = 60/N 秒
        mean_interval = 60.0 / max(self.typed_config.base_rate_per_minute, 0.1)
        self.logger.info(
            f"MockCollector simulator 模式启动 (基础消息率 {self.typed_config.base_rate_per_minute} 条/分钟)"
        )

        warmup_end = asyncio.get_event_loop().time() + self.typed_config.warmup_duration_s

        while not self._stop_event.is_set():
            try:
                # 间隔抖动
                jitter = random.uniform(mean_interval * 0.5, mean_interval * 1.5)
                await asyncio.sleep(jitter)

                if self._stop_event.is_set():
                    break

                # 暖场期内简化处理
                if asyncio.get_event_loop().time() < warmup_end:
                    continue

                roll = random.random()
                if roll < self.typed_config.sc_probability and gifts.get("sc"):
                    gift = random.choice(gifts["sc"])
                    text = f"[SC {gift.get('sc_amount_rmb', 50)}元] 模拟观众: 这是模拟醒目留言"
                    data_type = "super_chat"
                elif roll < self.typed_config.sc_probability + self.typed_config.gift_probability and gifts.get(
                    "normal"
                ):
                    gift = random.choice(gifts["normal"])
                    text = f"模拟观众 送出了 1 个 {gift['gift_name']}"
                    data_type = "gift"
                else:
                    text = random.choice(
                        [
                            "模拟弹幕消息",
                            "主播好可爱！",
                            "66666",
                            "支持主播",
                            "这个有意思",
                        ]
                    )
                    data_type = "text"

                normalized_msg = NormalizedMessage(
                    text=text,
                    source=self.name,
                    data_type=data_type,
                    importance=0.5,
                    timestamp_ms=now_ms(),
                    user_id=f"sim_{random.randint(1000, 9999)}",
                    user_nickname="模拟观众",
                    platform="simulated",
                    simulated=True,  # 数据溯源标记
                )

                if self._emit_semantic_events:
                    await self._emit_semantic(normalized_msg)

                yield normalized_msg

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"simulator 循环异常: {e}", exc_info=True)
                await asyncio.sleep(1.0)

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

    async def _load_gifts(self) -> Dict[str, list]:
        """加载礼物清单（runtime data 文件，按 category 分组）"""
        gifts_path = self._data_dir / self.typed_config.gifts_toml
        if not gifts_path.exists():
            self.logger.warning(f"礼物清单文件未找到: {gifts_path}")
            return {}

        try:
            import tomllib

            with open(gifts_path, "rb") as f:
                data = tomllib.load(f)
            items = data.get("gifts", {}).get("items", [])
            grouped: Dict[str, list] = {"normal": [], "medium": [], "premium": [], "sc": []}
            for item in items:
                cat = item.get("category", "normal")
                grouped.setdefault(cat, []).append(item)
            self.logger.info(f"已加载 {len(items)} 个礼物，分类: {[(k, len(v)) for k, v in grouped.items()]}")
            return grouped
        except Exception as e:
            self.logger.error(f"加载礼物清单失败: {e}", exc_info=True)
            return {}

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
            )
            await self.emit_event(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)
        except Exception as e:
            self.logger.debug(f"emit danmaku 失败: {e}", exc_info=True)

    async def _emit_semantic(self, msg: NormalizedMessage) -> None:
        """根据 data_type emit 对应的 room.message.* 事件"""
        try:
            payload = RoomMessagePayload(
                live_session_id=str(msg.room_id or "simulated_default"),
                message_type=msg.data_type
                if msg.data_type in ("danmaku", "gift", "super_chat", "enter")
                else "danmaku",
                user=RoomMessageUser(
                    id=str(msg.user_id or "sim_unknown"),
                    name=str(msg.user_nickname or "模拟观众"),
                ),
                content=msg.text,
                timestamp_ms=msg.timestamp_ms,
            )
            if msg.data_type == "gift":
                payload.gift = GiftInfo(name="模拟礼物", count=1)
            elif msg.data_type == "super_chat":
                payload.sc = SuperChatInfo(amount=50.0)

            event_map = {
                "danmaku": CoreEvents.ROOM_MESSAGE_DANMAKU,
                "gift": CoreEvents.ROOM_MESSAGE_GIFT,
                "super_chat": CoreEvents.ROOM_MESSAGE_SUPER_CHAT,
                "enter": CoreEvents.ROOM_MESSAGE_ENTER,
            }
            event_name = event_map.get(payload.message_type)
            if event_name:
                await self.emit_event(event_name, payload)
        except Exception as e:
            self.logger.debug(f"emit semantic 失败: {e}", exc_info=True)
