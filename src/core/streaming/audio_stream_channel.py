"""AudioStreamChannel - 音频流专用发布-订阅通道

提供音频数据从 TTS Provider 到多个订阅者（VTS、RemoteStream 等）的
低延迟、带背压控制的传输通道。
"""

import asyncio
from typing import Dict, Optional, Callable, Awaitable, Any

from .audio_chunk import AudioChunk, AudioMetadata
from .backpressure import BackpressureStrategy, SubscriberConfig, PublishResult
from src.core.utils.logger import get_logger


class AudioStreamChannel:
    """
    音频流专用通道管理器

    特性:
    - 回调注册式订阅（无需创建独立 Subscriber 类）
    - 订阅者级别的背压控制
    - 非阻塞发布（支持 DROP_NEWEST 策略）
    - 生命周期管理（start/stop）
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._logger = get_logger(f"AudioStreamChannel.{name}")

        # 订阅者存储: {sub_id: (config, callbacks, queue)}
        self._subscribers: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

        # 统计信息
        self._publish_count = 0
        self._drop_count = 0
        self._is_started = False

    async def start(self) -> None:
        """启动通道"""
        self._is_started = True
        self._logger.info(f"AudioStreamChannel '{self.name}' 已启动")

    async def stop(self) -> None:
        """停止通道并清理所有订阅者"""
        self._is_started = False

        async with self._lock:
            # 清空所有队列
            for sub_data in self._subscribers.values():
                queue = sub_data.get("queue")
                if queue:
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break

            self._subscribers.clear()

        self._logger.info(f"AudioStreamChannel '{self.name}' 已停止")

    async def subscribe(
        self,
        name: str,
        on_audio_chunk: Callable[[AudioChunk], Awaitable[None]],
        on_audio_start: Optional[Callable[[AudioMetadata], Awaitable[None]]] = None,
        on_audio_end: Optional[Callable[[AudioMetadata], Awaitable[None]]] = None,
        config: Optional[SubscriberConfig] = None,
    ) -> str:
        """
        订阅音频流（回调注册式）

        Args:
            name: 订阅者名称
            on_audio_chunk: 音频块回调
            on_audio_start: 音频开始回调（可选）
            on_audio_end: 音频结束回调（可选）
            config: 订阅者配置

        Returns:
            订阅 ID
        """
        if not self._is_started:
            raise RuntimeError(f"AudioStreamChannel '{self.name}' 未启动")

        config = config or SubscriberConfig()

        # 创建队列
        queue = asyncio.Queue(maxsize=config.queue_size)

        # 存储订阅者信息
        sub_data = {
            "name": name,
            "config": config,
            "callbacks": {
                "chunk": on_audio_chunk,
                "start": on_audio_start,
                "end": on_audio_end,
            },
            "queue": queue,
        }

        sub_id = f"{self.name}.{name}"

        async with self._lock:
            self._subscribers[sub_id] = sub_data

        self._logger.info(f"订阅者 '{name}' 已注册 (ID: {sub_id})")
        return sub_id

    async def unsubscribe(self, sub_id: str) -> bool:
        """取消订阅"""
        async with self._lock:
            if sub_id in self._subscribers:
                del self._subscribers[sub_id]
                self._logger.info(f"订阅者 '{sub_id}' 已取消")
                return True
        return False

    async def notify_start(self, metadata: AudioMetadata) -> PublishResult:
        """通知所有订阅者：音频流开始"""
        results = PublishResult()

        async with self._lock:
            for sub_id, sub_data in self._subscribers.items():
                try:
                    callback = sub_data["callbacks"].get("start")
                    if callback:
                        await callback(metadata)
                        results.success_count += 1
                except Exception as e:
                    results.errors[sub_id] = str(e)

        return results

    async def notify_end(self, metadata: AudioMetadata) -> PublishResult:
        """通知所有订阅者：音频流结束"""
        results = PublishResult()

        async with self._lock:
            for sub_id, sub_data in self._subscribers.items():
                try:
                    callback = sub_data["callbacks"].get("end")
                    if callback:
                        await callback(metadata)
                        results.success_count += 1
                except Exception as e:
                    results.errors[sub_id] = str(e)

        return results

    async def publish(self, chunk: AudioChunk) -> PublishResult:
        """
        发布音频块到所有订阅者

        修复背压机制代码逻辑错误（Critic 问题 #1）
        """
        results = PublishResult()

        if not self._is_started:
            self._logger.warning("AudioStreamChannel 未启动")
            return results

        self._publish_count += 1

        async with self._lock:
            for sub_id, sub_data in self._subscribers.items():
                try:
                    # 获取订阅者队列
                    queue = sub_data["queue"]

                    # 应用背压策略
                    if queue.full():
                        handled = await self._handle_backpressure(sub_data, chunk)
                        if handled:
                            results.success_count += 1
                        else:
                            results.drop_count += 1
                            self._logger.debug(f"订阅者 {sub_id} 队列满，丢弃新数据")
                            continue  # 关键修复：不再执行下面的 put_nowait

                    # 非阻塞 put
                    queue.put_nowait(chunk)
                    results.success_count += 1

                except asyncio.CancelledError:
                    # 订阅者取消
                    results.errors[sub_id] = "订阅者取消"
                except Exception as e:
                    results.errors[sub_id] = str(e)

        # 统计丢弃
        if results.drop_count > 0:
            self._drop_count += results.drop_count

        return results

    async def _handle_backpressure(self, sub_data: Dict[str, Any], chunk: AudioChunk) -> bool:
        """
        处理背压（返回 bool 表示是否成功处理）

        修复背压机制代码逻辑错误（Critic 问题 #1）

        Returns:
            True: 已成功处理（BLOCK 阻塞放入、DROP_OLDEST 替换）
            False: 丢弃（DROP_NEWEST）
        """
        queue = sub_data["queue"]
        strategy = sub_data["config"].backpressure_strategy

        if strategy == BackpressureStrategy.BLOCK:
            # 阻塞等待队列有空间
            await queue.put(chunk)
            return True

        elif strategy == BackpressureStrategy.DROP_NEWEST:
            # 丢弃新数据（当前 chunk 不放入队列）
            return False

        elif strategy == BackpressureStrategy.DROP_OLDEST:
            # 移除最旧数据，放入新数据
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            queue.put_nowait(chunk)
            return True

        elif strategy == BackpressureStrategy.FAIL_FAST:
            raise RuntimeError(f"订阅者 '{sub_data['name']}' 队列已满")

        return False

    def get_stats(self) -> Dict[str, Any]:
        """获取通道统计信息"""
        return {
            "name": self.name,
            "is_started": self._is_started,
            "subscriber_count": len(self._subscribers),
            "publish_count": self._publish_count,
            "drop_count": self._drop_count,
        }
