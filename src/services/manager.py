"""
服务管理器 - 统一的服务注册和生命周期管理

提供服务的注册、获取和生命周期管理功能。
所有非 Provider 的共享服务都应该通过服务管理器访问。

使用示例:
    ```python
    from src.services.manager import ServiceManager

    # 创建服务管理器
    service_manager = ServiceManager()

    # 注册服务
    service_manager.register("dg_lab", dg_lab_service)

    # 获取服务
    dg_lab = service_manager.get("dg_lab")

    # 检查服务是否可用
    if service_manager.has("dg_lab"):
        service = service_manager.get("dg_lab")

    # 清理所有服务
    await service_manager.cleanup_all()
    ```
"""

from typing import Dict, Optional, Any, Protocol, List
from src.core.utils.logger import get_logger


class ServiceProtocol(Protocol):
    """服务协议 - 所有注册的服务都应该实现这些方法"""

    async def setup(self) -> None:
        """初始化服务"""
        ...

    async def cleanup(self) -> None:
        """清理服务资源"""
        ...

    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        ...


class ServiceManager:
    """
    服务管理器 - 统一的服务注册表

    职责：
    - 服务注册和获取
    - 服务生命周期管理
    - 服务状态查询

    设计原则：
    - 服务通过名称注册
    - 服务需要实现 setup/cleanup/is_ready 生命周期方法
    - 支持可选服务（注册时可能为 None）
    """

    def __init__(self):
        """初始化服务管理器"""
        self.logger = get_logger("ServiceManager")
        self._services: Dict[str, Any] = {}
        self._service_configs: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        name: str,
        service: Any,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        注册服务

        Args:
            name: 服务名称（唯一标识符）
            service: 服务实例
            config: 服务配置（可选，用于记录）
        """
        if name in self._services:
            self.logger.warning(f"服务 '{name}' 已存在，将被覆盖")

        self._services[name] = service
        if config:
            self._service_configs[name] = config

        self.logger.info(f"服务 '{name}' 已注册")

    def get(self, name: str) -> Optional[Any]:
        """
        获取服务实例

        Args:
            name: 服务名称

        Returns:
            服务实例，如果服务不存在则返回 None
        """
        return self._services.get(name)

    def has(self, name: str) -> bool:
        """
        检查服务是否已注册

        Args:
            name: 服务名称

        Returns:
            bool: 服务是否已注册
        """
        return name in self._services

    def is_ready(self, name: str) -> bool:
        """
        检查服务是否就绪

        Args:
            name: 服务名称

        Returns:
            bool: 服务是否就绪（已注册且实现了 is_ready 方法）
        """
        service = self._services.get(name)
        if service is None:
            return False

        # 如果服务实现了 is_ready 方法，调用它
        if hasattr(service, "is_ready"):
            return service.is_ready()

        # 否则认为只要注册了就是就绪的
        return True

    def list_services(self) -> List[str]:
        """
        列出所有已注册的服务名称

        Returns:
            服务名称列表
        """
        return list(self._services.keys())

    def get_service_info(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有服务的信息

        Returns:
            服务信息字典，包含就绪状态
        """
        return {
            name: {
                "ready": self.is_ready(name),
                "config": self._service_configs.get(name, {}),
            }
            for name in self._services.keys()
        }

    async def setup_service(self, name: str) -> bool:
        """
        初始化单个服务

        Args:
            name: 服务名称

        Returns:
            bool: 是否成功初始化
        """
        service = self._services.get(name)
        if service is None:
            self.logger.error(f"服务 '{name}' 不存在，无法初始化")
            return False

        if hasattr(service, "setup"):
            try:
                await service.setup()
                self.logger.info(f"服务 '{name}' 初始化完成")
                return True
            except Exception as e:
                self.logger.error(f"初始化服务 '{name}' 失败: {e}", exc_info=True)
                return False
        else:
            self.logger.debug(f"服务 '{name}' 没有实现 setup 方法，跳过初始化")
            return True

    async def cleanup_service(self, name: str) -> bool:
        """
        清理单个服务

        Args:
            name: 服务名称

        Returns:
            bool: 是否成功清理
        """
        service = self._services.get(name)
        if service is None:
            self.logger.warning(f"服务 '{name}' 不存在，无法清理")
            return False

        if hasattr(service, "cleanup"):
            try:
                await service.cleanup()
                self.logger.info(f"服务 '{name}' 清理完成")
                return True
            except Exception as e:
                self.logger.error(f"清理服务 '{name}' 失败: {e}", exc_info=True)
                return False
        else:
            self.logger.debug(f"服务 '{name}' 没有实现 cleanup 方法，跳过清理")
            return True

    async def setup_all(self) -> None:
        """初始化所有已注册的服务"""
        self.logger.info(f"开始初始化 {len(self._services)} 个服务...")
        for name in self._services.keys():
            await self.setup_service(name)
        self.logger.info("所有服务初始化完成")

    async def cleanup_all(self) -> None:
        """清理所有已注册的服务"""
        self.logger.info(f"开始清理 {len(self._services)} 个服务...")
        # 按照相反的顺序清理服务（后注册的先清理）
        for name in reversed(list(self._services.keys())):
            await self.cleanup_service(name)
        self.logger.info("所有服务清理完成")

    def unregister(self, name: str) -> bool:
        """
        注销服务

        Args:
            name: 服务名称

        Returns:
            bool: 是否成功注销
        """
        if name not in self._services:
            self.logger.warning(f"服务 '{name}' 不存在，无法注销")
            return False

        del self._services[name]
        if name in self._service_configs:
            del self._service_configs[name]

        self.logger.info(f"服务 '{name}' 已注销")
        return True


# 全局服务管理器实例（可选，用于简单场景）
_global_service_manager: Optional[ServiceManager] = None


def get_global_service_manager() -> ServiceManager:
    """
    获取全局服务管理器实例

    Returns:
        ServiceManager: 全局服务管理器
    """
    global _global_service_manager
    if _global_service_manager is None:
        _global_service_manager = ServiceManager()
    return _global_service_manager
