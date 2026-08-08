"""
InputCollectorManager - 输入Collector管理器

负责管理多个InputCollector的生命周期和错误隔离。

使用 _COLLECTORS 字典直接构造 Collector。
"""

import asyncio
from typing import Any, Dict, Optional

from typing import Type

from src.modules.di import instantiate_with_di
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.logging import get_logger
from src.modules.pipeline import PipelineManager
from src.modules.types.base.normalized_message import NormalizedMessage
from src.stages.input.registry import _COLLECTORS


class InputCollectorManager:
    def __init__(
        self,
        event_bus: EventBus,
        pipeline_manager: Optional[PipelineManager[NormalizedMessage]] = None,
        services_by_type: Optional[Dict[Type, Any]] = None,
    ):
        self.event_bus = event_bus
        self.pipeline_manager = pipeline_manager
        self._services_by_type = services_by_type or {}
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
            except (TimeoutError, asyncio.CancelledError) as e:
                # Ctrl+C 时 asyncio.run 取消 main task → wait_for 抛出 CancelledError，
                # 需与 TimeoutError 一样强制取消未结束的子任务，避免"Task was destroyed" 警告。
                # 此处吞掉 CancelledError，让 cleanup() 正常返回 run_shutdown，
                # 由 run_shutdown 统一保证所有 cleanup 步骤都执行。
                if isinstance(e, TimeoutError):
                    self.logger.warning("等待Collector停止超时，强制取消任务")
                else:
                    self.logger.debug("收到 CancelledError，强制取消未结束的 Collector 任务")
                for task in self._collector_tasks.values():
                    if not task.done():
                        task.cancel()
                for task in self._collector_tasks.values():
                    if not task.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                        except (TimeoutError, asyncio.CancelledError, Exception):
                            pass

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

    def get_component_summaries(self) -> list[dict[str, Any]]:
        """Dashboard 协议接口：返回 Input 阶段参与者状态摘要字典列表

        包含全部已注册 Collector：已实例化的标 is_enabled=True，
        未启用（不在配置 enabled 列表）的补充为 is_enabled=False，便于前端展示与启用。
        """
        summaries = [
            {
                "name": s["name"],
                "phase": "input",
                "type": "collector",
                "is_started": s["is_started"],
                "is_enabled": True,
                "config": s.get("config"),
            }
            for s in self.get_collector_status()
        ]
        loaded = {s["name"] for s in summaries}
        from src.stages.input.registry import list_collectors

        for name in list_collectors():
            if name not in loaded:
                summaries.append(
                    {
                        "name": name,
                        "phase": "input",
                        "type": "collector",
                        "is_started": False,
                        "is_enabled": False,
                    }
                )
        return summaries

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

                nick = message.user_nickname or message.user_id or "anonymous"
                self.logger.info(f"[{collector_name}] {nick}({message.user_id}): {message.text}")
                self.logger.debug(
                    f"[{collector_name}] input.message.received: "
                    f"text={message.text!r}, source={message.source!r}, "
                    f"user_id={message.user_id!r}, user_nickname={message.user_nickname!r}"
                )
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

        enabled_collectors = config.get("enabled") or []
        if not enabled_collectors:
            self.logger.warning("未配置任何输入Collector（enabled 为空或缺失），输入层停用")
            return []

        self.logger.info(f"配置了 {len(enabled_collectors)} 个输入Collector: {enabled_collectors}")

        created_collectors = []
        failed_count = 0

        for input_name in enabled_collectors:
            try:
                collector = await self._create_collector(input_name, config_service)
                created_collectors.append(collector)
                self.logger.info(f"成功创建InputCollector: {input_name}")
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

    async def _create_collector(self, name: str, config_service=None):
        """创建单个 Collector 实例（load_from_config 与动态启用的公共工厂）"""
        schema_class = None
        if config_service:
            try:
                from src.modules.config.schemas import get_config_schema

                schema_class = get_config_schema(name, "input")
            except KeyError:
                pass

        if config_service:
            collector_config = config_service.get_config_with_defaults(
                name=name,
                phase="input",
                schema_class=schema_class,
            )
        else:
            collector_config = {}

        collector_type = collector_config.get("type", name)

        if collector_type not in _COLLECTORS:
            available = list(_COLLECTORS.keys())
            raise KeyError(f"Collector '{collector_type}' 未找到。可用: {available}")

        collector_cls = _COLLECTORS[collector_type]

        services = {EventBus: self.event_bus, **self._services_by_type}
        return instantiate_with_di(
            collector_cls,
            config=collector_config,
            services_by_type=services,
        )

    async def enable_collector(self, name: str, config_service=None) -> bool:
        """动态启用单个 Collector：创建实例并启动（若 Manager 已运行）。

        Args:
            name: Collector 名称
            config_service: 可选，用于读取组件配置（缺省时使用空配置）

        Returns:
            True 表示已在运行或启动成功
        """
        if self._get_collector_by_name(name):
            self.logger.info(f"Collector '{name}' 已在运行，跳过")
            return True

        try:
            collector = await self._create_collector(name, config_service)
        except Exception as e:
            self.logger.error(f"动态启用 Collector '{name}' 失败: {e}", exc_info=True)
            return False

        self._collectors.append(collector)
        if self._is_started:
            task = asyncio.create_task(self._run_collector(collector, name), name=f"InputCollector-{name}")
            self._collector_tasks[name] = task
        self.logger.info(f"Collector '{name}' 动态启用成功")
        return True

    async def disable_collector(self, name: str) -> bool:
        """动态停用单个 Collector：取消运行任务并停止实例，从运行列表移除。"""
        collector = self._get_collector_by_name(name)
        if not collector:
            self.logger.info(f"Collector '{name}' 未在运行，跳过")
            return True

        task = self._collector_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass

        try:
            await collector.stop()
        except Exception as e:
            self.logger.error(f"停止Collector '{name}' 时出错: {e}", exc_info=True)

        self._collectors = [c for c in self._collectors if c is not collector]
        self.logger.info(f"Collector '{name}' 动态停用成功")
        return True

    async def start_collector(self, collector) -> bool:
        """启动（或重启）单个 Collector 的运行任务（供 Dashboard 启停控制）。

        若任务已在运行则跳过；若任务已结束（stop 后 collect() 退出）则重新创建任务。
        """
        name = self._get_collector_name(collector)
        task = self._collector_tasks.get(name)
        if task and not task.done():
            if getattr(collector, "is_started", False):
                self.logger.info(f"Collector '{name}' 已在运行，跳过启动")
                return True
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass
        self._collector_tasks.pop(name, None)

        task = asyncio.create_task(self._run_collector(collector, name), name=f"InputCollector-{name}")
        self._collector_tasks[name] = task
        self.logger.info(f"Collector '{name}' 已启动")
        return True

    async def stop_collector(self, collector) -> bool:
        """停止单个 Collector：取消运行任务并调用 stop()（供 Dashboard 启停控制）。"""
        name = self._get_collector_name(collector)
        task = self._collector_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass

        try:
            await collector.stop()
        except Exception as e:
            self.logger.error(f"停止Collector '{name}' 时出错: {e}", exc_info=True)
        self.logger.info(f"Collector '{name}' 已停止")
        return True

    def _get_collector_by_name(self, name: str):
        """按名称精确查找已加载的 Collector 实例"""
        for collector in self._collectors:
            if self._get_collector_name(collector) == name:
                return collector
        return None

    def _get_collector_name(self, collector) -> str:
        class_name = collector.__class__.__name__
        if "Collector" in class_name:
            return class_name.replace("Collector", "")
        return class_name
