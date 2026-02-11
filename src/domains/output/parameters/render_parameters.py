"""
渲染参数数据类 - Output Domain 内部数据格式（向后兼容性）

为了符合3域架构，类型定义已迁移到 src/modules/types/render_parameters.py。
此文件保持向后兼容性，新代码应该从 Modules 层导入。
"""

# 向后兼容：重导出从 Modules 层的类型
# 新代码应该使用: from src.modules.types import ExpressionParameters, RenderParameters
from src.modules.types import ExpressionParameters, RenderParameters

__all__ = ["ExpressionParameters", "RenderParameters"]
