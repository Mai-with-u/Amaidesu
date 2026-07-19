"""
OutputHandlerManager - Output 阶段: 输出Handler管理器

职责:
- 协调 Decision 阶段 → Output 阶段 的数据流
- 订阅 DECISION_INTENT_GENERATED 事件，通过 OutputPipeline 处理后发布 OUTPUT_INTENT_DISPATCHED 事件
- 管理多个 OutputHandler
- 支持并发渲染
- 错误隔离（单个Handler失败不影响其他）
- 超时控制（防止单个Handler阻塞）
- 生命周期管理（启动、停止、清理）
- 从配置加载Handler
- Pipeline 集成（OutputPipeline）
- **两层事件聚合**：订阅 per-handler 完成事件 (OUTPUT_HANDLER_COMPLETED)，
  当同一 intent 的所有 active handler 都报告完成时再聚合发 OUTPUT_INTENT_FINISHED

数据流（3 阶段架构）:
    Intent (Decision) → OutputHandlerManager → OutputPipeline 过滤
                     → OUTPUT_INTENT_DISPATCHED 事件 (广播给所有 active handler)
                     → 每个 handler 处理完 emit OUTPUT_HANDLER_COMPLETED (事件流 1)
                     → Manager 聚合 → OUTPUT_INTENT_FINISHED (事件流 2)

注意:
- OutputHandlerManager 负责过滤 Intent 并分发
- 所有 OutputHandler 订阅 OUTPUT_INTENT_DISPATCHED 事件
- 所有 OutputHandler 必须在 handle() 末尾 emit OUTPUT_HANDLER_COMPLETED (推荐放 finally)
"""

import asyncio
from typing import Any, Optional

from pydantic import BaseModel

from src.modules.di import instantiate_with_di
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.decision import IntentPayload
from src.modules.events.payloads.output import OutputHandlerCompletedPayload
from src.modules.llm.manager import LLMManager
from src.modules.logging import get_logger
from src.modules.pipeline import PipelineManager
from src.modules.prompts.manager import PromptManager
from src.modules.streaming.audio_stream_channel import AudioStreamChannel
from src.modules.tts.audio_device_manager import AudioDeviceManager
from src.stages.output.registry import _HANDLERS, SupportsCapabilities
from src.modules.types.capabilities import UnifiedActionEntry, UnifiedCapabilitiesView


class RenderResult(BaseModel):
    """渲染结果"""

    name: str
    success: bool
    error: str | None = None
    timeout: bool = False
    duration: float = 0.0


