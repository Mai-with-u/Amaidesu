"""
InputProviderManager - 输入Provider管理器

负责管理多个InputProvider的生命周期、错误隔离和统计信息。
"""

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import time

from src.core.event_bus import EventBus
from src.core.providers.input_provider import InputProvider
from src.utils.logger import get_logger


@dataclass
class ProviderStats:
    """Provider统计信息"""

    name: str
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    message_count: int = 0
    error_count: int = 0
    is_running: bool = False
    last_message_at: Optional[float] = None

    @property
    def uptime(self) -> Optional[float]:
        """运行时长(秒)"""
        if not self.started_at:
            return None
        end_time = self.stopped_at or time.time()
        return end_time - self.started_at


class InputProviderManager:
    """
    输入Provider管理器

    负责管理多个InputProvider的生命周期、错误隔离和统计信息。
    支持并发启动、优雅停止、错误隔离等功能。
    """

    def __init__(self, event_bus: EventBus):
        """
        初始化InputProviderManager

        Args:
            event_bus: 事件总线实例
        """
        self.event_bus = event_bus
        self.logger = get_logger("InputProviderManager")

        # Provider列表
        self._providers: List[InputProvider] = []

        # Provider任务字典 {provider_name: task}
        self._provider_tasks: Dict[str, asyncio.Task] = {}

        # Provider统计信息字典 {provider_name: stats}
        self._provider_stats: Dict[str, ProviderStats] = {}

        # 停止事件
        self._stop_event = asyncio.Event()

        # 是否已启动
        self._is_started = False

        self.logger.debug("InputProviderManager初始化完成")

    async def start_all_providers(self, providers: List[InputProvider]) -> None:
        """
        并发启动所有InputProvider，错误隔离

        使用asyncio.gather包装每个Provider启动，确保单个Provider失败不影响其他。

        Args:
            providers: InputProvider列表
        """
        if self._is_started:
            self.logger.warning("InputProviderManager已启动，忽略重复启动")
            return

        self._providers = providers
        self._stop_event.clear()

        self.logger.info(f"开始启动{len(providers)}个InputProvider...")

        # 初始化Provider统计信息（初始状态为未运行）
        for provider in providers:
            provider_name = self._get_provider_name(provider)
            self._provider_stats[provider_name] = ProviderStats(
                name=provider_name, started_at=time.time(), is_running=False
            )

        # 并发启动所有Provider
        start_tasks = []
        for provider in providers:
            provider_name = self._get_provider_name(provider)
            task = asyncio.create_task(
                self._run_provider(provider, provider_name), name=f"InputProvider-{provider_name}"
            )
            self._provider_tasks[provider_name] = task
            start_tasks.append(task)

        # 等待所有Provider启动(使用gather包装，错误隔离)
        results = await asyncio.gather(*start_tasks, return_exceptions=True)

        # 记录启动失败的Provider
        failed_count = 0
        for i, result in enumerate(results):
            provider_name = self._get_provider_name(providers[i])
            if isinstance(result, Exception):
                self.logger.error(f"Provider {provider_name} 启动失败: {result}", exc_info=True)
                self._provider_stats[provider_name].is_running = False
                failed_count += 1
            elif isinstance(result, asyncio.CancelledError):
                self.logger.warning(f"Provider {provider_name} 启动被取消")
                self._provider_stats[provider_name].is_running = False
                failed_count += 1

        if failed_count > 0:
            self.logger.warning(f"{failed_count}个Provider启动失败，{len(providers) - failed_count}个Provider正常运行")
        else:
            self.logger.info(f"所有{len(providers)}个Provider启动成功")

        self._is_started = True

    async def stop_all_providers(self) -> None:
        """
        优雅停止所有InputProvider

        1. 设置停止事件
        2. 调用每个Provider的stop()方法
        3. 等待所有Provider任务完成
        4. 调用每个Provider的cleanup()方法
        """
        if not self._is_started:
            self.logger.warning("InputProviderManager未启动，忽略停止")
            return

        self.logger.info("开始停止所有InputProvider...")

        # 1. 设置停止事件
        self._stop_event.set()

        # 2. 调用每个Provider的stop()方法
        for provider in self._providers:
            provider_name = self._get_provider_name(provider)
            try:
                await provider.stop()
                self._provider_stats[provider_name].stopped_at = time.time()
                self._provider_stats[provider_name].is_running = False
            except Exception as e:
                self.logger.error(f"停止Provider {provider_name}时出错: {e}", exc_info=True)

        # 3. 等待所有Provider任务完成(超时10秒)
        if self._provider_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._provider_tasks.values(), return_exceptions=True), timeout=10.0
                )
            except asyncio.TimeoutError:
                self.logger.warning("等待Provider停止超时，强制取消任务")
                for task in self._provider_tasks.values():
                    if not task.done():
                        task.cancel()

        # 4. 调用每个Provider的cleanup()方法
        for provider in self._providers:
            provider_name = self._get_provider_name(provider)
            try:
                await provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理Provider {provider_name}时出错: {e}", exc_info=True)

        self._is_started = False
        self.logger.info("所有InputProvider已停止")

    async def get_stats(self) -> Dict[str, Any]:
        """
        获取所有Provider的统计信息

        Returns:
            统计信息字典
        """
        stats_summary = {}

        for provider_name, stats in self._provider_stats.items():
            stats_summary[provider_name] = {
                "name": stats.name,
                "started_at": stats.started_at,
                "stopped_at": stats.stopped_at,
                "uptime": stats.uptime,
                "message_count": stats.message_count,
                "error_count": stats.error_count,
                "is_running": stats.is_running,
                "last_message_at": stats.last_message_at,
            }

        return stats_summary

    def get_provider_by_source(self, source: str) -> Optional[InputProvider]:
        """
        根据source获取Provider实例

        Args:
            source: 数据源标识

        Returns:
            InputProvider实例，如果找不到返回None
        """
        for provider in self._providers:
            provider_name = self._get_provider_name(provider)
            if source in provider_name or provider_name == source:
                return provider
        return None

    async def _run_provider(self, provider: InputProvider, provider_name: str) -> None:
        """
        运行单个Provider，错误隔离

        捕获所有异常，避免单个Provider失败影响其他Provider。

        Args:
            provider: InputProvider实例
            provider_name: Provider名称
        """
        try:
            self.logger.info(f"Provider {provider_name} 开始运行")
            self._provider_stats[provider_name].is_running = True
            async for data in provider.start():
                # 更新统计信息
                self._provider_stats[provider_name].message_count += 1
                self._provider_stats[provider_name].last_message_at = time.time()

                # 发布事件
                await self.event_bus.emit(
                    "perception.raw_data.generated", {"data": data, "source": provider_name}, source=provider_name
                )

                self.logger.debug(f"Provider {provider_name} 生成数据: {data.content}")
        except asyncio.CancelledError:
            self.logger.info(f"Provider {provider_name} 被取消")
        except Exception as e:
            self.logger.error(f"Provider {provider_name} 运行时出错: {e}", exc_info=True)
            self._provider_stats[provider_name].error_count += 1
            # 不重新抛出，避免影响其他Provider

    def _get_provider_name(self, provider: InputProvider) -> str:
        """
        获取Provider名称

        Args:
            provider: InputProvider实例

        Returns:
            Provider名称
        """
        # 尝试从类名提取名称
        class_name = provider.__class__.__name__
        if "Provider" in class_name:
            return class_name.replace("Provider", "")
        return class_name
