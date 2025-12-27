"""
虚拟形象适配器基类

定义了所有平台适配器必须实现的统一接口。
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from typing import Literal


@dataclass
class ParameterMetadata:
    """参数元数据

    描述一个可控制的参数，包括其名称、范围、类型等信息。
    """

    name: str  # 参数名称（平台特定，如 "EyeOpenLeft" 或 "/input/face/eyes"）
    display_name: str  # 显示名称（中文，如 "左眼开合度"）
    param_type: Literal["float", "int", "bool", "string"]  # 类型: "float", "int", "bool", "string"
    min_value: float = 0.0  # 最小值
    max_value: float = 1.0  # 最大值
    default_value: float = 0.0  # 默认值
    description: str = ""  # 参数描述
    category: Literal["face", "body", "expression", "camera", "general"] = (
        "general"  # 分类: "face", "body", "expression", "camera", etc.
    )
    tags: List[str] = field(default_factory=list)  # 标签: ["eye", "mouth", "emotion"]

    def __post_init__(self):
        if isinstance(self.tags, list):
            pass  # 已经是列表
        elif self.tags is None:
            self.tags = []
        else:
            self.tags = list(self.tags)


@dataclass
class ActionMetadata:
    """动作元数据

    描述一个可触发的预设动作（如 VTS 热键、VRChat Gesture 等）。
    """

    name: str  # 动作名称
    display_name: str  # 显示名称（中文）
    description: str  # 动作描述
    category: Literal["expression", "gesture", "animation", "general"] = (
        "general"  # 分类: "expression", "gesture", "animation", etc.
    )
    parameters: Dict[str, Any] = field(default_factory=dict)  # 默认参数值

    def __post_init__(self):
        if isinstance(self.parameters, dict):
            pass
        elif self.parameters is None:
            self.parameters = {}
        else:
            self.parameters = dict(self.parameters)


class AvatarAdapter(ABC):
    """虚拟形象适配器基类

    所有平台适配器必须继承此类并实现抽象方法。
    适配器负责将统一接口转换为特定平台的 API 调用。

    典型实现流程：
        1. 在构造函数中调用 register_parameter() 注册所有可控制的参数
        2. 实现 connect() 建立与平台的连接
        3. 实现 set_parameter() 将参数值传递给平台
        4. 实现 trigger_action() 触发平台的预设动作
    """

    def __init__(self, adapter_name: str, config: Dict[str, Any]):
        """
        初始化适配器

        Args:
            adapter_name: 适配器名称（如 "vts", "vrc", "live2d"）
            config: 适配器配置字典
        """
        self.adapter_name = adapter_name
        self.config = config
        self._parameters: Dict[str, ParameterMetadata] = {}
        self._actions: Dict[str, ActionMetadata] = {}
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        """是否已连接到平台"""
        return self._is_connected

    def register_parameter(self, metadata: ParameterMetadata) -> None:
        """注册参数元数据

        Args:
            metadata: 参数元数据对象
        """
        self._parameters[metadata.name] = metadata

    def register_action(self, metadata: ActionMetadata) -> None:
        """注册动作元数据

        Args:
            metadata: 动作元数据对象
        """
        self._actions[metadata.name] = metadata

    def get_registered_parameters(self) -> Dict[str, ParameterMetadata]:
        """获取已注册的参数

        Returns:
            参数名到元数据的映射
        """
        return self._parameters.copy()

    def get_registered_actions(self) -> Dict[str, ActionMetadata]:
        """获取已注册的动作

        Returns:
            动作名到元数据的映射
        """
        return self._actions.copy()

    # ==================== 抽象方法 ====================

    @abstractmethod
    async def connect(self) -> bool:
        """连接到虚拟形象平台

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
    async def set_parameter(self, param_name: str, value: float) -> bool:
        """设置单个参数值

        Args:
            param_name: 参数名称
            value: 参数值

        Returns:
            是否设置成功
        """
        pass

    @abstractmethod
    async def set_parameters(self, parameters: Dict[str, float]) -> bool:
        """批量设置参数值

        Args:
            parameters: 参数名到值的映射

        Returns:
            是否全部设置成功
        """
        pass

    @abstractmethod
    async def get_parameter(self, param_name: str) -> Optional[float]:
        """获取参数当前值

        Args:
            param_name: 参数名称

        Returns:
            参数值，如果获取失败返回 None
        """
        pass

    @abstractmethod
    async def trigger_action(self, action_name: str, **kwargs) -> bool:
        """触发预设动作

        Args:
            action_name: 动作名称
            **kwargs: 动作参数

        Returns:
            是否触发成功
        """
        pass
