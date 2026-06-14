"""Avatar Output Handlers

本模块包含所有虚拟形象相关的 Handler：
- VTSHandler: VTube Studio 虚拟形象 Handler
- WarudoHandler: Warudo 虚拟形象 Handler
- VRChatHandler: VRChat 虚拟形象 Handler
- AvatarHandlerBase: 虚拟形象 Handler 抽象基类

每个 Handler 继承 AvatarHandlerBase 并实现平台特定的适配逻辑。
"""

# 导入子模块以触发 @handler 装饰器注册
from . import (
    vts,  # noqa: F401
    warudo,  # noqa: F401
    vrchat,  # noqa: F401
)

# 导出基类供外部使用
from .base import AvatarHandlerBase

__all__ = ["AvatarHandlerBase"]
