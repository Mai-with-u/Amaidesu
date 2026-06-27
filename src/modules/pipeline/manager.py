"""PipelineManager[T] 泛型管理器。

负责 Pipeline 的注册、排序、执行、统计。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Generic, List, Optional, Type

from src.modules.di import instantiate_with_di
from src.modules.logging import get_logger
from src.modules.types.base.pipeline_types import PipelineErrorHandling, PipelineException

from src.modules.pipeline.base import T, Pipeline
from src.modules.pipeline.registry import PIPELINE_REGISTRY


class PipelineManager(Generic[T]):
    """Pipeline 泛型管理器。

    Args:
        stage: "input" 或 "output"
        services_by_type: 可用服务字典（key=类型, value=服务实例），用于类型匹配注入
    """

    def __init__(
        self,
        stage: str,
        services_by_type: Optional[Dict[Type[Any], Any]] = None,
    ):
        if stage not in ("input", "output"):
            raise ValueError(f"stage 必须是 'input' 或 'output'，得到: {stage}")
        self.stage = stage
        self.services_by_type = services_by_type or {}
        self._pipelines: List[Pipeline] = []
        self._pipelines_sorted: bool = True
        self._pipeline_lock = asyncio.Lock()
        self.logger = get_logger("PipelineManager")

    def register_pipeline(self, pipeline_instance: Pipeline) -> None:
        """注册一个 Pipeline。"""
        self._pipelines.append(pipeline_instance)
        self._pipelines_sorted = False
        info = pipeline_instance.get_info()
        self.logger.info(f"Pipeline 已注册: {info['name']} (priority={info['priority']}, enabled={info['enabled']})")

    def _ensure_pipelines_sorted(self) -> None:
        """确保 Pipeline 列表按优先级排序（数字小优先）。"""
        if not self._pipelines_sorted:
            self._pipelines.sort(key=lambda p: p.priority)
            self._pipelines_sorted = True
            pipe_info = ", ".join([f"{p.get_info()['name']}({p.priority})" for p in self._pipelines])
            self.logger.debug(f"Pipeline 已排序: {pipe_info}")

    async def process(self, item: T) -> Optional[T]:
        """按优先级顺序通过所有启用的 Pipeline 处理 item。

        Args:
            item: 待处理对象

        Returns:
            处理后的对象，如果任何 Pipeline 返回 None 则返回 None（表示丢弃）

        Raises:
            PipelineException: 当某个 Pipeline 错误处理策略为 STOP 时抛出
        """
        if not self._pipelines:
            return item

        async with self._pipeline_lock:
            self._ensure_pipelines_sorted()

            current_item = item
            for pipeline_instance in self._pipelines:
                if not pipeline_instance.enabled:
                    continue

                info = pipeline_instance.get_info()
                pipeline_name = info["name"]

                try:
                    start_time = time.time()
                    assert current_item is not None
                    result = await asyncio.wait_for(
                        pipeline_instance.process(current_item),
                        timeout=pipeline_instance.timeout_seconds,
                    )
                    current_item = result

                    duration_ms = (time.time() - start_time) * 1000

                    if current_item is None:
                        self.logger.debug(f"Pipeline {pipeline_name} 丢弃了 item (耗时 {duration_ms:.2f}ms)")
                        stats = pipeline_instance.get_stats()
                        stats.dropped_count += 1
                        return None

                    self.logger.debug(f"Pipeline {pipeline_name} 处理完成 (耗时 {duration_ms:.2f}ms)")

                except asyncio.TimeoutError as timeout_error:
                    error = PipelineException(pipeline_name, f"处理超时 ({pipeline_instance.timeout_seconds}s)")
                    self.logger.error(f"Pipeline 超时: {error}")
                    stats = pipeline_instance.get_stats()
                    stats.error_count += 1
                    if pipeline_instance.error_handling == PipelineErrorHandling.STOP:
                        raise error from timeout_error
                    elif pipeline_instance.error_handling == PipelineErrorHandling.DROP:
                        stats.dropped_count += 1
                        return None
                    # CONTINUE: 继续下一个

                except PipelineException:
                    raise

                except Exception as e:
                    error = PipelineException(pipeline_name, f"处理失败: {e}", original_error=e)
                    self.logger.error(f"Pipeline 错误: {error}", exc_info=True)
                    stats = pipeline_instance.get_stats()
                    stats.error_count += 1
                    if pipeline_instance.error_handling == PipelineErrorHandling.STOP:
                        raise error from e
                    elif pipeline_instance.error_handling == PipelineErrorHandling.DROP:
                        stats.dropped_count += 1
                        return None
                    # CONTINUE: 继续下一个

            return current_item

    def get_pipeline_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有 Pipeline 的统计信息。"""
        result: Dict[str, Dict[str, Any]] = {}
        for p in self._pipelines:
            info = p.get_info()
            stats = p.get_stats()
            result[info["name"]] = {
                "processed_count": stats.processed_count,
                "dropped_count": stats.dropped_count,
                "error_count": stats.error_count,
                "avg_duration_ms": stats.avg_duration_ms,
                "enabled": info["enabled"],
                "priority": info["priority"],
            }
        return result

    def _instantiate_pipeline(
        self,
        pipeline_cls: Type[Pipeline],
        pipeline_config: Dict[str, Any],
    ) -> Pipeline:
        """按类型匹配注入依赖并实例化 Pipeline。

        Args:
            pipeline_cls: Pipeline 类
            pipeline_config: Pipeline 配置

        Returns:
            Pipeline 实例

        Raises:
            PipelineConfigError: 当 Pipeline 声明了不可用的依赖时
        """
        try:
            return instantiate_with_di(
                pipeline_cls,
                config=pipeline_config,
                services_by_type=self.services_by_type,
            )
        except Exception as e:
            raise PipelineConfigError(f"Pipeline {pipeline_cls.__name__} 实例化失败: {e}") from e

    async def load_from_config(
        self,
        config: Dict[str, Dict[str, Any]],
    ) -> int:
        """从配置加载 Pipeline。

        Args:
            config: dict of {name: pipeline_config}（来自 [pipelines.<stage>.<name>]）

        Returns:
            加载的 Pipeline 数量
        """
        loaded_count = 0
        for name, pipeline_config in config.items():
            if not isinstance(pipeline_config, dict):
                continue

            enabled = pipeline_config.get("enabled", True)
            if not enabled:
                continue

            # 必填字段校验
            if "priority" not in pipeline_config or not isinstance(pipeline_config["priority"], int):
                self.logger.warning(f"Pipeline '{name}' 缺少 priority 字段或类型错误，跳过加载")
                continue

            # 查注册表
            key = (self.stage, name)
            pipeline_cls = PIPELINE_REGISTRY.get(key)
            if pipeline_cls is None:
                raise PipelineConfigError(
                    f"Pipeline '{name}' 在配置 [pipelines.{self.stage}.{name}] 中启用，"
                    f"但未在 PIPELINE_REGISTRY 中注册。"
                    f"请确认：(1) 模块是否被 import？(2) 是否加了 @pipeline('{name}') 装饰器？"
                )

            # 构造 Pipeline（反射注入服务）
            try:
                pipeline_instance = self._instantiate_pipeline(pipeline_cls, pipeline_config)
                self.register_pipeline(pipeline_instance)
                loaded_count += 1
            except Exception as e:
                self.logger.error(f"实例化 Pipeline {name} 失败: {e}", exc_info=True)
                raise

        if loaded_count > 0:
            self.logger.info(f"加载完成，共 {loaded_count} 个 Pipeline")
        else:
            self.logger.info("未加载任何 Pipeline")
        return loaded_count


class PipelineConfigError(Exception):
    """Pipeline 配置错误（缺少注册、签名不匹配等）。"""


__all__ = ["PipelineManager", "PipelineConfigError"]
