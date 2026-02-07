"""
InputProviderManager - 输入Provider管理器

负责管理多个InputProvider的生命周期、错误隔离和统计信息。
"""

import asyncio
import warnings
from typing import Any
from dataclasses import dataclass
import time

from src.core.event_bus import EventBus
from src.core.base.input_provider import InputProvider
from src.core.events.names import CoreEvents
from src.core.events.payloads.input import RawDataPayload
from src.core.utils.logger import get_logger


@dataclass
class ProviderStats:
    """Provider统计信息"""

    name: str
    started_at: float | None = None
    stopped_at: float | None = None
    message_count: int = 0
    error_count: int = 0
    is_running: bool = False
    last_message_at: float | None = None

    @property
    def uptime(self) -> float | None:
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
        self._providers: list[InputProvider] = []

        # Provider任务字典 {provider_name: task}
        self._provider_tasks: dict[str, asyncio.Task] = {}

        # Provider统计信息字典 {provider_name: stats}
        self._provider_stats: dict[str, ProviderStats] = {}

        # 停止事件
        self._stop_event = asyncio.Event()

        # 是否已启动
        self._is_started = False

        self.logger.debug("InputProviderManager初始化完成")

    async def start_all_providers(self, providers: list[InputProvider]) -> None:
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

        # 并发启动所有Provider（后台运行，不等待完成）
        start_tasks = []
        for provider in providers:
            provider_name = self._get_provider_name(provider)
            task = asyncio.create_task(
                self._run_provider(provider, provider_name), name=f"InputProvider-{provider_name}"
            )
            self._provider_tasks[provider_name] = task
            start_tasks.append(task)

        # Provider任务已创建并开始后台运行
        # 注意：Provider运行在无限循环中，因此不等待任务完成
        # 错误隔离已在_run_provider方法中实现
        self.logger.info(f"所有{len(providers)}个Provider已启动并在后台运行")

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
            except TimeoutError:
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

    async def get_stats(self) -> dict[str, Any]:
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

    def get_provider_by_source(self, source: str) -> InputProvider | None:
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
                await self.event_bus.emit_typed(
                    CoreEvents.PERCEPTION_RAW_DATA_GENERATED,
                    RawDataPayload.from_raw_data(data),
                    source=provider_name,
                )

                self.logger.debug(f"Provider {provider_name} 生成数据: {data.content}")
        except asyncio.CancelledError:
            self.logger.info(f"Provider {provider_name} 被取消")
        except Exception as e:
            self.logger.error(f"Provider {provider_name} 运行时出错: {e}", exc_info=True)
            self._provider_stats[provider_name].error_count += 1
            # 不重新抛出，避免影响其他Provider

    async def load_from_config(self, config: dict[str, Any], config_service=None) -> list[InputProvider]:
        """
        从配置加载并创建所有InputProvider（支持三级配置合并）

        Args:
            config: 输入配置（来自[providers.input]）
            config_service: ConfigService实例（用于三级配置加载）

        Returns:
            创建的InputProvider列表

        配置格式:
            [providers.input]
            enabled = true
            enabled_inputs = ["console_input", "bili_danmaku"]

            # 可选：常用参数覆盖
            [providers.input.overrides.console_input]
            user_id = "custom_user"
        """
        from src.core.provider_registry import ProviderRegistry

        self.logger.info("开始从配置加载InputProvider...")

        # 检查是否启用
        enabled = config.get("enabled", True)
        if not enabled:
            self.logger.info("输入层已禁用（enabled=false）")
            return []

        # 获取Provider列表（支持新旧配置格式）
        enabled_inputs = config.get("enabled_inputs", config.get("inputs", []))
        if not enabled_inputs:
            self.logger.warning("未配置任何输入Provider（enabled_inputs为空）")
            return []

        self.logger.info(f"配置了 {len(enabled_inputs)} 个输入Provider: {enabled_inputs}")

        # 创建Provider实例
        created_providers = []
        failed_count = 0

        for input_name in enabled_inputs:
            try:
                # 使用新的三级配置加载
                if config_service is not None:
                    try:
                        from src.services.config.schemas import get_provider_schema

                        schema_class = get_provider_schema(input_name, "input")
                    except (ImportError, AttributeError, KeyError):
                        # Schema registry未实现或Provider未注册，回退到None
                        schema_class = None

                    provider_config = config_service.get_provider_config_with_defaults(
                        provider_name=input_name,
                        provider_layer="input",
                        schema_class=schema_class,
                    )
                else:
                    # 回退到旧的配置加载方式（向后兼容）
                    warnings.warn(
                        f"InputProviderManager: Using deprecated configuration loading for '{input_name}'. "
                        f"Please pass config_service parameter to enable three-level configuration merge. "
                        f"Old configuration format will be removed in a future version.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    # 支持两种旧格式：
                    # 1. "inputs_config": {input_name: {...}}
                    # 2. "inputs": {input_name: {...}}
                    inputs_config = config.get("inputs_config", config.get("inputs", {}))
                    # 如果inputs_config是列表（新格式enabled_inputs），则创建空配置
                    if isinstance(inputs_config, list):
                        provider_config = {}
                    else:
                        provider_config = inputs_config.get(input_name, {}).copy()
                    provider_config["type"] = provider_config.get("type", input_name)

                provider_type = provider_config.get("type", input_name)

                # 创建Provider（不再检查enabled字段，由enabled_inputs控制）
                provider = ProviderRegistry.create_input(provider_type, provider_config)
                created_providers.append(provider)
                self.logger.info(f"成功创建InputProvider: {input_name} (type={provider_type})")
            except Exception as e:
                self.logger.error(f"InputProvider创建异常: {input_name} - {e}", exc_info=True)
                failed_count += 1

        if failed_count > 0:
            self.logger.warning(
                f"InputProvider加载完成: 成功={len(created_providers)}/{len(enabled_inputs)}, "
                f"失败={failed_count}/{len(enabled_inputs)}"
            )
        else:
            self.logger.info(f"InputProvider加载完成: 成功={len(created_providers)}/{len(enabled_inputs)}")

        return created_providers

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
