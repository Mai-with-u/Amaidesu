"""Avatar Output Provider

本模块包含所有虚拟形象相关的 Provider：
- VTSProvider: VTube Studio 虚拟形象 Provider
- WarudoOutputProvider: Warudo 虚拟形象 Provider
- AvatarProviderBase: 虚拟形象 Provider 抽象基类

每个 Provider 继承 AvatarProviderBase 并实现平台特定的适配逻辑。
"""

# 导入子模块以触发注册
from . import vts  # noqa: F401
from . import warudo  # noqa: F401

# 导出基类供外部使用
from .base import AvatarProviderBase

__all__ = ["AvatarProviderBase"]
