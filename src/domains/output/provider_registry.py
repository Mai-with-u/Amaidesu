"""
Provider Registry - 向后兼容模块

此模块保留是为了向后兼容，实际实现已移至 src.core.provider_registry

所有导入应该使用: from src.core.provider_registry import ProviderRegistry
"""

import warnings

# 从核心模块重新导出 ProviderRegistry
from src.core.provider_registry import ProviderRegistry

# 发出弃用警告
warnings.warn(
    "Importing ProviderRegistry from src.domains.output.provider_registry is deprecated. "
    "Use 'from src.core.provider_registry import ProviderRegistry' instead. "
    "This backward compatibility import will be removed in a future version.",
    DeprecationWarning,
    stacklevel=2
)

__all__ = ["ProviderRegistry"]
