"""
Command Processor Plugin

处理消息中的命令标签（如 %{vts_trigger_hotkey}%），
执行相应的服务调用，并从消息文本中移除命令标签。
"""

from .plugin import CommandProcessorPlugin

__all__ = ["CommandProcessorPlugin"]
