"""
阶段 Manager 抽象协议

定义 Dashboard 模块与阶段层 Manager 之间的最小接口，
避免 Dashboard 反向依赖具体的阶段层实现类。

协议使用 Dict 返回类型避免 Dashboard 与阶段层循环依赖：
阶段层 Manager 不引入 Dashboard 的 ComponentSummary 类型，
由 Dashboard 自行将 dict 适配为 ComponentSummary。
"""

from typing import Any, Dict, List, Protocol, runtime_checkable


@runtime_checkable
class ManagerStatusProvider(Protocol):
    """阶段 Manager 状态提供者协议

    任何实现此协议的 Manager 都可被 Dashboard 使用，
    用于统一查询各阶段参与者的状态摘要。

    返回的字典应至少包含以下字段（Dashboard 适配为 ComponentSummary）：
        - name: 参与者名称
        - is_started: 是否已启动
        - phase: 所属阶段（input / decision / output）
        - type: 参与者类型（collector / decider / handler）
    """

    def get_component_summaries(self) -> List[Dict[str, Any]]:
        """返回当前阶段所有参与者的状态摘要字典列表"""
        ...
