"""
服务模块 - 共享基础设施服务管理

提供统一的服务注册和访问接口。
所有非 Provider 的共享服务都应该通过服务管理器访问。

可用服务:
- llm: LLM 服务 (LLMManager)
- dg_lab: DG-LAB 硬件控制服务 (DGLabService)
- config: 配置服务 (ConfigService)
- context: 上下文管理器 (ContextManager)

使用示例:
    ```python
    from src.services import get_service, has_service

    # 检查服务是否可用
    if has_service("dg_lab"):
        # 获取服务实例
        dg_lab = get_service("dg_lab")
        await dg_lab.trigger_shock(strength=10)

    # 或者使用服务管理器
    from src.services.manager import ServiceManager

    manager = ServiceManager()
    manager.register("dg_lab", dg_lab_service)
    service = manager.get("dg_lab")
    ```
"""

from typing import Optional, Any

from .manager import ServiceManager, get_global_service_manager

__all__ = [
    "ServiceManager",
    "get_global_service_manager",
    "get_service",
    "has_service",
    "list_services",
]


def get_service(name: str) -> Optional[Any]:
    """
    获取全局服务实例

    Args:
        name: 服务名称

    Returns:
        服务实例，如果服务不存在则返回 None

    示例:
        ```python
        dg_lab = get_service("dg_lab")
        if dg_lab:
            await dg_lab.trigger_shock(strength=10)
        ```
    """
    manager = get_global_service_manager()
    return manager.get(name)


def has_service(name: str) -> bool:
    """
    检查全局服务是否已注册

    Args:
        name: 服务名称

    Returns:
        bool: 服务是否已注册

    示例:
        ```python
        if has_service("dg_lab"):
            service = get_service("dg_lab")
        ```
    """
    manager = get_global_service_manager()
    return manager.has(name)


def list_services() -> list[str]:
    """
    列出所有已注册的全局服务名称

    Returns:
        服务名称列表
    """
    manager = get_global_service_manager()
    return manager.list_services()
