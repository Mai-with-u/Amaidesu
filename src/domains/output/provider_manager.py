"""
OutputProviderManager - Output Domain: 输出Provider管理器（重构后）

职责:
- 协调 Decision Domain → Output Domain 的数据流
- 订阅 DECISION_INTENT 事件，通过 OutputPipeline 处理后发布 OUTPUT_INTENT 事件
- 管理多个 OutputProvider
- 支持并发渲染
- 错误隔离（单个Provider失败不影响其他）
- 超时控制（防止单个Provider阻塞）
- 生命周期管理（启动、停止、清理）
- 从配置加载Provider
- Pipeline 集成（OutputPipeline）

数据流（3域架构 - 重构后）:
    Intent (Decision) → OutputProviderManager → OutputPipeline 过滤 → OUTPUT_INTENT 事件
                     → Output Providers (TTS/Subtitle/Avatar/Sticker 等)

注意:
- OutputProviderManager 负责过滤 Intent 并分发
- 所有 OutputProvider 订阅 OUTPUT_INTENT 事件
"""

import asyncio
import os
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel

from src.domains.output.pipelines.manager import OutputPipelineManager
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.events.payloads import IntentPayload


class RenderResult(BaseModel):
    """
    渲染结果

    Attributes:
        provider_name: Provider名称
        success: 是否成功
        error: 错误信息（如果失败）
        timeout: 是否超时
        duration: 渲染耗时（秒）
    """

    provider_name: str
    success: bool
    error: str | None = None
    timeout: bool = False
    duration: float = 0.0


