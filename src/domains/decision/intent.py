"""Intent 数据类型重导出

为了向后兼容，从 src.modules.types 重导出 Intent 和 SourceContext。

新代码应该使用: from src.modules.types import Intent, SourceContext
"""

# 从 modules.types 重导出
from src.modules.types import Intent, SourceContext

__all__ = ["Intent", "SourceContext"]
