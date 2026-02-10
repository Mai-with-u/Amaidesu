"""
OutputProviderManager - Output Domain: 渲染输出管理器

职责:
- 管理多个OutputProvider
- 支持并发渲染
- 错误隔离（单个Provider失败不影响其他）
- 超时控制（防止单个Provider阻塞）
- 生命周期管理（启动、停止、清理）
- 从配置加载Provider
"""

import asyncio
from typing import Any, TYPE_CHECKING
from pydantic import BaseModel, Field
from src.core.utils.logger import get_logger

from src.core.base.output_provider import OutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters

# 类型检查时的导入
if TYPE_CHECKING:
    pass


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


class RenderStats(BaseModel):
    """
    渲染统计信息

    Attributes:
        total_renders: 总渲染次数
        success_count: 成功次数
        error_count: 错误次数
        timeout_count: 超时次数
        provider_stats: 各Provider统计 {provider_name: RenderStats}
    """

    total_renders: int = 0
    success_count: int = 0
    error_count: int = 0
    timeout_count: int = 0
    provider_stats: dict[str, dict[str, int]] = Field(default_factory=dict)


class OutputProviderManager:
    """
    输出Provider管理器

    核心职责:
    - 管理多个OutputProvider实例
    - 并发渲染到所有Provider
    - 错误隔离（单个Provider失败不影响其他）
    - 超时控制（防止单个Provider阻塞）
    - 生命周期管理
    """

    def __init__(self, config: dict[str, Any] = None):
        """
        初始化Provider管理器

        Args:
            config: 配置字典，支持:
                - concurrent_rendering: bool = True  # 是否并发渲染
                - error_handling: str = "continue"  # 错误处理策略: continue/stop/drop
                - render_timeout: float = 10.0  # 单个Provider渲染超时（秒）
        """
        self.config = config or {}
        self.providers: list[OutputProvider] = []
        self.logger = get_logger("OutputProviderManager")

        # 是否并发渲染（默认True）
        self.concurrent_rendering = self.config.get("concurrent_rendering", True)

        # 错误处理模式（continue/stop/drop）
        self.error_handling = self.config.get("error_handling", "continue")

        # 渲染超时时间（秒），0表示不限制
        self.render_timeout = float(self.config.get("render_timeout", 10.0))

        # 渲染统计信息
        self._render_stats = RenderStats()

        self.logger.info(
            f"OutputProviderManager初始化完成 "
            f"(concurrent={self.concurrent_rendering}, "
            f"error_handling={self.error_handling}, "
            f"timeout={self.render_timeout}s)"
        )

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

    async def render_all(self, parameters: RenderParameters) -> list[RenderResult]:
        """
        并发渲染到所有Provider

        核心特性:
        - 并发执行所有Provider的渲染（使用asyncio.gather）
        - 超时控制（单个Provider超时不影响其他）
        - 错误隔离（单个Provider失败不影响其他）
        - 统计信息更新

        Args:
            parameters: RenderParameters对象

        Returns:
            List[RenderResult]: 每个Provider的渲染结果
        """
        self.logger.info(f"正在渲染到 {len(self.providers)} 个Provider...")

        # 更新统计
        self._render_stats.total_renders += 1

        if not self.providers:
            self.logger.warning("没有已注册的Provider")
            return []

        # 获取已设置（is_setup=True）的Provider
        active_providers = [p for p in self.providers if p.is_setup]
        if not active_providers:
            self.logger.warning("没有已启动的Provider（is_setup=False）")
            return []

        if len(active_providers) < len(self.providers):
            self.logger.warning(f"部分Provider未启动: {len(active_providers)}/{len(self.providers)} 已就绪")

        if self.concurrent_rendering:
            # 并发渲染：使用_safe_render包装每个Provider
            results = await self._render_concurrent(active_providers, parameters)
        else:
            # 串行渲染（用于调试）
            results = await self._render_sequential(active_providers, parameters)

        # 更新统计信息
        self._update_render_stats(results)

        # 记录结果
        success_count = sum(1 for r in results if r.success)
        timeout_count = sum(1 for r in results if r.timeout)
        error_count = sum(1 for r in results if not r.success and not r.timeout)

        self.logger.info(f"渲染完成: 成功={success_count}/{len(results)}, 超时={timeout_count}, 失败={error_count}")

        return results

    async def _render_concurrent(
        self, providers: list[OutputProvider], parameters: RenderParameters
    ) -> list[RenderResult]:
        """
        并发渲染到多个Provider

        使用asyncio.gather并发执行所有渲染任务。
        每个任务都被_safe_render包装，提供超时和异常隔离。

        Args:
            providers: Provider列表
            parameters: 渲染参数

        Returns:
            List[RenderResult]: 渲染结果列表
        """
        # 创建渲染任务
        render_tasks = []
        for provider in providers:
            provider_name = provider.get_info()["name"]
            task = asyncio.create_task(self._safe_render(provider, parameters), name=f"render-{provider_name}")
            render_tasks.append((provider_name, task))

        # 等待所有任务完成（return_exceptions=True确保异常不会传播）
        results = []
        for provider_name, task in render_tasks:
            try:
                result = await task
                results.append(result)

                # 根据error_handling配置决定是否继续
                if not result.success and self.error_handling == "stop":
                    self.logger.error(f"停止渲染流程（错误处理策略: stop, 失败Provider: {provider_name}）")
                    # 取消剩余任务
                    for _remaining_name, remaining_task in render_tasks:
                        if not remaining_task.done():
                            remaining_task.cancel()
                    break
            except asyncio.CancelledError:
                self.logger.debug(f"渲染任务被取消: {provider_name}")
            except Exception as e:
                # 理论上不应到达这里（_safe_render已捕获异常）
                self.logger.error(f"渲染任务意外异常: {provider_name} - {e}")
                results.append(RenderResult(provider_name=provider_name, success=False, error=str(e)))

        return results

    async def _render_sequential(
        self, providers: list[OutputProvider], parameters: RenderParameters
    ) -> list[RenderResult]:
        """
        串行渲染到多个Provider（用于调试）

        Args:
            providers: Provider列表
            parameters: 渲染参数

        Returns:
            List[RenderResult]: 渲染结果列表
        """
        results = []
        for provider in providers:
            result = await self._safe_render(provider, parameters)
            results.append(result)

            # 根据error_handling配置决定是否继续
            if not result.success and self.error_handling == "stop":
                self.logger.error(f"停止渲染流程（错误处理策略: stop, 失败Provider: {result.provider_name}）")
                break

        return results

    async def _safe_render(self, provider: OutputProvider, parameters: RenderParameters) -> RenderResult:
        """
        安全渲染到单个Provider

        核心特性:
        - 超时控制（使用asyncio.wait_for）
        - 异常捕获（任何异常都被捕获并返回结果）
        - 性能计时（记录渲染耗时）

        Args:
            provider: OutputProvider实例
            parameters: RenderParameters对象

        Returns:
            RenderResult: 渲染结果
        """
        import time

        provider_name = provider.get_info()["name"]
        start_time = time.time()

        try:
            # 执行渲染（带超时）
            if self.render_timeout > 0:
                await asyncio.wait_for(provider.render(parameters), timeout=self.render_timeout)
            else:
                await provider.render(parameters)

            duration = time.time() - start_time
            self.logger.debug(f"Provider渲染成功: {provider_name} (耗时: {duration:.3f}s)")

            return RenderResult(provider_name=provider_name, success=True, duration=duration)

        except TimeoutError:
            duration = time.time() - start_time
            self.logger.warning(f"Provider渲染超时: {provider_name} (超时限制: {self.render_timeout}s)")

            return RenderResult(
                provider_name=provider_name,
                success=False,
                timeout=True,
                error=f"渲染超时（限制: {self.render_timeout}s）",
                duration=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            self.logger.error(
                f"Provider渲染失败: {provider_name} - {e}",
                exc_info=False,  # 不打印完整堆栈，避免日志冗长
            )

            return RenderResult(provider_name=provider_name, success=False, error=str(e), duration=duration)

    def _update_render_stats(self, results: list[RenderResult]) -> None:
        """
        更新渲染统计信息

        Args:
            results: 渲染结果列表
        """
        for result in results:
            # 初始化Provider统计
            if result.provider_name not in self._render_stats.provider_stats:
                self._render_stats.provider_stats[result.provider_name] = {"success": 0, "error": 0, "timeout": 0}

            # 更新全局统计
            if result.success:
                self._render_stats.success_count += 1
                self._render_stats.provider_stats[result.provider_name]["success"] += 1
            elif result.timeout:
                self._render_stats.timeout_count += 1
                self._render_stats.provider_stats[result.provider_name]["timeout"] += 1
            else:
                self._render_stats.error_count += 1
                self._render_stats.provider_stats[result.provider_name]["error"] += 1

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

    def get_stats(self) -> dict[str, Any]:
        """
        获取所有Provider的统计信息

        Returns:
            统计信息字典，包含:
            - total_providers: 总Provider数
            - setup_providers: 已启动Provider数
            - concurrent_rendering: 是否并发渲染
            - error_handling: 错误处理策略
            - render_timeout: 渲染超时时间
            - render_stats: 渲染统计（总渲染次数、成功/失败/超时次数）
            - provider_stats: 各Provider统计
        """
        provider_stats = {}
        for provider in self.providers:
            provider_info = provider.get_info()
            provider_name = provider_info["name"]
            provider_stats[provider_name] = {
                "is_setup": provider_info.get("is_setup", False),
                "type": provider_info.get("type", "unknown"),
                # 添加渲染统计
                **self._render_stats.provider_stats.get(provider_name, {"success": 0, "error": 0, "timeout": 0}),
            }

        return {
            "total_providers": len(self.providers),
            "setup_providers": sum(1 for p in self.providers if p.is_setup),
            "concurrent_rendering": self.concurrent_rendering,
            "error_handling": self.error_handling,
            "render_timeout": self.render_timeout,
            "render_stats": {
                "total_renders": self._render_stats.total_renders,
                "success_count": self._render_stats.success_count,
                "error_count": self._render_stats.error_count,
                "timeout_count": self._render_stats.timeout_count,
            },
            "provider_stats": provider_stats,
        }

    def get_render_stats(self) -> RenderStats:
        """
        获取渲染统计信息对象

        Returns:
            RenderStats: 渲染统计信息
        """
        return self._render_stats

    def reset_render_stats(self) -> None:
        """重置渲染统计信息"""
        self._render_stats = RenderStats()
        self.logger.debug("渲染统计信息已重置")

    # ==================== Phase 4: 配置加载 ====================

    async def load_from_config(self, config: dict[str, Any], config_service=None, core=None):
        """
        从配置加载并创建所有OutputProvider（支持三级配置合并）

        Args:
            config: 输出Provider配置（来自[providers.output]）
            core: AmaidesuCore实例（可选，用于访问服务）
            config_service: ConfigService实例（用于三级配置加载）

        配置格式:
            [providers.output]
            enabled = true
            enabled_outputs = ["subtitle", "vts", "tts"]

            # 可选：常用参数覆盖
            [providers.output.overrides.subtitle]
            font_size = 24
        """
        self.logger.info("开始从配置加载OutputProvider...")

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
                # 使用三级配置加载
                try:
                    from src.services.config.schemas import get_provider_schema

                    schema_class = get_provider_schema(output_name, "output")
                except (ImportError, AttributeError, KeyError):
                    # Schema registry未实现或Provider未注册，回退到None
                    schema_class = None

                if config_service:
                    provider_config = config_service.get_provider_config_with_defaults(
                        provider_name=output_name,
                        provider_layer="output",
                        schema_class=schema_class,
                    )
                else:
                    # 向后兼容：直接从配置中读取
                    provider_config = config.get("outputs_config", {}).get(output_name, {})

                provider_type = provider_config.get("type", output_name)

                # 创建Provider（不再检查enabled字段，由enabled_outputs控制）
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
            f"OutputProvider加载完成: 成功={created_count}/{len(enabled_outputs)}, "
            f"失败={failed_count}/{len(enabled_outputs)}"
        )

    def _create_provider(self, provider_type: str, config: dict[str, Any], core=None) -> OutputProvider | None:
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
        from src.core.provider_registry import ProviderRegistry

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
