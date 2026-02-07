# 导出所有管道类，方便导入
# 新架构管道（TextPipelineBase）
from src.domains.input.pipelines.rate_limit.pipeline import RateLimitTextPipeline
from src.domains.input.pipelines.similar_filter.pipeline import SimilarFilterTextPipeline

# 旧架构管道（MessagePipeline）- 保留用于消息日志记录
from src.domains.input.pipelines.message_logger.pipeline import MessageLoggerPipeline

__all__ = [
    # 新架构管道
    "RateLimitTextPipeline",
    "SimilarFilterTextPipeline",
    # 旧架构管道
    "MessageLoggerPipeline",
]
