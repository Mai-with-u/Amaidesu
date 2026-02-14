"""日志配置模块。

此模块提供延迟初始化的日志配置，避免在导入时添加处理器。
应在应用启动时（main.py）调用 configure_from_config() 进行配置。
"""

import json
import os
import sys
import time
from pathlib import Path

from loguru import logger as loguru_logger

# 模块级状态变量
_CONFIGURED = False  # 追踪 configure_from_config() 是否已被调用
_HANDLER_IDS: list[int] = []  # 追踪处理器 ID 以便清理
_DEFAULT_HANDLER_ID: int | None = None  # 追踪默认处理器


def _ensure_default_handler():
    """确保默认 stderr 处理器存在（延迟初始化）。

    若 configure_from_config() 尚未被调用，则创建一个默认的 stderr 处理器。
    这确保了在配置前调用 get_logger() 仍然能正常工作。
    """
    global _DEFAULT_HANDLER_ID, _CONFIGURED
    if _DEFAULT_HANDLER_ID is None and not _CONFIGURED:
        _DEFAULT_HANDLER_ID = loguru_logger.add(
            sys.stderr,
            level="INFO",
            colorize=True,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{extra[module]}</cyan> - <level>{message}</level>",
        )
    return _DEFAULT_HANDLER_ID


def configure_from_config(config_dict: dict | None = None) -> None:
    """从配置字典配置日志。

    此函数应在应用启动时（在 main.py 中）调用一次。
    调用此函数后，get_logger() 将使用配置的处理器。

    Args:
        config_dict: 日志配置字典，包含以下键：
            - enabled: bool - 启用文件日志（默认：True）
            - format: Literal["jsonl", "text"] - 日志格式（默认："jsonl"）
            - directory: str - 日志目录路径（默认："logs"）
            - level: str - 日志级别（默认："INFO"）
            - rotation: str - 文件轮转触发条件（默认："10 MB"）
            - retention: str - 日志保留时间（默认："7 days"）
            - compression: str - 压缩格式（默认："zip"）
            - split_by_session: bool - 是否按会话分割日志文件（默认：False）
            - console_level: str - 控制台日志级别（默认："INFO"）
            - filter: list[str] - 模块过滤器列表（仅显示这些模块的日志）

    若 config_dict 为 None，使用默认值（启用文件日志，JSONL 格式）。
    """
    global _CONFIGURED, _HANDLER_IDS, _DEFAULT_HANDLER_ID

    # 标记已配置
    _CONFIGURED = True

    # 解析配置，使用默认回退值
    enabled = config_dict.get("enabled", True) if config_dict else True
    log_format = config_dict.get("format", "jsonl") if config_dict else "jsonl"
    directory = config_dict.get("directory", "logs") if config_dict else "logs"
    level = config_dict.get("level", "INFO") if config_dict else "INFO"
    rotation = config_dict.get("rotation", "10 MB") if config_dict else "10 MB"
    retention = config_dict.get("retention", "7 days") if config_dict else "7 days"
    compression = config_dict.get("compression", "zip") if config_dict else "zip"
    split_by_session = config_dict.get("split_by_session", False) if config_dict else False
    console_level = config_dict.get("console_level", "INFO") if config_dict else "INFO"

    # 0. 清除 loguru 的默认处理器（如果存在）
    # loguru 会在第一次使用时自动添加一个默认的 stderr 处理器
    # 我们需要完全移除它以避免日志重复
    loguru_logger.remove()

    # 1. 重置状态变量
    _DEFAULT_HANDLER_ID = None
    _HANDLER_IDS.clear()

    # 3. 添加带颜色的 stderr 处理器
    # 解析 filter 配置
    filter_config = config_dict.get("filter") if config_dict else None

    # 定义模块过滤器函数
    if filter_config:
        # filter_config 可以是 list[str] 或 callable
        if callable(filter_config):
            # 如果是可调用对象（从 main.py 传入的 filter_logic）
            module_filter = filter_config
        else:
            # 如果是列表，创建过滤器函数
            filter_modules = set(filter_config)

            def module_filter(record):
                """只允许指定模块的日志通过，WARNING 及以上级别总是显示"""
                module = record["extra"].get("module", "unknown")
                # 如果模块在过滤列表中，或者日志级别 >= WARNING，则显示
                return module in filter_modules or record["level"].no >= loguru_logger.level("WARNING").no
    else:
        module_filter = None

    # 添加 stderr 处理器，应用过滤器（如果有）
    stderr_handler_id = loguru_logger.add(
        sys.stderr,
        level=console_level,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{extra[module]}</cyan> - <level>{message}</level>",
        filter=module_filter,
    )
    _HANDLER_IDS.append(stderr_handler_id)

    # 4. 若 enabled=True，添加文件处理器
    if enabled:
        # 按需创建目录
        if not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                loguru_logger.warning(f"无法创建日志目录 {directory}，将仅使用控制台输出: {e}")
                return

        # 定义自定义 JSONL sink
        # loguru 的 serialize=True 会传递包含 {"text": "...", "record": {...}} 的 JSON 字符串
        def json_sink(message):
            """自定义 JSONL sink，每行写入一个 JSON 对象。"""
            data = json.loads(message)  # {"text": "...", "record": {...}}
            record = data["record"]  # 实际的记录对象
            # 使用 ISO 格式的时间戳字符串，更简洁易读
            timestamp_str = record["time"]["repr"]
            log_obj = {
                "timestamp": timestamp_str,
                "level": record["level"]["name"],
                "module": record["extra"].get("module", "unknown"),
                "message": record["message"],
            }
            # 追加到文件
            with open(file_path_for_json, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_obj, ensure_ascii=False) + "\n")

        # 根据格式选择文件名和格式化器
        if log_format == "jsonl":
            # 预先计算文件路径
            # 根据 split_by_session 决定时间戳格式
            if split_by_session:
                # 按会话分割：每次启动生成新文件
                timestamp_str = time.strftime("%Y%m%d_%H%M%S")
            else:
                # 默认：按日期轮转，同一天多次运行追加到同一文件
                timestamp_str = time.strftime("%Y-%m-%d")
            file_path_for_json = str(Path(directory) / f"amaidesu_{timestamp_str}.jsonl")

            # 使用自定义 sink
            file_handler_id = loguru_logger.add(
                json_sink,
                level=level,
                serialize=True,  # 序列化为 JSON
            )
            _HANDLER_IDS.append(file_handler_id)
            return  # JSONL 格式已处理，直接返回
        else:  # text
            # 根据 split_by_session 决定文件名格式
            if split_by_session:
                # 按会话分割：每次启动生成新文件
                import datetime

                time_suffix = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                file_path = os.path.join(directory, f"amaidesu_{time_suffix}.log")
            else:
                # 默认：按日期轮转，同一天多次运行追加到同一文件
                file_path = os.path.join(directory, "amaidesu_{time}.log")
            file_format = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{extra[module]}</cyan> - <level>{message}</level>"
            # 添加文件处理器
            file_handler_id = loguru_logger.add(
                file_path,
                level=level,
                format=file_format,
                rotation=rotation,
                retention=retention,
                compression=compression,
                encoding="utf-8",
            )
            _HANDLER_IDS.append(file_handler_id)


def get_logger(module_name: str):
    """获取绑定了模块名的 logger 实例。

    若尚未调用 configure_from_config()，则会自动创建一个默认的 stderr 处理器。

    Args:
        module_name: 模块名称，用于标识日志来源

    Returns:
        绑定了模块名的 loguru logger 实例
    """
    # 若尚未配置，确保默认处理器存在
    _ensure_default_handler()
    return loguru_logger.bind(module=module_name)


# 导出配置好的 logger 获取函数
__all__ = ["get_logger", "configure_from_config"]
