"""
OutputPipeline 基类和协议定义

处理 Intent 的管道，在 Intent 分发给 OutputProvider 前执行过滤。
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol, runtime_checkable

import time

from src.modules.logging import get_logger
from src.modules.types.base.pipeline_stats import PipelineStats

if TYPE_CHECKING:
    from src.modules.types import Intent


class PipelineErrorHandling(str, Enum):
    """Pipeline 错误处理策略"""

    CONTINUE = "continue"  # 记录日志，继续执行下一个 Pipeline
    STOP = "stop"  # 停止执行，抛出异常
    DROP = "drop"  # 丢弃消息，不执行后续 Pipeline


class PipelineException(Exception):
    """Pipeline 处理异常"""

    def __init__(
        self,
        pipeline_name: str,
        message: str,
        original_error: Optional[Exception] = None,
    ):
        self.pipeline_name = pipeline_name
        self.message = message
        self.original_error = original_error
        super().__init__(f"[{pipeline_name}] {message}")
        # 设置异常链，使 __cause__ 可用
        if original_error is not None:
            self.__cause__ = original_error


@runtime_checkable
class OutputPipeline(Protocol):
    """
    输出管道协议（与 InputPipeline 对称）

    处理 Intent，在分发给 OutputProvider 前执行过滤/修改/丢弃。

    数据流：
        Intent → OutputPipeline.process() → Intent | None
    """

    priority: int
    enabled: bool
    error_handling: PipelineErrorHandling
    timeout_seconds: float

    async def process(self, intent: "Intent") -> Optional["Intent"]:
        """处理 Intent，返回 None 表示丢弃"""
        ...

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息"""
        ...

    def get_stats(self) -> PipelineStats:
        """获取统计信息"""
        ...

    def reset_stats(self) -> None:
        """重置统计"""
        ...


class OutputPipelineBase(ABC):
    """
    OutputPipeline 基类

    提供默认实现，子类只需实现 _process() 方法。
    """

    priority: int = 500
    enabled: bool = True
    error_handling: PipelineErrorHandling = PipelineErrorHandling.CONTINUE
    timeout_seconds: float = 5.0

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self._stats = PipelineStats()

        # 从配置读取可选参数
        self.priority = config.get("priority", self.priority)
        self.enabled = config.get("enabled", self.enabled)
        self.timeout_seconds = config.get("timeout_seconds", self.timeout_seconds)

        error_handling_str = config.get("error_handling", self.error_handling.value)
        if isinstance(error_handling_str, str):
            try:
                self.error_handling = PipelineErrorHandling(error_handling_str)
            except ValueError:
                self.logger.warning(f"无效的 error_handling 值: {error_handling_str}")
                self.error_handling = PipelineErrorHandling.CONTINUE

    async def process(self, intent: "Intent") -> Optional["Intent"]:
        """处理 Intent（包装 _process 并记录统计）"""
        start_time = time.time()
        try:
            result = await self._process(intent)
            duration_ms = (time.time() - start_time) * 1000
            self._stats.processed_count += 1
            self._stats.total_duration_ms += duration_ms
            return result
        except Exception:
            self._stats.error_count += 1
            raise

    @abstractmethod
    async def _process(self, intent: "Intent") -> Optional["Intent"]:
        """实际处理 Intent（子类实现）"""
        pass

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "priority": self.priority,
            "enabled": self.enabled,
            "error_handling": self.error_handling.value,
            "timeout_seconds": self.timeout_seconds,
        }

    def get_stats(self) -> PipelineStats:
        return self._stats

    def reset_stats(self) -> None:
        self._stats = PipelineStats()
