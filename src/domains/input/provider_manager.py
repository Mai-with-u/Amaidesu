"""
InputProviderManager - 输入Provider管理器

负责管理多个InputProvider的生命周期和错误隔离。
"""

import asyncio
from typing import Any, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.logging import get_logger
from src.modules.types.base.input_provider import InputProvider
from src.domains.input.pipelines.manager import InputPipelineManager


class InputProviderManager:
    """
    输入Provider管理器（重构后）

    负责管理多个InputProvider的生命周期、错误隔离。
    支持并发启动、优雅停止、错误隔离、Pipeline过滤等功能。
    """

    def __init__(
        self,
        event_bus: EventBus,
        pipeline_manager: Optional[InputPipelineManager] = None,
    ):
        """
        初始化InputProviderManager

        Args:
            event_bus: 事件总线实例
            pipeline_manager: Pipeline 管理器实例（可选）
        """
        self.event_bus = event_bus
        self.pipeline_manager = pipeline_manager
        self.logger = get_logger("InputProviderManager")

        # Provider列表
        self._providers: list[InputProvider] = []

        # Provider任务字典 {provider_name: task}
        self._provider_tasks: dict[str, asyncio.Task] = {}

        # 停止事件
        self._stop_event = asyncio.Event()

        # 是否已启动
        self._is_started = False

        if self.pipeline_manager:
            self.logger.info("已集成 Pipeline 到 ProviderManager")
        else:
            self.logger.debug("未提供 Pipeline，跳过消息过滤")

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

        # 注意：stop() 方法已经调用了 cleanup()，所以不需要额外清理
        # 步骤 2 中的 stop() 已经完成了所有清理工作

        self._is_started = False
        self.logger.info("所有InputProvider已停止")

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

        重构后：
        1. Provider.start() 启动 Provider
        2. Provider.stream() 获取数据流
        3. 通过 Pipeline 过滤（如果有）
        4. 发布 DATA_MESSAGE 事件
        5. Provider 停止后调用 stop() 清理资源

        捕获所有异常，避免单个Provider失败影响其他Provider。

        Args:
            provider: InputProvider实例
            provider_name: Provider名称
        """
        try:
            self.logger.info(f"Provider {provider_name} 开始运行")
            # 1. 启动 Provider
            await provider.start()
            # 2. 迭代数据流
            async for message in provider.stream():
                # Pipeline 过滤处理（如果有）
                if self.pipeline_manager:
                    message = await self.pipeline_manager.process(message)
                    # Pipeline 可能返回 None 表示过滤掉
                    if message is None:
                        self.logger.debug(f"Provider {provider_name} 消息被 Pipeline 过滤")
                        continue

                # 发布事件
                await self.event_bus.emit(
                    CoreEvents.DATA_MESSAGE,
                    MessageReadyPayload.from_normalized_message(message),
                    source=provider_name,
                )

                self.logger.debug(f"Provider {provider_name} 生成消息: {message.text}")
        except asyncio.CancelledError:
            self.logger.info(f"Provider {provider_name} 被取消")
        except Exception as e:
            self.logger.error(f"Provider {provider_name} 运行时出错: {e}", exc_info=True)
        finally:
            # 确保资源被清理
            try:
                await provider.stop()
            except Exception as e:
                self.logger.warning(f"Provider {provider_name} 停止时出错: {e}")

    async def load_from_config(self, config: dict[str, Any], config_service=None) -> list[InputProvider]:
        """
        从配置加载并创建所有InputProvider（支持二级配置合并）

        Args:
            config: 输入配置（来自[providers.input]）
            config_service: ConfigService实例（用于二级配置加载）

        Returns:
            创建的InputProvider列表

        配置格式:
            [providers.input]
            enabled = true
            enabled_inputs = ["console_input", "bili_danmaku"]

            # 可选：常用参数覆盖
            [providers.input.console_input]
            user_id = "custom_user"
        """
        from src.modules.registry import ProviderRegistry

        # 确保所有 Provider 模块已导入（触发 Schema 注册）
        # 导入 providers 包会执行 __init__.py，注册所有 Provider
        try:
            from src.domains.input import providers  # noqa: F401

            self.logger.debug("已导入 providers 包，所有 Provider 应已注册")
        except ImportError as e:
            self.logger.warning(f"导入 providers 包失败: {e}")

        self.logger.info("开始从配置加载InputProvider...")

        # 检查是否启用
        enabled = config.get("enabled", True)
        if not enabled:
            self.logger.info("输入层已禁用（enabled=false）")
            return []

        # 获取Provider列表
        enabled_inputs = config.get("enabled_inputs")
        if not enabled_inputs:
            self.logger.warning("未配置任何输入Provider（enabled_inputs为空）")
            return []

        self.logger.info(f"配置了 {len(enabled_inputs)} 个输入Provider: {enabled_inputs}")

        # 创建Provider实例
        created_providers = []
        failed_count = 0

        for input_name in enabled_inputs:
            try:
                # 使用二级配置加载
                try:
                    from src.modules.config.schemas import get_provider_schema

                    schema_class = get_provider_schema(input_name, "input")
                except KeyError:
                    # Provider未注册（模块未导入），回退到None
                    self.logger.debug(
                        f"Provider '{input_name}' Schema未注册（模块可能未导入），将使用无Schema方式加载配置"
                    )
                    schema_class = None

                provider_config = config_service.get_provider_config_with_defaults(
                    provider_name=input_name,
                    provider_layer="input",
                    schema_class=schema_class,
                )

                provider_type = provider_config.get("type", input_name)

                # 创建 ProviderContext
                from src.modules.di.context import ProviderContext

                context = ProviderContext(event_bus=self.event_bus)

                # 创建Provider（不再检查enabled字段，由enabled_inputs控制）
                provider = ProviderRegistry.create_input(provider_type, provider_config, context=context)
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
