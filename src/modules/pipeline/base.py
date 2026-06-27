"""Pipeline[T] 泛型基类。

为 Input 和 Output 阶段的 Pipeline 提供统一的抽象。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Generic, Optional, TypeVar

from src.modules.logging import get_logger
from src.modules.types.base.pipeline_stats import PipelineStats
from src.modules.types.base.pipeline_types import PipelineErrorHandling

T = TypeVar("T")  # exported


class Pipeline(ABC, Generic[T]):
    """Pipeline 泛型基类。

    子类实现 `_process()` 方法定义具体处理逻辑。
    基类负责统计、超时、错误处理的包装。
    """

    priority: int = 500
    enabled: bool = True
    error_handling: PipelineErrorHandling = PipelineErrorHandling.CONTINUE
    timeout_seconds: float = 5.0

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """初始化 Pipeline。

        Args:
            config: 配置字典（来自 [pipelines.<stage>.<name>]）
        """
        self.config = config or {}
        self.logger = get_logger(self.__class__.__name__)
        self._stats = PipelineStats()

        # 从配置读取可选参数（覆盖类属性默认值）
        self.priority = self.config.get("priority", self.priority)
        self.enabled = self.config.get("enabled", self.enabled)
        self.timeout_seconds = self.config.get("timeout_seconds", self.timeout_seconds)

        error_handling_str = self.config.get("error_handling", self.error_handling.value)
        if isinstance(error_handling_str, str):
            try:
                self.error_handling = PipelineErrorHandling(error_handling_str)
            except ValueError:
                self.logger.warning(f"无效的 error_handling 值: {error_handling_str}")
                self.error_handling = PipelineErrorHandling.CONTINUE

    async def process(self, item: T) -> Optional[T]:
        """处理 item（包装 _process 并记录统计）。

        Args:
            item: 待处理对象（NormalizedMessage 或 Intent）

        Returns:
            处理后的对象，或 None（表示丢弃）
        """
        start_time = time.time()
        try:
            result = await self._process(item)
            duration_ms = (time.time() - start_time) * 1000
            self._stats.processed_count += 1
            self._stats.total_duration_ms += duration_ms
            return result
        except Exception:
            self._stats.error_count += 1
            raise

    @abstractmethod
    async def _process(self, item: T) -> Optional[T]:
        """实际处理 item（子类实现）。

        Args:
            item: 待处理对象

        Returns:
            - 原 item（透传）
            - 新 item（model_copy 或新对象）
            - None（丢弃整个 item）
        """
        raise NotImplementedError

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息。"""
        return {
            "name": self.__class__.__name__,
            "priority": self.priority,
            "enabled": self.enabled,
            "error_handling": self.error_handling.value,
            "timeout_seconds": self.timeout_seconds,
        }

    def get_stats(self) -> PipelineStats:
        """获取 Pipeline 统计信息。"""
        return self._stats

    def reset_stats(self) -> None:
        """重置 Pipeline 统计信息。"""
        self._stats = PipelineStats()


__all__ = ["Pipeline"]
