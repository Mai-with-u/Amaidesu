"""
OutputCoordinator - 数据流协调器（3域架构：Decision Domain → Output Domain）

职责:
- 协调 Decision Domain 到 Output Domain 的数据流
- 订阅 Intent 事件并生成 RenderParameters（用于 TTS/Subtitle）
- 初始化输出层（OutputProviderManager 和 OutputPipelineManager）

新架构事件流:
    Intent (Decision Domain)
        ↓ EventBus: DECISION_INTENT_GENERATED
        ├─────────────────────────────────────────────┐
        ↓                                             ↓
    Avatar Providers (直接订阅)                  OutputCoordinator
    (VTSProvider, WarudoProvider...)                     ↓
        ↓                                         RenderParameters
    平台特定参数                                    (TTS/Subtitle)
    ↓                                              ↓
    平台渲染                                  EXPRESSION_PARAMETERS_GENERATED
                                                ↓
                                            TTS/Subtitle Providers

注意:
- Avatar Provider 直接订阅 DECISION_INTENT_GENERATED，在内部实现 Intent → 平台参数的适配
- OutputCoordinator 只为 TTS/Subtitle 生成 RenderParameters
"""

import os
from typing import Dict, Any, Optional, TYPE_CHECKING
from src.core.utils.logger import get_logger

from src.core.event_bus import EventBus
from src.core.events.names import CoreEvents
from src.domains.output.provider_manager import OutputProviderManager
from src.core.events.payloads import ParametersGeneratedPayload
from src.domains.output.pipelines.manager import OutputPipelineManager
from src.domains.output.parameters.render_parameters import RenderParameters

if TYPE_CHECKING:
    from src.core.events.payloads import IntentPayload


