"""
Platform Adapter 基类

职责:
- 定义平台适配器的统一接口
- 抽象参数翻译为平台特定参数
- 不包含业务逻辑，仅做平台 API 封装
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class PlatformAdapter(ABC):
    """平台适配器基类

    职责：仅做平台 API 封装，不包含业务逻辑
    原 AvatarAdapter 的精简版本

    抽象参数示例（平台无关）:
    - "smile": 微笑程度 (0.0-1.0)
    - "eye_open": 眼睛开合度 (0.0-1.0)
    - "mouth_open": 嘴巴张开度 (0.0-1.0)
    """

    def __init__(self, adapter_name: str, config: Dict[str, Any]):
        """
        初始化适配器

        Args:
            adapter_name: 适配器名称（如 "vts", "vrc", "live2d"）
            config: 配置字典
        """
        self.adapter_name = adapter_name
        self.config = config
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        """是否已连接到平台"""
        return self._is_connected

    # ==================== 抽象方法（必须实现） ====================

    @abstractmethod
    async def connect(self) -> bool:
        """连接到平台

        Returns:
            是否连接成功
        """
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """断开与平台的连接

        Returns:
            是否断开成功
        """
        pass

    @abstractmethod
    async def set_parameters(self, params: Dict[str, float]) -> bool:
        """设置表情参数（接收抽象参数）

        Args:
            params: 抽象参数字典（如 {"smile": 0.8, "eye_open": 0.9}）

        Returns:
            是否设置成功
        """
        pass

    # ==================== 抽象参数翻译（可选重写） ====================

    def translate_params(self, abstract_params: Dict[str, float]) -> Dict[str, float]:
        """翻译抽象参数为平台特定参数

        子类应该重写此方法以实现参数翻译。

        Args:
            abstract_params: 抽象参数字典

        Returns:
            平台特定参数字典
        """
        # 默认实现：直接返回（假设平台使用相同的参数名）
        return abstract_params.copy()

    # ==================== 辅助方法（子类可用） ====================

    async def _set_parameter_safe(self, param_name: str, value: float) -> bool:
        """安全设置单个参数（子类调用）

        子类可以在 set_parameters 中使用此方法。

        Args:
            param_name: 平台特定参数名
            value: 参数值

        Returns:
            是否设置成功
        """
        # 子类应该实现此方法或直接调用平台 API
        # 这是一个默认实现，子类应该重写它
        return True