class OutputHandlerManager:
    """
    输出Handler管理器

    核心职责:
    - 协调 Decision 阶段 → Output 阶段 的数据流
    - 订阅 DECISION_INTENT_GENERATED 事件，通过 OutputPipeline 过滤后发布 OUTPUT_INTENT_DISPATCHED 事件
    - 管理多个 OutputHandler 实例
    - 并发渲染到所有 Handler
    - 错误隔离（单个 Handler 失败不影响其他）
    - 超时控制（防止单个 Handler 阻塞）
    - 生命周期管理
    - Pipeline 集成
    - **两层事件聚合**：订阅 OUTPUT_HANDLER_COMPLETED 事件，
      维护 intent_id → expected_handlers 映射，等齐后发 OUTPUT_INTENT_FINISHED,
      并带 watchdog 超时兜底（避免某个 handler 漏发导致 FINISHED 永远不发）
    """

    def __init__(
        self,
        event_bus: EventBus,
        pipeline_manager: PipelineManager,
        config: Optional[dict[str, Any]] = None,
        llm_manager: Optional[LLMManager] = None,
        prompt_manager: Optional[PromptManager] = None,
        audio_device_manager: Optional[AudioDeviceManager] = None,
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

        self.concurrent_rendering = self.config.get("concurrent_rendering", True)
        self.error_handling = self.config.get("error_handling", "continue")
        self.render_timeout_ms = int(self.config.get("render_timeout_ms", 10000))
        self.completion_timeout_ms = int(self.config.get("completion_timeout_ms", 30000))

        self.pipeline_manager = pipeline_manager

        self._is_setup = False
        self._event_handler_registered = False
        self._completion_handler_registered = False
        self._audio_stream_channel = None

        # === 两层事件聚合状态（per-intent tracking） ===
        # key: intent_id, value: 剩余期望完成的 handler 名字集合
        self._pending_intents: dict[str, set[str]] = {}
        # 每个 intent 对应一份 IntentPayload,用于聚合完成时 emit FINISHED
        self._pending_intent_payloads: dict[str, IntentPayload] = {}
        # watchdog task,key=intent_id,用于超时兜底
        self._pending_timeouts: dict[str, asyncio.Task] = {}
        # 异步锁保护上述 4 个集合(emit 是 fire-and-forget,可能并发)
        self._pending_lock = asyncio.Lock()

        self.logger.info(
            f"OutputHandlerManager初始化完成 "
            f"(concurrent={self.concurrent_rendering}, "
            f"error_handling={self.error_handling}, "
            f"timeout={self.render_timeout_ms}ms, "
            f"completion_timeout={self.completion_timeout_ms}ms)"
        )

    async def setup(
        self,
        config: dict[str, Any],
        config_service=None,
        audio_stream_channel: Optional[AudioStreamChannel] = None,
        llm_manager=None,
        prompt_manager=None,
        audio_device_manager=None,
    ) -> None:
        self.logger.info("开始设置输出Handler管理器...")

        self._audio_stream_channel = audio_stream_channel

        self._llm_service = llm_manager or self._llm_service
        self._prompt_service = prompt_manager or self._prompt_service
        self._audio_device_service = audio_device_manager or self._audio_device_service

        await self.load_from_config(config, config_service=config_service)

        self.event_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            self._on_decision_intent,
            model_class=IntentPayload,
            priority=50,
        )
        self._event_handler_registered = True
        self.logger.info(f"已订阅 '{CoreEvents.DECISION_INTENT_GENERATED}' 事件（类型化）")

        # 订阅 per-handler 完成事件,作为两层事件模式的聚合者
        if not self._completion_handler_registered:
            self.event_bus.on(
                CoreEvents.OUTPUT_HANDLER_COMPLETED,
                self._on_handler_completed,
                model_class=OutputHandlerCompletedPayload,
                priority=50,
            )
            self._completion_handler_registered = True
            self.logger.info(f"已订阅 '{CoreEvents.OUTPUT_HANDLER_COMPLETED}' 事件(聚合者)")

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

        if self._completion_handler_registered:
            try:
                self.event_bus.off(CoreEvents.OUTPUT_HANDLER_COMPLETED, self._on_handler_completed)
                self._completion_handler_registered = False
                self.logger.info("聚合事件订阅已取消")
            except Exception as e:
                self.logger.error(f"取消聚合事件订阅失败: {e}", exc_info=True)

        # 取消所有 pending watchdog,避免关闭后还有 task 在跑
        for task in list(self._pending_timeouts.values()):
            if not task.done():
                task.cancel()
        if self._pending_timeouts:
            await asyncio.gather(*self._pending_timeouts.values(), return_exceptions=True)
        self._pending_intents.clear()
        self._pending_intent_payloads.clear()
        self._pending_timeouts.clear()

        self._is_setup = False
        self.logger.info("输出Handler管理器清理完成")

    async def _on_decision_intent(self, event_name: str, payload: IntentPayload, source: str):
        """处理Intent事件（Decision 阶段 → Output 阶段，类型化）"""
        intent = payload.to_intent()

        # Decision → Output 的单一汇聚点：每个被发布的意图在此处打印一次摘要
        extra = ""
        if intent.action is not None:
            extra += f" → {intent.action.name}"
        if intent.emotion is not None:
            extra += f" ({intent.emotion.name})"
        self.logger.info(f"[{payload.name}] {intent.speech or ''}{extra}")

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

            # 用当前活跃 handler 集合初始化 pending 跟踪(intent_id 为空兜底生成)
            intent_id = intent.metadata.intent_id
            # 用类名(`type(h).__name__`)而非注册名,与 handler emit COMPLETED 时的命名一致
            active_handler_names = [type(h).__name__ for h in self.handlers if self._handler_started.get(h, False)]
            await self._register_pending_intent(intent_id, output_payload, set(active_handler_names))

            # 1) 广播 DISPATCHED → 所有 handler 会通过订阅开始干活
            await self.event_bus.emit(
                CoreEvents.OUTPUT_INTENT_DISPATCHED,
                output_payload,
                source="OutputHandlerManager",
            )
            self.logger.debug(f"已发布事件: {CoreEvents.OUTPUT_INTENT_DISPATCHED}")

            # 2) 不再立刻发 FINISHED;改由 _on_handler_completed 聚合触发
            #    如果没有任何 active handler,直接发 FINISHED (避免悬挂待跟踪意图)
            if not active_handler_names:
                self.logger.debug(f"无 active handler,直接发 FINISHED: intent_id={intent_id}")
                await self._finalize_intent(intent_id)

        except Exception as e:
            self.logger.error(f"处理Intent事件时出错: {e}", exc_info=True)

    async def _register_pending_intent(
        self,
        intent_id: str,
        payload: IntentPayload,
        expected_handlers: set[str],
    ) -> None:
        """注册一个新的待完成 intent(线程安全)。

        同时启动 watchdog 超时兜底,防止某个 handler 漏发 COMPLETED 导致 FINISHED 永远不发。
        """
        async with self._pending_lock:
            self._pending_intents[intent_id] = set(expected_handlers)
            self._pending_intent_payloads[intent_id] = payload

            # 取消旧的 watchdog (同一 intent_id 重复场景罕见但要防呆)
            old_task = self._pending_timeouts.pop(intent_id, None)
            if old_task is not None and not old_task.done():
                old_task.cancel()

            # 启动新的 watchdog
            if expected_handlers and self.completion_timeout_ms > 0:
                task = asyncio.create_task(self._watchdog_intent(intent_id))
                self._pending_timeouts[intent_id] = task

        self.logger.debug(f"注册 pending intent: id={intent_id}, expected={sorted(expected_handlers)}")

    async def _watchdog_intent(self, intent_id: str) -> None:
        """Watchdog: 超过 completion_timeout_ms 后强制 finalize 未完成的 intent。

        调用 _on_handler_completed 完成等价的清理路径,保证 FINISHED 一定会发出。
        """
        try:
            await asyncio.sleep(self.completion_timeout_ms / 1000.0)
            async with self._pending_lock:
                remaining = self._pending_intents.get(intent_id)
                if remaining is None:
                    return  # 已完成
                self.logger.warning(
                    f"completion watchdog 超时: intent_id={intent_id}, "
                    f"超时={self.completion_timeout_ms}ms, "
                    f"remaining={sorted(remaining)}"
                )
                # 强制清空,触发 finalize
                self._pending_intents[intent_id] = set()
            await self._finalize_intent(intent_id)
        except asyncio.CancelledError:
            # 被 _register_pending_intent 取消或 _finalize_intent 清理了,正常情况
            pass
        except Exception as e:
            self.logger.error(f"watchdog 异常 (intent_id={intent_id}): {e}", exc_info=True)

    async def _on_handler_completed(
        self,
        event_name: str,
        payload: OutputHandlerCompletedPayload,
        source: str,
    ) -> None:
        """聚合者回调:每个 handler 完成都会触发此回调。

        从 expected set 里移除该 handler_name;若 set 变空,触发 FINISHED。
        """
        async with self._pending_lock:
            remaining = self._pending_intents.get(payload.intent_id)
            if remaining is None:
                # 未知 intent_id (例如重复注册或 idle 期间的迟到事件);静默忽略
                self.logger.debug(
                    f"未知 intent_id,忽略 COMPLETED: id={payload.intent_id}, handler={payload.handler_name}"
                )
                return

            if payload.handler_name in remaining:
                remaining.discard(payload.handler_name)
            else:
                # 该 handler 不在 expected set 里(可能 handler 漏订阅/未启动)
                self.logger.warning(
                    f"handler_name={payload.handler_name} 不在 expected set "
                    f"for intent_id={payload.intent_id} (remaining={sorted(remaining)})"
                )

            self.logger.debug(
                f"handler 完成: intent_id={payload.intent_id}, "
                f"handler={payload.handler_name}, "
                f"remaining={len(remaining)}"
            )

            if remaining:
                return
            # 变空,继续外层 finalize

        # 锁外做 finalize 避免持锁 emit 导致死锁
        await self._finalize_intent(payload.intent_id)

    async def _finalize_intent(self, intent_id: str) -> None:
        """发出 OUTPUT_INTENT_FINISHED 并清理 pending 跟踪状态。"""
        async with self._pending_lock:
            payload = self._pending_intent_payloads.pop(intent_id, None)
            self._pending_intents.pop(intent_id, None)
            timeout_task = self._pending_timeouts.pop(intent_id, None)

        if timeout_task is not None and not timeout_task.done():
            timeout_task.cancel()

        if payload is None:
            # 已经被 finalize 过了 (重复路径) 或 未知 id
            self.logger.debug(f"_finalize_intent: 无 payload for intent_id={intent_id}")
            return

        try:
            await self.event_bus.emit(
                CoreEvents.OUTPUT_INTENT_FINISHED,
                payload,
                source="OutputHandlerManager",
            )
            self.logger.debug(f"已聚合发出事件: {CoreEvents.OUTPUT_INTENT_FINISHED} (intent_id={intent_id})")
        except Exception as e:
            self.logger.error(f"发出 FINISHED 失败 (intent_id={intent_id}): {e}", exc_info=True)

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

    def get_all_capabilities(self) -> UnifiedCapabilitiesView:
        """汇总所有支持 `SupportsCapabilities` 的 handler 的 action 列表。

        - 自动给 action name 加 `f"{handler_name}.{local_name}"` 前缀(handler 不感知前缀)
        - 不支持 `SupportsCapabilities` 的 handler 静默跳过
        - handler `get_capabilities()` 抛异常时,warn + 跳过(不传播)
        - 不缓存(用户决定无缓存)
        """
        unified_actions: list[UnifiedActionEntry] = []
        for handler in self.handlers:
            handler_name = self._handler_names.get(handler, "unknown")
            if not isinstance(handler, SupportsCapabilities):
                continue
            try:
                caps = handler.get_capabilities()
            except Exception as e:
                self.logger.warning(f"Handler '{handler_name}' get_capabilities failed: {e}")
                continue
            for action_spec in caps.actions:
                unified_actions.append(
                    UnifiedActionEntry(
                        name=f"{handler_name}.{action_spec.name}",
                        description=action_spec.description,
                        parameters=action_spec.parameters.copy(),
                    )
                )
        return UnifiedCapabilitiesView(actions=unified_actions)

    def get_component_summaries(self) -> list[dict[str, Any]]:
        """Dashboard 协议接口：返回 Output 阶段参与者状态摘要字典列表"""
        return [
            {
                "name": s["name"],
                "phase": "output",
                "type": "handler",
                "is_started": s["is_started"],
                "is_enabled": True,
                "config": s.get("config"),
            }
            for s in self.get_handler_status()
        ]

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
        self.render_timeout_ms = int(config.get("render_timeout_ms", 10000))
        self.completion_timeout_ms = int(config.get("completion_timeout_ms", 30000))

        self.logger.info(
            f"输出Handler管理器配置: "
            f"concurrent={self.concurrent_rendering}, "
            f"error_handling={self.error_handling}, "
            f"timeout={self.render_timeout_ms}ms, "
            f"completion_timeout={self.completion_timeout_ms}ms"
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
        """Handler工厂方法：根据类型创建Handler实例

        使用类型匹配 DI 自动注入 Handler 声明的依赖（AudioStreamChannel 等）。
        """
        if handler_type not in _HANDLERS:
            available = list(_HANDLERS.keys())
            self.logger.error(f"未知的Handler类型: '{handler_type}'. 可用的Handler: {available or '无'}")
            return None

        try:
            handler_cls = _HANDLERS[handler_type]

            services_by_type: dict[type, Any] = {EventBus: self.event_bus}
            if self._audio_stream_channel is not None:
                services_by_type[AudioStreamChannel] = self._audio_stream_channel

            handler = instantiate_with_di(
                handler_cls,
                config=config,
                services_by_type=services_by_type,
            )

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
