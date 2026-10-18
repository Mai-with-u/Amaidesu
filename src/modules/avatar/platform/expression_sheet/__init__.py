"""表情差分图虚拟形象模块

基于"表情差分图切换"的静态立绘虚拟形象平台（avatar 平台第 4 家 Provider）:
- ``ExpressionSheetProvider``: ToolProvider 实现，把"按情绪切换差分图"能力
  封装为一个 Provider（``expression_sheet_set_expression``、
  ``expression_sheet_list_preset_actions``、``expression_sheet_trigger_preset_action``）。
"""

from .expression_sheet_provider import (
    ExpressionSheetProvider,
    create_expression_sheet_provider,
    register_expression_sheet_tools,
)

__all__ = [
    "ExpressionSheetProvider",
    "create_expression_sheet_provider",
    "register_expression_sheet_tools",
]
