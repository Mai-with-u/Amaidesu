from abc import ABC, abstractmethod
from typing import Dict, Any


class ActionExecutor(ABC):
    """
    行动执行器接口，定义了执行具体行动的抽象方法。
    不同的实现可以提供不同的执行方式（如日志输出、真实游戏控制等）。
    """

    @abstractmethod
    async def execute_action(self, action_id: str, params: Dict[str, Any]) -> bool:
        """
        执行指定的行动。

        Args:
            action_id: 行动标识符（如 "minecraft_chat"）
            params: 行动参数字典

        Returns:
            执行是否成功
        """
        pass

    async def initialize(self) -> bool:
        """
        初始化执行器。
        子类可以重写此方法来进行必要的初始化操作。

        Returns:
            初始化是否成功
        """
        return True

    async def cleanup(self):
        """
        清理执行器资源。
        子类可以重写此方法来进行资源清理。
        """
        pass

    def get_executor_type(self) -> str:
        """
        获取执行器类型标识。

        Returns:
            执行器类型字符串
        """
        return self.__class__.__name__.lower().replace("actionexecutor", "")
