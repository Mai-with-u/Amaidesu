"""
OutputPipeline 基类和协议定义

类似于 Input Domain 的 TextPipeline，但处理 ExpressionParameters 对象。
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from src.domains.output.parameters.render_parameters import ExpressionParameters
from src.modules.logging import get_logger
from src.modules.types.base.pipeline_stats import PipelineStats


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


@runtime_checkable
class OutputPipeline(Protocol):
    """
    输出参数处理管道协议（3域架构：Output Domain 的参数后处理）

    用于在 OutputDomain (ExpressionParameters → EventBus) 中处理参数，
    如参数过滤、修改、增强、条件渲染等。

    位置：
        - OutputDomain: FlowCoordinator._on_intent_ready() 方法内部调用
        - 在 ExpressionGenerator.generate() 之后、发布事件之前
        - 可返回 None 表示丢弃该输出

    数据流：
        ExpressionParameters → OutputPipeline.process() → ExpressionParameters | None
    """

    priority: int
    enabled: bool
    error_handling: PipelineErrorHandling
    timeout_seconds: float

    async def process(self, params: ExpressionParameters) -> Optional[ExpressionParameters]:
        """
        处理 ExpressionParameters

        Args:
            params: 待处理的参数对象

        Returns:
            处理后的参数对象，或 None 表示丢弃该输出
        """
        ...

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息（名称、版本等）"""
        ...

    def get_stats(self) -> PipelineStats:
        """获取统计信息"""
        ...

    def reset_stats(self) -> None:
        """重置统计信息"""
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
        """
        初始化 OutputPipeline

        Args:
            config: Pipeline 配置
        """
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self._stats = PipelineStats()

        # 从配置中读取可选参数
        self.priority = config.get("priority", self.priority)
        self.enabled = config.get("enabled", self.enabled)
        self.timeout_seconds = config.get("timeout_seconds", self.timeout_seconds)

        error_handling_str = config.get("error_handling", self.error_handling.value)
        if isinstance(error_handling_str, str):
            try:
                self.error_handling = PipelineErrorHandling(error_handling_str)
            except ValueError:
                self.logger.warning(f"无效的 error_handling 值: {error_handling_str}，使用默认值 CONTINUE")
                self.error_handling = PipelineErrorHandling.CONTINUE

    async def process(self, params: ExpressionParameters) -> Optional[ExpressionParameters]:
        """处理参数（包装 _process 并记录统计）"""
        import time

        start_time = time.time()
        try:
            result = await self._process(params)
            duration_ms = (time.time() - start_time) * 1000
            self._stats.processed_count += 1
            self._stats.total_duration_ms += duration_ms
            return result
        except Exception:
            self._stats.error_count += 1
            raise

    @abstractmethod
    async def _process(self, params: ExpressionParameters) -> Optional[ExpressionParameters]:
        """
        实际处理参数（子类实现）

        Args:
            params: 待处理的参数对象

        Returns:
            处理后的参数对象，或 None 表示丢弃
        """
        pass

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息"""
        return {
            "name": self.__class__.__name__,
            "priority": self.priority,
            "enabled": self.enabled,
            "error_handling": self.error_handling.value,
            "timeout_seconds": self.timeout_seconds,
        }

    def get_stats(self) -> PipelineStats:
        """获取统计信息"""
        return self._stats

    def reset_stats(self) -> None:
        """重置统计信息"""
        self._stats = PipelineStats()
