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

    def get_collectors(self) -> List[Any]:
        """返回所有已加载的 Collector 实例列表（供 Dashboard 子路由查询）"""
        ...

    # ==================== 控制接口（供 Dashboard 组件管理页调用） ====================

    def get_collector_by_name(self, name: str) -> Any:
        """按注册名查找已加载的 Collector 实例"""
        ...

    async def enable_collector(self, name: str, config_service=None) -> bool:
        """动态启用 Collector 并启动（若 Manager 已运行）"""
        ...

    async def disable_collector(self, name: str) -> bool:
        """动态停用 Collector：取消任务、停止实例并移除"""
        ...

    async def start_collector(self, collector: Any) -> bool:
        """启动（或重启）单个 Collector 的运行任务"""
        ...

    async def stop_collector(self, collector: Any) -> bool:
        """停止单个 Collector：取消运行任务并调用 stop()"""
        ...

    def get_available_deciders(self) -> List[str]:
        """返回所有已注册的 Decider 名称"""
        ...

    async def enable_decider(self, name: str, config: Dict[str, Any]) -> bool:
        """动态启用 Decider"""
        ...

    async def disable_decider(self, name: str) -> bool:
        """动态停用 Decider"""
        ...

    def get_handler_by_name(self, name: str) -> Any:
        """按名称查找已加载的 Handler 实例"""
        ...

    async def enable_handler(self, name: str, config_service=None) -> bool:
        """动态启用 Handler"""
        ...

    async def disable_handler(self, name: str) -> bool:
        """动态停用 Handler"""
        ...
