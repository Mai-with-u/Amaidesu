import sys
from loguru import logger

# 移除默认的 handler
logger.remove()

# 添加一个新的 handler，输出到 stderr，并启用颜色
logger.add(
    sys.stderr,
    level="INFO",  # 可以根据需要调整日志级别
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{line: <4}</cyan> | <cyan>{extra[module]}</cyan> - <level>{message}</level>",
)


# 可以在这里添加其他的 handler，比如写入文件
# logger.add("file_{time}.log", rotation="1 week") # 例如：每周轮换日志文件


def get_logger(module_name: str):
    """获取绑定了模块名的 logger 实例"""
    return logger.bind(module=module_name)


# 导出配置好的 logger 获取函数
__all__ = ["get_logger"]