class OutputCoordinator:
    """
    数据流协调器（3域架构）

    核心职责:
    - 协调 Decision Domain → Output Domain 的数据流
    - 初始化和管理输出层组件
    - 订阅并处理 Intent 事件，为 TTS/Subtitle 生成参数

    数据流程（3域架构 - 重构后）:
    Intent (Decision) → Avatar Providers (直接订阅 DECISION_INTENT_GENERATED)
                     → OutputCoordinator → RenderParameters (TTS/Subtitle) → TTS/Subtitle Providers
    """

    def __init__(
        self,
        event_bus: EventBus,
        output_provider_manager: Optional[OutputProviderManager] = None,
        output_pipeline_manager: Optional[OutputPipelineManager] = None,
    ):
        """
        初始化数据流协调器

        Args:
            event_bus: 事件总线实例
            output_provider_manager: (可选) 输出Provider管理器实例
            output_pipeline_manager: (可选) 输出Pipeline管理器实例
        """
        self.event_bus = event_bus
        self.output_provider_manager = output_provider_manager
        self.output_pipeline_manager = output_pipeline_manager
        self.logger = get_logger("OutputCoordinator")

        self._is_setup = False
        self._event_handler_registered = False
        self._audio_stream_channel = None

        self.logger.info("OutputCoordinator 初始化完成")

    async def setup(
        self,
        config: Dict[str, Any],
        config_service=None,
        root_config: Optional[Dict[str, Any]] = None,
        audio_stream_channel=None,
    ) -> None:
        """
        设置数据流协调器

        Args:
            config: 输出Provider配置（来自[providers.output]）
            config_service: ConfigService实例（用于三级配置加载）
            root_config: 根配置字典（包含 pipelines 配置）
            audio_stream_channel: AudioStreamChannel 实例（在 main.py 中创建）
        """
        self.logger.info("开始设置数据流协调器...")

        # 保存 AudioStreamChannel 引用
        self._audio_stream_channel = audio_stream_channel

        # 创建输出Provider管理器（如果未提供）
        if self.output_provider_manager is None:
            self.output_provider_manager = OutputProviderManager(config)
            self.logger.info("输出Provider管理器已创建")

        # 创建输出Pipeline管理器（如果未提供）
        if self.output_pipeline_manager is None:
            self.output_pipeline_manager = OutputPipelineManager()
            self.logger.info("输出Pipeline管理器已创建")

            # 从配置加载输出Pipeline
            pipeline_config = root_config.get("pipelines", {}) if root_config else {}
            if pipeline_config:
                # 构建管道加载目录路径：src/domains/output/pipelines
                pipeline_load_dir = os.path.join(os.path.dirname(__file__), "pipelines")
                pipeline_load_dir = os.path.abspath(pipeline_load_dir)
                self.logger.info(f"准备加载输出Pipeline (从目录: {pipeline_load_dir})...")

                try:
                    await self.output_pipeline_manager.load_output_pipelines(pipeline_load_dir, pipeline_config)
                    pipeline_count = len(self.output_pipeline_manager._pipelines)
                    if pipeline_count > 0:
                        self.logger.info(f"输出Pipeline加载完成，共 {pipeline_count} 个管道。")
                    else:
                        self.logger.info("未找到任何有效的输出Pipeline。")
                except Exception as e:
                    self.logger.error(f"加载输出Pipeline时出错: {e}", exc_info=True)
            else:
                self.logger.info("配置中未启用管道功能")

        # 从配置加载Provider（传递config_service以启用三级配置合并）
        if self.output_provider_manager:
            await self.output_provider_manager.load_from_config(config, core=None, config_service=config_service)

        # 订阅 Decision Domain 的 Intent 事件（3域架构，类型化）
        from src.core.events.payloads.decision import IntentPayload

        self.event_bus.on(
            CoreEvents.DECISION_INTENT_GENERATED,
            self._on_intent_ready,
            model_class=IntentPayload,
            priority=50,
        )
        self._event_handler_registered = True
        self.logger.info(f"已订阅 '{CoreEvents.DECISION_INTENT_GENERATED}' 事件（类型化）")

        self._is_setup = True
        self.logger.info("数据流协调器设置完成")

    async def start(self):
        """启动数据流协调器"""
        if not self._is_setup:
            self.logger.warning("FlowCoordinator 未设置，无法启动")
            return

        self.logger.info("启动数据流协调器...")

        # 启动 OutputProvider，注入 AudioStreamChannel
        if self.output_provider_manager:
            try:
                # 构建 dependencies 字典
                dependencies = {}
                if self._audio_stream_channel:
                    dependencies["audio_stream_channel"] = self._audio_stream_channel

                await self.output_provider_manager.setup_all_providers(self.event_bus, dependencies=dependencies)
                self.logger.info("OutputProvider 已启动")
            except Exception as e:
                self.logger.error(f"启动 OutputProvider 失败: {e}", exc_info=True)

    async def stop(self):
        """停止数据流协调器"""
        self.logger.info("停止数据流协调器...")

        # 停止OutputProvider
        if self.output_provider_manager:
            try:
                await self.output_provider_manager.stop_all_providers()
                self.logger.info("OutputProvider 已停止")
            except Exception as e:
                self.logger.error(f"停止 OutputProvider 失败: {e}", exc_info=True)

    async def cleanup(self):
        """清理资源"""
        self.logger.info("清理数据流协调器...")

        # 取消事件订阅
        if self._event_handler_registered:
            try:
                self.event_bus.off(CoreEvents.DECISION_INTENT_GENERATED, self._on_intent_ready)
                self._event_handler_registered = False
                self.logger.info("事件订阅已取消")
            except Exception as e:
                self.logger.error(f"取消事件订阅失败: {e}", exc_info=True)

        self._is_setup = False
        self.logger.info("数据流协调器清理完成")

    async def _on_intent_ready(self, event_name: str, payload: "IntentPayload", source: str):
        """
        处理Intent事件（Decision Domain → Output Domain，类型化）

        数据流（重构后）:
            IntentPayload → Intent → RenderParameters (TTS/Subtitle) → OutputPipeline处理 → 发布 EXPRESSION_PARAMETERS_GENERATED 事件 → TTS/Subtitle Providers

        注意: Avatar Providers 直接订阅 DECISION_INTENT_GENERATED 事件，不经过此方法

        Args:
            event_name: 事件名称（decision.intent_generated）
            payload: 类型化的事件数据（IntentPayload 对象，自动反序列化）
            source: 事件源
        """
        self.logger.info(f"收到Intent事件: {event_name}")

        try:
            # 使用 IntentPayload.to_intent() 方法转换为 Intent 对象
            intent = payload.to_intent()

            # 直接生成 RenderParameters（仅用于 TTS/Subtitle）
            # Avatar Provider 已在各自的 _on_intent_ready 中处理 Intent
            params = RenderParameters(
                text=intent.response_text,
                tts_text=intent.response_text,
                emotion=None,  # TTS/Subtitle 不需要情感参数
                vts_hotkey=None,
            )
            self.logger.info("RenderParameters生成完成（TTS/Subtitle）")

            # OutputPipeline 处理（参数后处理）
            if self.output_pipeline_manager:
                params = await self.output_pipeline_manager.process(params)
                if params is None:  # 被管道丢弃
                    self.logger.info("RenderParameters 被 Pipeline 丢弃，取消本次输出")
                    return
                self.logger.debug("OutputPipeline 处理完成")

            # 发布 EXPRESSION_PARAMETERS_GENERATED 事件（事件驱动）
            # TTS/Subtitle Provider 订阅此事件并响应
            payload = ParametersGeneratedPayload.from_parameters(params, source_intent=intent.model_dump(mode="json"))
            await self.event_bus.emit(CoreEvents.EXPRESSION_PARAMETERS_GENERATED, payload, source="OutputCoordinator")
            self.logger.debug(f"已发布事件: {CoreEvents.EXPRESSION_PARAMETERS_GENERATED}")

        except Exception as e:
            self.logger.error(f"处理Intent事件时出错: {e}", exc_info=True)

    def get_output_provider_manager(self) -> Optional[OutputProviderManager]:
        """获取输出Provider管理器实例"""
        return self.output_provider_manager

    def get_output_pipeline_manager(self) -> Optional[OutputPipelineManager]:
        """获取输出Pipeline管理器实例"""
        return self.output_pipeline_manager
