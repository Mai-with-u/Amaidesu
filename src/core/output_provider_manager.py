"""
OutputProviderManager - Layer 6 Rendering层管理器

职责:
- 管理多个OutputProvider
- 支持并发渲染
- 错误隔离（单个Provider失败不影响其他）
- 生命周期管理（启动、停止、清理）
"""

import asyncio
from typing import List, Dict, Any, Optional
from src.utils.logger import get_logger

from .output_provider import OutputProvider
from ..expression.render_parameters import ExpressionParameters


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
