"""
OutputHandlerManager - Output 阶段: 输出Handler管理器

职责:
- 协调 Decision 阶段 → Output 阶段 的数据流
- 订阅 DECISION_INTENT_GENERATED 事件，通过 OutputPipeline 处理后发布 OUTPUT_INTENT_READY 事件
- 管理多个 OutputHandler
- 支持并发渲染
- 错误隔离（单个Handler失败不影响其他）
- 超时控制（防止单个Handler阻塞）
- 生命周期管理（启动、停止、清理）
- 从配置加载Handler
- Pipeline 集成（OutputPipeline）

数据流（3 阶段架构）:
    Intent (Decision) → OutputHandlerManager → OutputPipeline 过滤 → OUTPUT_INTENT_READY 事件
                     → Output Handlers (TTS/Subtitle/Avatar/Sticker 等)

注意:
- OutputHandlerManager 负责过滤 Intent 并分发
- 所有 OutputHandler 订阅 OUTPUT_INTENT_READY 事件
"""

import asyncio
import inspect
import os
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel

from src.stages.output.capability_registry import OutputCapabilityRegistry
from src.stages.output.pipelines.base import OutputPipelineContext
from src.stages.output.pipelines.manager import OutputPipelineManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.logging import get_logger
from src.stages.output.registry import _HANDLERS

if TYPE_CHECKING:
    from src.modules.llm.manager import LLMManager
    from src.modules.prompts.manager import PromptManager
    from src.modules.tts.audio_device_manager import AudioDeviceManager
    from src.modules.streaming.audio_stream_channel import AudioStreamChannel


class RenderResult(BaseModel):
    """渲染结果"""

    name: str
    success: bool
    error: str | None = None
    timeout: bool = False
    duration: float = 0.0


# 需要 audio_stream_channel 的音频Handler列表
AUDIO_HANDLERS = {"edge_tts", "gptsovits", "omni_tts", "vts", "warudo", "vrchat"}


