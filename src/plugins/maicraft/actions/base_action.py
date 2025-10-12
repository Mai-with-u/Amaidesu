from abc import ABC, abstractmethod
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..impl.action_executor_interface import ActionExecutor


class BaseAction(ABC):
    """
    行动基类，定义所有行动的基础接口。
    每个具体的行动都应该继承此类并实现相应的方法。
    """

    @abstractmethod
    async def execute(self, params: Dict[str, Any], executor: "ActionExecutor") -> bool:
        """
        执行行动。

        Args:
            params: 行动参数字典
            executor: 行动执行器实例

        Returns:
            执行是否成功
        """
        pass

    @abstractmethod
    def get_action_id(self) -> str:
        """
        获取行动标识（独立于命令名称）。

        Returns:
            行动的唯一标识符
        """
        pass

    def validate_params(self, params: Dict[str, Any]) -> bool:
        """
        验证参数是否有效。
        子类可以重写此方法来实现自定义的参数验证逻辑。

        Args:
            params: 要验证的参数字典

        Returns:
            参数是否有效
        """
        return True

    def get_required_params(self) -> list[str]:
        """
        获取必需的参数列表。
        子类可以重写此方法来指定必需的参数。

        Returns:
            必需参数名称列表
        """
        return []

    def validate_required_params(self, params: Dict[str, Any]) -> bool:
        """
        验证必需参数是否存在。

        Args:
            params: 参数字典

        Returns:
            必需参数是否都存在
        """
        required_params = self.get_required_params()
        for param_name in required_params:
            if param_name not in params:
                return False
            # 检查参数值是否为空
            param_value = params[param_name]
            if param_value is None or (isinstance(param_value, str) and not param_value.strip()):
                return False
        return True
