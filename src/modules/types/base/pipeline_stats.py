"""
PipelineStats 共享统计类

供 Input Domain 的 InputPipeline 和 Output Domain 的 OutputPipeline 使用。
"""

from dataclasses import dataclass


@dataclass
class PipelineStats:
    """Pipeline 统计信息"""

    processed_count: int = 0  # 处理次数
    dropped_count: int = 0  # 丢弃次数
    error_count: int = 0  # 错误次数
    total_duration_ms: float = 0  # 总处理时间（毫秒）
    filtered_words_count: int = 0  # 过滤的敏感词数量（仅用于统计，可选）

    @property
    def avg_duration_ms(self) -> float:
        """平均处理时间（毫秒）"""
        if self.processed_count == 0:
            return 0.0
        return self.total_duration_ms / self.processed_count
