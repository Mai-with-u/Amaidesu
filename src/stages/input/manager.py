"""
InputCollectorManager - 输入Collector管理器

负责管理多个InputCollector的生命周期和错误隔离。

使用 _COLLECTORS 字典直接构造 Collector。
"""

import asyncio
from typing import Any, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.logging import get_logger
from src.stages.input.pipelines.manager import InputPipelineManager
from src.stages.input.registry import _COLLECTORS


class InputCollectorManager:
    def __init__(
        self,
        event_bus: EventBus,
        pipeline_manager: Optional[InputPipelineManager] = None,
    ):
        self.event_bus = event_bus
        self.pipeline_manager = pipeline_manager
        self.logger = get_logger("InputCollectorManager")

        self._collectors: list = []
        self._collector_tasks: dict[str, asyncio.Task] = {}
        self._stop_event = asyncio.Event()
        self._is_started = False

    async def setup(
        self,
        config: Optional[Dict[str, Any]] = None,
        config_service=None,
    ) -> None:
        self.logger.info("开始设置 InputCollectorManager...")

        if config is None:
            self.logger.warning("未提供配置，跳过 Collector 加载")
            return

        collectors = await self.load_from_config(config, config_service=config_service)
        self._collectors = collectors

        self.logger.info(f"InputCollectorManager 设置完成，加载了 {len(collectors)} 个 Collector")

    async def start(self) -> None:
        if not self._collectors:
            self.logger.warning("没有已加载的 Collector，跳过启动")
            return

        await self._start_all_collectors(self._collectors)

    async def start_all_collectors(self, collectors: list) -> None:
        """直接启动传入的 collectors 列表（供测试使用）"""
        await self._start_all_collectors(collectors)

    async def stop_all_collectors(self) -> None:
        """停止所有 collectors（供测试使用）"""
        await self._stop_all_collectors()

    async def cleanup(self) -> None:
        self.logger.info("清理 InputCollectorManager...")

        if self._is_started:
            await self._stop_all_collectors()

        self._collectors.clear()
        self._collector_tasks.clear()

        self.logger.info("InputCollectorManager 清理完成")

    async def _start_all_collectors(self, collectors: list) -> None:
        if self._is_started:
            self.logger.warning("InputCollectorManager已启动，忽略重复启动")
            return

        self._collectors = collectors
        self._stop_event.clear()

        self.logger.info(f"开始启动{len(collectors)}个Collector...")

        for collector in collectors:
            collector_name = self._get_collector_name(collector)
            task = asyncio.create_task(
                self._run_collector(collector, collector_name), name=f"InputCollector-{collector_name}"
            )
            self._collector_tasks[collector_name] = task

        self.logger.info(f"所有{len(collectors)}个Collector已启动并在后台运行")

        self._is_started = True

    async def _stop_all_collectors(self) -> None:
        if not self._is_started:
            self.logger.warning("InputCollectorManager未启动，忽略停止")
            return

        self.logger.info("开始停止所有Collector...")

        self._stop_event.set()

        for collector in self._collectors:
            collector_name = self._get_collector_name(collector)
            try:
                await collector.stop()
            except Exception as e:
                self.logger.error(f"停止Collector {collector_name}时出错: {e}", exc_info=True)

        if self._collector_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._collector_tasks.values(), return_exceptions=True), timeout=10.0
                )
            except TimeoutError:
                self.logger.warning("等待Collector停止超时，强制取消任务")
                for task in self._collector_tasks.values():
                    if not task.done():
                        task.cancel()

        self._is_started = False
        self.logger.info("所有Collector已停止")

    def get_collector_by_source(self, source: str):
        for collector in self._collectors:
            collector_name = self._get_collector_name(collector)
            if source in collector_name or collector_name == source:
                return collector
        return None

    def get_collectors(self) -> list:
        """
        获取所有已加载的 InputCollector 实例。

        Returns:
            InputCollector 实例列表
        """
        return self._collectors.copy()

    def get_collector_status(self) -> list[dict[str, Any]]:
        """
        获取所有 InputCollector 的状态信息。

        用于 Dashboard 等外部组件查询，避免直接访问私有属性。

        Returns:
            包含 name, is_started, config 的字典列表
        """
        result = []
        for collector in self._collectors:
            collector_name = self._get_collector_name(collector)
            result.append(
                {
                    "name": collector_name,
                    "is_started": getattr(collector, "is_started", False),
                    "config": getattr(collector, "config", None),
                }
            )
        return result

    async def _run_collector(self, collector, collector_name: str) -> None:
        try:
            self.logger.info(f"Collector {collector_name} 开始运行")
            await collector.start()
            async for message in collector.collect():
                if self.pipeline_manager:
                    message = await self.pipeline_manager.process(message)
                    if message is None:
                        self.logger.debug(f"Collector {collector_name} 消息被 Pipeline 过滤")
                        continue

                await self.event_bus.emit(
                    CoreEvents.INPUT_MESSAGE_RECEIVED,
                    MessageReadyPayload.from_normalized_message(message),
                    source=collector_name,
                )

                self.logger.debug(f"Collector {collector_name} 生成消息: {message.text}")
        except asyncio.CancelledError:
            self.logger.info(f"Collector {collector_name} 被取消")
        except Exception as e:
            self.logger.error(f"Collector {collector_name} 运行时出错: {e}", exc_info=True)
        finally:
            try:
                await collector.stop()
            except Exception as e:
                self.logger.warning(f"Collector {collector_name} 停止时出错: {e}")

    async def load_from_config(self, config: dict[str, Any], config_service=None) -> list:
        self.logger.info("开始从配置加载InputCollector...")

        enabled = config.get("enabled", True)
        if not enabled:
            self.logger.info("输入层已禁用（enabled=false）")
            return []

        enabled_collectors = config.get("enabled")
        if not enabled_collectors:
            self.logger.warning("未配置任何输入Collector（enabled为空）")
            return []

        self.logger.info(f"配置了 {len(enabled_collectors)} 个输入Collector: {enabled_collectors}")

        created_collectors = []
        failed_count = 0

        for input_name in enabled_collectors:
            try:
                schema_class = None
                if config_service:
                    try:
                        from src.modules.config.schemas import get_config_schema

                        schema_class = get_config_schema(input_name, "input")
                    except KeyError:
                        pass

                if config_service:
                    collector_config = config_service.get_config_with_defaults(
                        name=input_name,
                        phase="input",
                        schema_class=schema_class,
                    )
                else:
                    collector_config = {}

                collector_type = collector_config.get("type", input_name)

                # 使用 _COLLECTORS 字典直接获取 Collector 类
                if collector_type not in _COLLECTORS:
                    available = list(_COLLECTORS.keys())
                    raise KeyError(f"Collector '{collector_type}' 未找到。可用: {available}")

                collector_cls = _COLLECTORS[collector_type]

                # 直接构造 Collector
                collector = collector_cls(config=collector_config, event_bus=self.event_bus)
                created_collectors.append(collector)
                self.logger.info(f"成功创建InputCollector: {input_name} (type={collector_type})")
            except Exception as e:
                self.logger.error(f"InputCollector创建异常: {input_name} - {e}", exc_info=True)
                failed_count += 1

        if failed_count > 0:
            self.logger.warning(
                f"InputCollector加载完成: 成功={len(created_collectors)}/{len(enabled_collectors)}, "
                f"失败={failed_count}/{len(enabled_collectors)}"
            )
        else:
            self.logger.info(f"InputCollector加载完成: 成功={len(created_collectors)}/{len(enabled_collectors)}")

        return created_collectors

    def _get_collector_name(self, collector) -> str:
        class_name = collector.__class__.__name__
        if "Collector" in class_name:
            return class_name.replace("Collector", "")
        return class_name
