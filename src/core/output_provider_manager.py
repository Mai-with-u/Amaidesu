"""
OutputProviderManager - Layer 7 渲染呈现层管理器

职责:
- 管理多个OutputProvider
- 支持并发渲染
- 错误隔离（单个Provider失败不影响其他）
- 生命周期管理（启动、停止、清理）
- 从配置加载Provider
"""

import asyncio
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from src.utils.logger import get_logger

from .providers.output_provider import OutputProvider
from ..expression.render_parameters import ExpressionParameters

# 类型检查时的导入
if TYPE_CHECKING:
    pass


class OutputProviderManager:
    """
    输出Provider管理器

    核心职责:
    - 管理多个OutputProvider实例
    - 并发渲染到所有Provider
    - 错误隔离（单个Provider失败不影响其他）
    - 生命周期管理
    """

    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化Provider管理器

        Args:
            config: 配置字典
        """
        self.config = config or {}
        self.providers: List[OutputProvider] = []
        self.logger = get_logger("OutputProviderManager")

        # 是否并发渲染（默认True）
        self.concurrent_rendering = self.config.get("concurrent_rendering", True)

        # 错误处理模式（continue/stop/drop）
        self.error_handling = self.config.get("error_handling", "continue")

        self.logger.info("OutputProviderManager初始化完成")

    async def register_provider(self, provider: OutputProvider):
        """
        注册Provider

        Args:
            provider: OutputProvider实例
        """
        self.providers.append(provider)
        self.logger.info(f"Provider已注册: {provider.get_info()['name']}")

    async def setup_all_providers(self, event_bus):
        """
        启动所有Provider

        Args:
            event_bus: EventBus实例
        """
        self.logger.info(f"正在启动 {len(self.providers)} 个Provider...")

        if self.concurrent_rendering:
            # 并发启动所有Provider
            setup_tasks = []
            for provider in self.providers:
                setup_tasks.append(provider.setup(event_bus))

            await asyncio.gather(*setup_tasks, return_exceptions=True)
        else:
            # 串行启动（用于调试）
            for provider in self.providers:
                try:
                    await provider.setup(event_bus)
                except Exception as e:
                    self.logger.error(f"Provider启动失败: {provider.get_info()['name']} - {e}")

        # 检查所有Provider是否都已设置
        all_setup = all(provider.is_setup for provider in self.providers)
        if all_setup:
            self.logger.info(f"所有 {len(self.providers)} 个Provider已启动")
        else:
            setup_count = sum(1 for p in self.providers if p.is_setup)
            self.logger.warning(f"部分Provider启动失败: {setup_count}/{len(self.providers)}")

    async def render_all(self, parameters: ExpressionParameters):
        """
        并发渲染到所有Provider

        Args:
            parameters: ExpressionParameters对象
        """
        self.logger.info(f"正在渲染到 {len(self.providers)} 个Provider...")

        if not self.providers:
            self.logger.warning("没有已注册的Provider")
            return

        if self.concurrent_rendering:
            # 并发渲染
            render_tasks = []
            for provider in self.providers:
                render_tasks.append(provider.render(parameters))

            results = await asyncio.gather(*render_tasks, return_exceptions=True)

            # 检查结果
            success_count = 0
            error_count = 0
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger.error(f"Provider渲染失败: {self.providers[i].get_info()['name']} - {result}")
                    error_count += 1
                    if self.error_handling == "stop":
                        self.logger.error("停止渲染流程（错误处理策略: stop）")
                        break
                else:
                    success_count += 1

            self.logger.info(
                f"渲染完成: 成功={success_count}/{len(self.providers)}, 失败={error_count}/{len(self.providers)}"
            )
        else:
            # 串行渲染（用于调试）
            for provider in self.providers:
                try:
                    await provider.render(parameters)
                    self.logger.debug(f"Provider渲染成功: {provider.get_info()['name']}")
                except Exception as e:
                    self.logger.error(f"Provider渲染失败: {provider.get_info()['name']} - {e}")
                    if self.error_handling == "stop":
                        self.logger.error("停止渲染流程（错误处理策略: stop）")
                        break

    async def stop_all_providers(self):
        """
        停止所有Provider
        """
        self.logger.info(f"正在停止 {len(self.providers)} 个Provider...")

        # 先停止渲染
        for provider in self.providers:
            if provider.is_setup:
                try:
                    # Provider的cleanup方法会处理停止逻辑
                    await provider.cleanup()
                except Exception as e:
                    self.logger.error(f"Provider停止失败: {provider.get_info()['name']} - {e}")

        self.logger.info("所有Provider已停止")

    def get_provider_names(self) -> List[str]:
        """
        获取所有Provider名称

        Returns:
            Provider名称列表
        """
        return [p.get_info()["name"] for p in self.providers]

    def get_provider_by_name(self, name: str) -> Optional[OutputProvider]:
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

    def get_stats(self) -> Dict[str, Any]:
        """
        获取所有Provider的统计信息

        Returns:
            统计信息字典
        """
        provider_stats = {}
        for provider in self.providers:
            provider_info = provider.get_info()
            provider_stats[provider_info["name"]] = {
                "is_setup": provider_info.get("is_setup", False),
                "type": provider_info.get("type", "unknown"),
            }

        return {
            "total_providers": len(self.providers),
            "setup_providers": sum(1 for p in self.providers if p.is_setup),
            "concurrent_rendering": self.concurrent_rendering,
            "error_handling": self.error_handling,
            "provider_stats": provider_stats,
        }

    async def _render_single(self, provider: OutputProvider, parameters: ExpressionParameters):
        """
        渲染到单个Provider（内部方法）

        Args:
            provider: Provider实例
            parameters: ExpressionParameters对象
        """
        try:
            await provider.render(parameters)
            return True
        except Exception as e:
            self.logger.error(f"Provider渲染异常: {provider.get_info()['name']} - {e}")
            raise e

    # ==================== Phase 4: 配置加载 ====================

    async def load_from_config(self, config: Dict[str, Any], core=None):
        """
        从配置加载并创建所有OutputProvider

        Args:
            config: 渲染配置（来自[rendering]）
            core: AmaidesuCore实例（可选，用于访问服务）

        配置格式:
            [rendering]
            outputs = ["tts", "subtitle", "sticker", "vts", "omni_tts"]

            [rendering.outputs.tts]
            type = "tts"
            engine = "edge"
            voice = "zh-CN-XiaoxiaoNeural"
            ...
        """
        self.logger.info("开始从配置加载OutputProvider...")

        # 检查是否启用
        enabled = config.get("enabled", True)
        if not enabled:
            self.logger.info("渲染层已禁用（enabled=false）")
            return

        # 更新管理器配置
        self.concurrent_rendering = config.get("concurrent_rendering", True)
        self.error_handling = config.get("error_handling", "continue")

        # 获取Provider列表
        outputs = config.get("outputs", [])
        if not outputs:
            self.logger.warning("未配置任何输出Provider（outputs为空）")
            return

        self.logger.info(f"配置了 {len(outputs)} 个输出Provider: {outputs}")

        # 获取各个Provider的配置
        outputs_config = config.get("outputs", {})

        # 创建Provider实例
        created_count = 0
        failed_count = 0

        for output_name in outputs:
            provider_config = outputs_config.get(output_name, {})
            provider_type = provider_config.get("type", output_name)

            try:
                provider = self._create_provider(provider_type, provider_config, core)
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
            f"OutputProvider加载完成: 成功={created_count}/{len(outputs)}, 失败={failed_count}/{len(outputs)}"
        )

    def _create_provider(self, provider_type: str, config: Dict[str, Any], core=None) -> Optional[OutputProvider]:
        """
        Provider工厂方法：根据类型创建Provider实例

        使用 ProviderRegistry 来创建 Provider，替代之前的硬编码映射。

        Args:
            provider_type: Provider类型（"tts", "subtitle", "sticker", "vts", "omni_tts", "avatar"等）
            config: Provider配置
            core: AmaidesuCore实例（可选）

        Returns:
            Provider实例，如果创建失败返回None
        """
        from src.rendering.provider_registry import ProviderRegistry

        # 检查 Provider 是否已注册
        if not ProviderRegistry.is_output_provider_registered(provider_type):
            available = ", ".join(ProviderRegistry.get_registered_output_providers())
            self.logger.error(
                f"未知的Provider类型: '{provider_type}'. "
                f"可用的Provider: {available or '无'}"
            )
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