class OutputProviderManager:
    """
    输出Provider管理器（重构后）

    核心职责:
    - 协调 Decision Domain → Output Domain 的数据流
    - 订阅 DECISION_INTENT 事件，通过 OutputPipeline 过滤后发布 OUTPUT_INTENT 事件
    - 管理多个 OutputProvider 实例
    - 并发渲染到所有 Provider
    - 错误隔离（单个 Provider 失败不影响其他）
    - 超时控制（防止单个 Provider 阻塞）
    - 生命周期管理
    - Pipeline 集成
    """

    def __init__(self, event_bus: EventBus, config: dict[str, Any] = None):
        """
        初始化 Provider 管理器

        Args:
            event_bus: 事件总线实例
            config: 配置字典，支持:
                - concurrent_rendering: bool = True  # 是否并发渲染
                - error_handling: str = "continue"  # 错误处理策略: continue/stop/drop
                - render_timeout: float = 10.0  # 单个Provider渲染超时（秒）
        """
        self.event_bus = event_bus
        self.config = config or {}
        self.providers: list[OutputProvider] = []
        self.logger = get_logger("OutputProviderManager")

        # 是否并发渲染（默认True）
        self.concurrent_rendering = self.config.get("concurrent_rendering", True)

        # 错误处理模式（continue/stop/drop）
        self.error_handling = self.config.get("error_handling", "continue")

        # 渲染超时时间（秒），0表示不限制
        self.render_timeout = float(self.config.get("render_timeout", 10.0))

        # Pipeline 管理器
        self.pipeline_manager: Optional[OutputPipelineManager] = None

        # 状态标记
        self._is_setup = False
        self._event_handler_registered = False
        self._audio_stream_channel = None

        self.logger.info(
            f"OutputProviderManager初始化完成 "
            f"(concurrent={self.concurrent_rendering}, "
            f"error_handling={self.error_handling}, "
            f"timeout={self.render_timeout}s)"
        )

    # ==================== 生命周期管理 ====================

    async def setup(
        self,
        config: dict[str, Any],
        config_service=None,
        root_config: Optional[dict[str, Any]] = None,
        audio_stream_channel=None,
    ) -> None:
        """
        设置输出Provider管理器

        Args:
            config: 输出Provider配置（来自[providers.output]）
            config_service: ConfigService实例（用于二级配置加载）
            root_config: 根配置字典（包含 pipelines 配置）
            audio_stream_channel: AudioStreamChannel 实例
        """
        self.logger.info("开始设置输出Provider管理器...")

        # 保存 AudioStreamChannel 引用
        self._audio_stream_channel = audio_stream_channel

        # 创建输出Pipeline管理器
        self.pipeline_manager = OutputPipelineManager()
        self.logger.info("输出Pipeline管理器已创建")

        # 从配置加载输出Pipeline
        pipeline_config = root_config.get("pipelines", {}) if root_config else {}
        if pipeline_config:
            # 构建管道加载目录路径：src/domains/output/pipelines
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

        # 从配置加载Provider
        await self.load_from_config(config, config_service=config_service)

        # 订阅 Decision Domain 的 Intent 事件（3域架构，类型化）
        from src.modules.events.payloads.decision import IntentPayload

        self.event_bus.on(
            CoreEvents.DECISION_INTENT,
            self._on_decision_intent,
            model_class=IntentPayload,
            priority=50,
        )
        self._event_handler_registered = True
        self.logger.info(f"已订阅 '{CoreEvents.DECISION_INTENT}' 事件（类型化）")

        self._is_setup = True
        self.logger.info("输出Provider管理器设置完成")

    async def start(self):
        """启动输出Provider管理器"""
        if not self._is_setup:
            self.logger.warning("OutputProviderManager 未设置，无法启动")
            return

        self.logger.info("启动输出Provider管理器...")

        # 启动 OutputProvider，注入 AudioStreamChannel
        try:
            await self.setup_all_providers(self.event_bus, audio_stream_channel=self._audio_stream_channel)
            self.logger.info("OutputProvider 已启动")
        except Exception as e:
            self.logger.error(f"启动 OutputProvider 失败: {e}", exc_info=True)

    async def stop(self):
        """停止输出Provider管理器"""
        self.logger.info("停止输出Provider管理器...")

        # 停止 OutputProvider
        try:
            await self.stop_all_providers()
            self.logger.info("OutputProvider 已停止")
        except Exception as e:
            self.logger.error(f"停止 OutputProvider 失败: {e}", exc_info=True)

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理输出Provider管理器...")

        # 取消事件订阅
        if self._event_handler_registered:
            try:
                self.event_bus.off(CoreEvents.DECISION_INTENT, self._on_decision_intent)
                self._event_handler_registered = False
                self.logger.info("事件订阅已取消")
            except Exception as e:
                self.logger.error(f"取消事件订阅失败: {e}", exc_info=True)

        self._is_setup = False
        self.logger.info("输出Provider管理器清理完成")

    async def _on_decision_intent(self, event_name: str, payload: "IntentPayload", source: str):
        """
        处理Intent事件（Decision Domain → Output Domain，类型化）

        数据流（重构后）:
            IntentPayload → Intent → OutputPipeline 过滤 → 发布 OUTPUT_INTENT 事件 → Output Providers

        Args:
            event_name: 事件名称（decision.intent）
            payload: 类型化的事件数据（IntentPayload 对象，自动反序列化）
            source: 事件源
        """
        # 使用 IntentPayload.to_intent() 方法转换为 Intent 对象
        intent = payload.to_intent()

        # 获取第一个动作的类型（如果有）
        # 注意：由于 IntentAction 使用了 use_enum_values=True，type 已经是字符串
        action_type = intent.actions[0].type if intent.actions else "none"
        self.logger.info(f'收到Intent事件: {event_name}, 响应: "{intent.response_text[:50]}...", 动作: {action_type}')

        try:
            # OutputPipeline 处理（Intent 过滤）
            if self.pipeline_manager:
                intent = await self.pipeline_manager.process(intent)
                if intent is None:  # 被管道丢弃
                    self.logger.info("Intent 被 Pipeline 丢弃，取消本次输出")
                    return
                self.logger.debug("OutputPipeline 处理完成")

            # 发布 OUTPUT_INTENT 事件（事件驱动）
            # 所有 Output Provider 订阅此事件并响应
            # 使用处理后的 Intent 创建新的 IntentPayload
            from src.modules.events.payloads.decision import IntentPayload

            output_payload = IntentPayload.from_intent(intent, payload.provider)
            await self.event_bus.emit(
                CoreEvents.OUTPUT_INTENT,
                output_payload,
                source="OutputProviderManager",
            )
            self.logger.debug(f"已发布事件: {CoreEvents.OUTPUT_INTENT}")

        except Exception as e:
            self.logger.error(f"处理Intent事件时出错: {e}", exc_info=True)

    # ==================== Provider 管理 ====================

    async def register_provider(self, provider: OutputProvider):
        """
        注册Provider

        Args:
            provider: OutputProvider实例
        """
        self.providers.append(provider)
        self.logger.info(f"Provider已注册: {provider.get_info()['name']}")

    async def setup_all_providers(
        self,
        event_bus,
        audio_stream_channel=None,
    ) -> None:
        """
        启动所有Provider

        Args:
            event_bus: EventBus实例
            audio_stream_channel: 可选的AudioStreamChannel实例
        """
        self.logger.info(f"正在启动 {len(self.providers)} 个Provider...")

        if self.concurrent_rendering:
            # 并发启动所有Provider
            setup_tasks = []
            for provider in self.providers:
                # 传递 audio_stream_channel 给每个 Provider
                setup_tasks.append(provider.start(event_bus, audio_stream_channel=audio_stream_channel))

            await asyncio.gather(*setup_tasks, return_exceptions=True)
        else:
            # 串行启动（用于调试）
            for provider in self.providers:
                try:
                    # 传递 audio_stream_channel 给每个 Provider
                    await provider.start(event_bus, audio_stream_channel=audio_stream_channel)
                except Exception as e:
                    self.logger.error(f"Provider启动失败: {provider.get_info()['name']} - {e}")

        # 检查所有Provider是否都已设置
        all_setup = all(provider.is_started for provider in self.providers)
        if all_setup:
            self.logger.info(f"所有 {len(self.providers)} 个Provider已启动")
        else:
            setup_count = sum(1 for p in self.providers if p.is_started)
            self.logger.warning(f"部分Provider启动失败: {setup_count}/{len(self.providers)}")

    async def stop_all_providers(self):
        """
        停止所有Provider
        """
        self.logger.info(f"正在停止 {len(self.providers)} 个Provider...")

        # 先停止渲染
        for provider in self.providers:
            if provider.is_started:
                try:
                    # 调用 stop 方法，它会调用 cleanup 并设置 is_started = False
                    await provider.stop()
                except Exception as e:
                    self.logger.error(f"Provider停止失败: {provider.get_info()['name']} - {e}")

        self.logger.info("所有Provider已停止")

    def get_provider_names(self) -> list[str]:
        """
        获取所有Provider名称

        Returns:
            Provider名称列表
        """
        return [p.get_info()["name"] for p in self.providers]

    def get_provider_by_name(self, name: str) -> OutputProvider | None:
        """
        根据名称获取Provider

        Args:
            name: Provider名称

        Returns:
            Provider实例，如果未找到返回None
        """
        for provider in self.providers:
            if provider.get_info()["name"] == name:
                return provider
        return None

    # ==================== Phase 4: 配置加载 ====================

    async def load_from_config(self, config: dict[str, Any], config_service=None):
        """
        从配置加载并创建所有OutputProvider（支持二级配置合并）

        Args:
            config: 输出Provider配置（来自[providers.output]）
            config_service: ConfigService实例（用于二级配置加载）

        配置格式:
            [providers.output]
            enabled = true
            enabled_outputs = ["subtitle", "vts", "tts"]

            # 可选：常用参数覆盖
            [providers.output.subtitle]
            font_size = 24
        """
        self.logger.info("开始从配置加载OutputProvider...")

        # 确保所有 Provider 模块已导入（触发 Schema 注册）
        # 导入 providers 包会执行 __init__.py，注册所有 Provider
        try:
            from src.domains.output import providers  # noqa: F401

            self.logger.debug("已导入 providers 包，所有 Provider 应已注册")
        except ImportError as e:
            self.logger.warning(f"导入 providers 包失败: {e}")

        # 检查是否启用
        enabled = config.get("enabled", True)
        if not enabled:
            self.logger.info("输出Provider层已禁用（enabled=false）")
            return

        # 更新管理器配置
        self.concurrent_rendering = config.get("concurrent_rendering", True)
        self.error_handling = config.get("error_handling", "continue")
        self.render_timeout = float(config.get("render_timeout", 10.0))

        self.logger.info(
            f"输出Provider管理器配置: "
            f"concurrent={self.concurrent_rendering}, "
            f"error_handling={self.error_handling}, "
            f"timeout={self.render_timeout}s"
        )

        # 获取Provider列表
        enabled_outputs = config.get("enabled_outputs", [])
        if not enabled_outputs:
            self.logger.warning("未配置任何输出Provider（enabled_outputs为空）")
            return

        self.logger.info(f"配置了 {len(enabled_outputs)} 个输出Provider: {enabled_outputs}")

        # 创建Provider实例
        created_count = 0
        failed_count = 0

        for output_name in enabled_outputs:
            try:
                # 使用二级配置加载
                try:
                    from src.modules.config.schemas import get_provider_schema

                    schema_class = get_provider_schema(output_name, "output")
                except KeyError:
                    # Provider未注册（模块未导入），回退到None
                    self.logger.debug(
                        f"Provider '{output_name}' Schema未注册（模块可能未导入），将使用无Schema方式加载配置"
                    )
                    schema_class = None

                if config_service:
                    provider_config = config_service.get_provider_config_with_defaults(
                        provider_name=output_name,
                        provider_layer="output",
                        schema_class=schema_class,
                    )
                else:
                    # 从配置中读取（测试场景使用 outputs_config 字段）
                    provider_config = config.get("outputs_config", {}).get(output_name, {})

                provider_type = provider_config.get("type", output_name)

                # 创建Provider（不再检查enabled字段，由enabled_outputs控制）
                provider = self._create_provider(provider_type, provider_config)
                if provider:
                    await self.register_provider(provider)
                    created_count += 1
                else:
                    self.logger.error(f"Provider创建失败: {output_name} (type={provider_type})")
                    failed_count += 1
            except Exception as e:
                self.logger.error(f"Provider创建异常: {output_name} - {e}", exc_info=True)
                failed_count += 1

        self.logger.info(
            f"OutputProvider加载完成: 成功={created_count}/{len(enabled_outputs)}, "
            f"失败={failed_count}/{len(enabled_outputs)}"
        )

    def _create_provider(self, provider_type: str, config: dict[str, Any]) -> OutputProvider | None:
        """
        Provider工厂方法：根据类型创建Provider实例

        使用 ProviderRegistry 来创建 Provider。

        Args:
            provider_type: Provider类型（"tts", "subtitle", "sticker", "vts", "omni_tts", "avatar"等）
            config: Provider配置

        Returns:
            Provider实例，如果创建失败返回None
        """
        from src.modules.registry import ProviderRegistry

        # 检查 Provider 是否已注册
        if not ProviderRegistry.is_output_provider_registered(provider_type):
            available = ", ".join(ProviderRegistry.get_registered_output_providers())
            self.logger.error(f"未知的Provider类型: '{provider_type}'. 可用的Provider: {available or '无'}")
            return None

        try:
            # 使用 ProviderRegistry 创建 Provider 实例
            provider = ProviderRegistry.create_output(provider_type, config)

            self.logger.info(f"Provider创建成功: {provider_type}")
            return provider

        except ValueError as e:
            # ProviderRegistry.create_output 抛出的 ValueError
            self.logger.error(f"Provider创建失败: {provider_type} - {e}")
            return None
        except Exception as e:
            self.logger.error(f"Provider实例化失败: {provider_type} - {e}", exc_info=True)
            return None

    def get_stats(self) -> dict[str, Any]:
        """
        获取管理器的统计信息

        Returns:
            包含统计信息的字典：
            - total_providers: 总Provider数量
            - setup_providers: 已启动的Provider数量
            - concurrent_rendering: 是否并发渲染
            - error_handling: 错误处理策略
            - provider_stats: 每个Provider的详细信息
        """
        stats = {
            "total_providers": len(self.providers),
            "setup_providers": sum(1 for p in self.providers if p.is_started),
            "concurrent_rendering": self.concurrent_rendering,
            "error_handling": self.error_handling,
            "provider_stats": {},
        }

        # 收集每个Provider的统计信息
        provider_count = {}
        for provider in self.providers:
            provider_name = provider.get_info()["name"]
            if provider_name not in stats["provider_stats"]:
                # 如果是同名的Provider，使用从0开始的编号
                if provider_name in provider_count:
                    provider_count[provider_name] += 1
                    numbered_name = f"{provider_name}_{provider_count[provider_name]}"
                else:
                    provider_count[provider_name] = 0
                    numbered_name = provider_name

                stats["provider_stats"][numbered_name] = {
                    "is_started": provider.is_started,
                    "type": "output_provider",
                }
            else:
                # 如果已经存在，增加计数器
                provider_count[provider_name] = provider_count.get(provider_name, 0) + 1
                numbered_name = f"{provider_name}_{provider_count[provider_name]}"
                stats["provider_stats"][numbered_name] = {
                    "is_started": provider.is_started,
                    "type": "output_provider",
                }

        return stats