class OutputHandlerManager:
    """
    输出Handler管理器

    核心职责:
    - 协调 Decision 阶段 → Output 阶段 的数据流
    - 订阅 DECISION_INTENT_GENERATED 事件，通过 OutputPipeline 过滤后发布 OUTPUT_INTENT_READY 事件
    - 管理多个 OutputHandler 实例
    - 并发渲染到所有 Handler
    - 错误隔离（单个 Handler 失败不影响其他）
    - 超时控制（防止单个 Handler 阻塞）
    - 生命周期管理
    - Pipeline 集成
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[dict[str, Any]] = None,
        llm_manager: Optional["LLMManager"] = None,
        prompt_manager: Optional["PromptManager"] = None,
        audio_device_manager: Optional["AudioDeviceManager"] = None,
    ):
        self.event_bus = event_bus
        self.config = config or {}
        self.handlers: list[Any] = []
        self._handler_names: dict[Any, str] = {}
        self._handler_started: dict[Any, bool] = {}
        self.logger = get_logger("OutputHandlerManager")

        self._llm_service = llm_manager
        self._prompt_service = prompt_manager
        self._audio_device_service = audio_device_manager

        self.capability_registry = OutputCapabilityRegistry()
        self.logger.debug("OutputCapabilityRegistry 已创建")

        self.concurrent_rendering = self.config.get("concurrent_rendering", True)
        self.error_handling = self.config.get("error_handling", "continue")
        self.render_timeout = float(self.config.get("render_timeout", 10.0))

        self.pipeline_manager: Optional[OutputPipelineManager] = None

        self._is_setup = False
        self._event_handler_registered = False
        self._audio_stream_channel = None

        self.logger.info(
            f"OutputHandlerManager初始化完成 "
            f"(concurrent={self.concurrent_rendering}, "
            f"error_handling={self.error_handling}, "
            f"timeout={self.render_timeout}s)"
        )

    async def setup(
        self,
        config: dict[str, Any],
        config_service=None,
        root_config: Optional[dict[str, Any]] = None,
        audio_stream_channel: Optional["AudioStreamChannel"] = None,
        llm_manager=None,
        prompt_manager=None,
        audio_device_manager=None,
    ) -> None:
        self.logger.info("开始设置输出Handler管理器...")

        self._audio_stream_channel = audio_stream_channel

        self._llm_service = llm_manager or self._llm_service
        self._prompt_service = prompt_manager or self._prompt_service
        self._audio_device_service = audio_device_manager or self._audio_device_service

        pipeline_context = OutputPipelineContext(
            capability_registry=self.capability_registry,
            llm_service=self._llm_service,
            prompt_service=self._prompt_service,
        )
        self.logger.debug("OutputPipelineContext 已创建")

        self.pipeline_manager = OutputPipelineManager(context=pipeline_context)
        self.logger.info("输出Pipeline管理器已创建")

        pipeline_config = root_config.get("pipelines", {}) if root_config else {}
        if pipeline_config:
            pipeline_load_dir = os.path.join(os.path.dirname(__file__), "pipelines")
            pipeline_load_dir = os.path.abspath(pipeline_load_dir)
            self.logger.info(f"准备加载输出Pipeline (从目录: {pipeline_load_dir})...")

            try:
                await self.pipeline_manager.load_output_pipelines(pipeline_load_dir, pipeline_config)
                pipeline_count = len(self.pipeline_manager._pipelines)
                if pipeline_count > 0:
                    self.logger.info(f"输出Pipeline加载完成，共 {pipeline_count} 个管道。")
                else:
                    self.logger.info("未找到任何有效的输出Pipeline。")
            except Exception as e:
                self.logger.error(f"加载输出Pipeline时出错: {e}", exc_info=True)
        else:
            self.logger.info("配置中未启用管道功能")

        await self.load_from_config(config, config_service=config_service)

        self.event_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            self._on_decision_intent,
            model_class=IntentPayload,
            priority=50,
        )
        self._event_handler_registered = True
        self.logger.info(f"已订阅 '{CoreEvents.DECISION_INTENT_GENERATED}' 事件（类型化）")

        self._is_setup = True
        self.logger.info("输出Handler管理器设置完成")

    async def start(self):
        """启动输出Handler管理器"""
        if not self._is_setup:
            self.logger.warning("OutputHandlerManager 未设置，无法启动")
            return

        self.logger.info("启动输出Handler管理器...")

        try:
            await self._start_all_handlers()
            self.logger.info("OutputHandler 已启动")
        except Exception as e:
            self.logger.error(f"启动 OutputHandler 失败: {e}", exc_info=True)

    async def stop(self):
        """停止输出Handler管理器"""
        self.logger.info("停止输出Handler管理器...")

        try:
            await self._stop_all_handlers()
            self.logger.info("OutputHandler 已停止")
        except Exception as e:
            self.logger.error(f"停止 OutputHandler 失败: {e}", exc_info=True)

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理输出Handler管理器...")

        if self._event_handler_registered:
            try:
                self.event_bus.off(CoreEvents.DECISION_INTENT_GENERATED, self._on_decision_intent)
                self._event_handler_registered = False
                self.logger.info("事件订阅已取消")
            except Exception as e:
                self.logger.error(f"取消事件订阅失败: {e}", exc_info=True)

        self._is_setup = False
        self.logger.info("输出Handler管理器清理完成")

    async def _on_decision_intent(self, event_name: str, payload: "IntentPayload", source: str):
        """处理Intent事件（Decision 阶段 → Output 阶段，类型化）"""
        intent = payload.to_intent()

        action_type = intent.action if intent.action else "none"
        self.logger.debug(
            f'收到Intent事件: {event_name}, 响应: "{intent.speech[:50] if intent.speech else ""}...", 动作: {action_type}'
        )

        try:
            if self.pipeline_manager:
                intent = await self.pipeline_manager.process(intent)
                if intent is None:
                    self.logger.debug("Intent 被 Pipeline 丢弃，取消本次输出")
                    return
                self.logger.debug("OutputPipeline 处理完成")

            output_payload = IntentPayload.from_intent(intent, payload.name)
            await self.event_bus.emit(
                CoreEvents.OUTPUT_INTENT_READY,
                output_payload,
                source="OutputHandlerManager",
            )
            self.logger.debug(f"已发布事件: {CoreEvents.OUTPUT_INTENT_READY}")

        except Exception as e:
            self.logger.error(f"处理Intent事件时出错: {e}", exc_info=True)

    async def register_handler(self, handler: Any, handler_name: str):
        """注册Handler"""
        self.handlers.append(handler)
        self._handler_names[handler] = handler_name
        self.logger.info(f"Handler已注册: {handler_name}")

    async def _start_all_handlers(self) -> None:
        """启动所有Handler"""
        self.logger.info(f"正在启动 {len(self.handlers)} 个Handler...")

        if self.concurrent_rendering:
            setup_tasks = []
            for handler in self.handlers:
                setup_tasks.append(handler.init())

            results = await asyncio.gather(*setup_tasks, return_exceptions=True)
            for handler, result in zip(self.handlers, results, strict=False):
                if isinstance(result, Exception):
                    self._handler_started[handler] = False
                    self.logger.error(f"Handler启动异常: {self._handler_names.get(handler, 'unknown')} - {result}")
                else:
                    self._handler_started[handler] = True
        else:
            for handler in self.handlers:
                try:
                    await handler.init()
                    self._handler_started[handler] = True
                except Exception as e:
                    self._handler_started[handler] = False
                    self.logger.error(f"Handler启动失败: {self._handler_names.get(handler, 'unknown')} - {e}")

        all_setup = all(self._handler_started.get(h, False) for h in self.handlers)
        if all_setup:
            self.logger.info(f"所有 {len(self.handlers)} 个Handler已启动")
        else:
            setup_count = sum(1 for h in self.handlers if self._handler_started.get(h, False))
            self.logger.warning(f"部分Handler启动失败: {setup_count}/{len(self.handlers)}")

    async def _stop_all_handlers(self):
        """停止所有Handler"""
        self.logger.info(f"正在停止 {len(self.handlers)} 个Handler...")

        for handler in self.handlers:
            if self._handler_started.get(handler, False):
                try:
                    await handler.cleanup()
                except Exception as e:
                    self.logger.error(f"Handler停止失败: {self._handler_names.get(handler, 'unknown')} - {e}")

        self.logger.info("所有Handler已停止")

    def get_handler_names(self) -> list[str]:
        """获取所有Handler名称"""
        return [self._handler_names.get(h, "unknown") for h in self.handlers]

    def get_handler_by_name(self, name: str) -> Any | None:
        """根据名称获取Handler"""
        for handler in self.handlers:
            if self._handler_names.get(handler, "unknown") == name:
                return handler
        return None

    def get_handlers(self) -> list:
        """
        获取所有已注册的 Handler 实例。

        Returns:
            Handler 实例列表
        """
        return self.handlers.copy()

    def get_handler_status(self) -> list[dict[str, Any]]:
        """
        获取所有 Handler 的状态信息。

        用于 Dashboard 等外部组件查询，避免直接访问私有属性。

        Returns:
            包含 name, is_started, config 的字典列表
        """
        result = []
        for handler in self.handlers:
            handler_name = self._handler_names.get(handler, "unknown")
            result.append(
                {
                    "name": handler_name,
                    "is_started": self._handler_started.get(handler, False),
                    "config": getattr(handler, "config", None),
                }
            )
        return result

    async def load_from_config(self, config: dict[str, Any], config_service=None):
        """从配置加载并创建所有OutputHandler（支持二级配置合并）"""
        self.logger.info("开始从配置加载OutputHandler...")

        try:
            from src.stages.output import handlers  # noqa: F401

            self.logger.debug("已导入 handlers 包，所有 Handler 应已注册")
        except ImportError as e:
            self.logger.warning(f"导入 handlers 包失败: {e}")

        enabled = config.get("enabled", True)
        if not enabled:
            self.logger.info("输出Handler层已禁用（enabled=false）")
            return

        self.concurrent_rendering = config.get("concurrent_rendering", True)
        self.error_handling = config.get("error_handling", "continue")
        self.render_timeout = float(config.get("render_timeout", 10.0))

        self.logger.info(
            f"输出Handler管理器配置: "
            f"concurrent={self.concurrent_rendering}, "
            f"error_handling={self.error_handling}, "
            f"timeout={self.render_timeout}s"
        )

        enabled_handlers = config.get("enabled", [])
        if not enabled_handlers:
            self.logger.warning("未配置任何输出Handler（enabled为空）")
            return

        self.logger.info(f"配置了 {len(enabled_handlers)} 个输出Handler: {enabled_handlers}")

        created_count = 0
        failed_count = 0

        for output_name in enabled_handlers:
            try:
                try:
                    from src.modules.config.schemas import get_config_schema

                    schema_class = get_config_schema(output_name, "output")
                except KeyError:
                    self.logger.debug(
                        f"Handler '{output_name}' Schema未注册（模块可能未导入），将使用无Schema方式加载配置"
                    )
                    schema_class = None

                if config_service is None:
                    raise RuntimeError("config_service 未提供，无法加载 Handler 配置")

                handler_config = config_service.get_config_with_defaults(
                    name=output_name,
                    phase="output",
                    schema_class=schema_class,
                )

                handler_type = handler_config.get("type", output_name)

                handler = self._create_handler(handler_type, handler_config)
                if handler:
                    await self.register_handler(handler, output_name)
                    created_count += 1
                else:
                    self.logger.error(f"Handler创建失败: {output_name} (type={handler_type})")
                    failed_count += 1
            except Exception as e:
                self.logger.error(f"Handler创建异常: {output_name} - {e}", exc_info=True)
                failed_count += 1

        self.logger.info(
            f"OutputHandler加载完成: 成功={created_count}/{len(enabled_handlers)}, "
            f"失败={failed_count}/{len(enabled_handlers)}"
        )

    def _create_handler(self, handler_type: str, config: dict[str, Any]) -> Any | None:
        """Handler工厂方法：根据类型创建Handler实例"""
        if handler_type not in _HANDLERS:
            available = list(_HANDLERS.keys())
            self.logger.error(f"未知的Handler类型: '{handler_type}'. 可用的Handler: {available or '无'}")
            return None

        try:
            handler_cls = _HANDLERS[handler_type]

            # 根据Handler类型决定是否传递audio_stream_channel
            is_audio_handler = handler_type in AUDIO_HANDLERS

            # 检查 handler 是否接受 handler_manager 参数
            sig = inspect.signature(handler_cls.__init__)
            accepts_handler_manager = "handler_manager" in sig.parameters

            kwargs: dict[str, Any] = {"config": config, "event_bus": self.event_bus}

            if is_audio_handler and self._audio_stream_channel:
                kwargs["audio_stream_channel"] = self._audio_stream_channel

            if accepts_handler_manager:
                kwargs["handler_manager"] = self

            handler = handler_cls(**kwargs)

            self.logger.info(f"Handler创建成功: {handler_type}")
            return handler

        except Exception as e:
            self.logger.error(f"Handler实例化失败: {handler_type} - {e}", exc_info=True)
            return None

    def get_stats(self) -> dict[str, Any]:
        """获取管理器的统计信息"""
        stats: dict[str, Any] = {
            "total_handlers": len(self.handlers),
            "setup_handlers": sum(1 for h in self.handlers if self._handler_started.get(h, False)),
            "concurrent_rendering": self.concurrent_rendering,
            "error_handling": self.error_handling,
            "handler_stats": {},
        }

        handler_count = {}
        for handler in self.handlers:
            handler_name = self._handler_names.get(handler, "unknown")
            if handler_name not in stats["handler_stats"]:
                if handler_name in handler_count:
                    handler_count[handler_name] += 1
                    numbered_name = f"{handler_name}_{handler_count[handler_name]}"
                else:
                    handler_count[handler_name] = 0
                    numbered_name = handler_name

                stats["handler_stats"][numbered_name] = {
                    "is_started": self._handler_started.get(handler, False),
                    "type": "output_handler",
                }
            else:
                handler_count[handler_name] = handler_count.get(handler_name, 0) + 1
                numbered_name = f"{handler_name}_{handler_count[handler_name]}"
                stats["handler_stats"][numbered_name] = {
                    "is_started": self._handler_started.get(handler, False),
                    "type": "output_handler",
                }

        return stats
