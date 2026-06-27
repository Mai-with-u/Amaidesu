"""
依赖注入（DI）模块。

提供共享的实例化工具，支持按类型注解匹配服务依赖。
"""

from src.modules.di.instantiation import (
    DependencyInjectionError,
    instantiate_with_di,
)

__all__ = [
    "DependencyInjectionError",
    "instantiate_with_di",
]
