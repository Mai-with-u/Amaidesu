# 行动系统模块
import importlib
import inspect
from typing import Dict, Type, Set, Optional
from .base_action import BaseAction
from src.utils.logger import get_logger


class ActionDiscoverer:
    """
    动作发现器，用于自动发现和注册所有可用的动作类。
    """

    def __init__(self):
        self.logger = get_logger("ActionDiscoverer")
        self._discovered_actions: Dict[str, Type[BaseAction]] = {}

    def discover_actions(self, package_path: str = None) -> Dict[str, Type[BaseAction]]:
        """
        发现指定包路径下的所有动作类。

        Args:
            package_path: 要扫描的包路径

        Returns:
            动作标识到动作类的映射字典
        """
        # 如果没有指定包路径，使用当前模块的包路径
        if package_path is None:
            package_path = __name__  # 使用当前模块的完整包路径
            self.logger.debug(f"使用默认包路径: {package_path}")

        try:
            # 导入包
            package = importlib.import_module(package_path)

            # 获取包中的所有模块
            if hasattr(package, "__path__"):
                import pkgutil

                for _importer, modname, ispkg in pkgutil.iter_modules(package.__path__):
                    if not ispkg and modname not in ["__init__", "base_action"]:
                        try:
                            module = importlib.import_module(f"{package_path}.{modname}")
                            self._scan_module_for_actions(module)
                        except Exception as e:
                            self.logger.warning(f"扫描模块 {modname} 时出错: {e}")

            self.logger.info(f"发现 {len(self._discovered_actions)} 个动作类: {list(self._discovered_actions.keys())}")
            return self._discovered_actions.copy()

        except Exception as e:
            self.logger.error(f"发现动作类时出错: {e}")
            return {}

    def _scan_module_for_actions(self, module) -> None:
        """
        扫描模块中的所有动作类。

        Args:
            module: 要扫描的模块
        """
        for _name, obj in inspect.getmembers(module):
            if inspect.isclass(obj) and issubclass(obj, BaseAction) and obj != BaseAction:
                # 获取动作标识
                try:
                    action_id = obj().get_action_id()
                    self._discovered_actions[action_id] = obj
                    self.logger.debug(f"发现动作类: {action_id} -> {obj.__name__}")
                except Exception as e:
                    self.logger.warning(f"获取动作标识失败 {obj.__name__}: {e}")

    def get_action_class(self, action_id: str) -> Optional[Type[BaseAction]]:
        """
        根据动作标识获取动作类。

        Args:
            action_id: 动作标识

        Returns:
            对应的动作类，如果未找到则返回None
        """
        return self._discovered_actions.get(action_id)

    def get_available_actions(self) -> Set[str]:
        """
        获取所有可用的动作标识。

        Returns:
            可用动作标识的集合
        """
        return set(self._discovered_actions.keys())
