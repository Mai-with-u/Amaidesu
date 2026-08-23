"""
事件拦截器链

``InterceptorChain`` 持有按注册顺序排列的拦截器列表，对单个事件依次应用：
- 拦截器返回 ``dict``（放行）：payload 传给下一个拦截器，最终传给下游 handler
- 拦截器返回 ``None``（丢弃）：立即终止链，返回 ``None``
- 拦截器抛异常：**捕获 + 记录 + 视为 pass-through**（不影响后续拦截器或 handler），
  与"丢事件"语义严格区分——异常不应导致事件被丢弃（事件量小、宁可放过不可错过）

设计原则：
- 链本身持有拦截器列表（按注册顺序）；不复制以减少开销
- ``register`` 追加；``unregister(name)`` 按 ``name`` 去重删除首个匹配
- ``apply`` 是只读操作，不修改链本身
"""

from typing import Any, Dict, List, Optional

from src.modules.events.interceptors.base import EventInterceptor
from src.modules.logging import get_logger


class InterceptorChain:
    """
    拦截器链

    按注册顺序对事件依次应用 ``EventInterceptor.intercept``。
    任何拦截器返回 ``None`` 即终止链并返回 ``None``（事件被丢弃）；
    异常被捕获 + 记录 + 视为 pass-through（不影响后续拦截器或 handler）。

    默认空链的 ``apply`` 直接返回入参 ``payload``（零行为差异）。
    """

    def __init__(self) -> None:
        self._interceptors: List[EventInterceptor] = []
        self.logger = get_logger("InterceptorChain")

    def register(self, interceptor: EventInterceptor) -> None:
        """
        注册一个拦截器（追加到链尾）

        Args:
            interceptor: 已实例化的 ``EventInterceptor`` 子类
        """
        self._interceptors.append(interceptor)
        self.logger.debug(f"注册拦截器: {interceptor.name}")

    def unregister(self, name: str) -> bool:
        """
        按 ``name`` 移除首个匹配的拦截器

        Args:
            name: ``EventInterceptor.name`` 标识

        Returns:
            是否实际移除（``False`` 表示未找到）
        """
        for i, interceptor in enumerate(self._interceptors):
            if interceptor.name == name:
                del self._interceptors[i]
                self.logger.debug(f"移除拦截器: {name}")
                return True
        return False

    def __len__(self) -> int:
        """返回当前拦截器数量（便于测试与监控）"""
        return len(self._interceptors)

    def __bool__(self) -> bool:
        """空链视为 ``False``，便于 ``if bus._interceptor_chain`` 判空"""
        return bool(self._interceptors)

    async def apply(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        """
        顺序应用所有拦截器

        Args:
            event_name: 事件名（具体名）
            payload: 事件数据（``model_dump()`` 后的 dict）
            source: 事件源

        Returns:
            - ``dict``：放行的 payload（可能被链中拦截器原地修改）；handler 将接收此值
            - ``None``：事件被丢弃（任一拦截器返回 ``None`` 立即终止）

        异常处理：拦截器抛出的任何异常都被捕获 + 记录 + 视为 pass-through；
        异常**绝不**导致事件被丢弃。这是与"显式返回 None"的关键区别。
        """
        for interceptor in self._interceptors:
            try:
                result = await interceptor.intercept(event_name, payload, source)
            except Exception as e:
                # 异常隔离：捕获 + 日志 + 视为 pass-through（不影响后续拦截器/handler）
                self.logger.error(
                    f"拦截器 '{interceptor.name}' 执行异常（事件: {event_name}, 来源: {source}），视为 pass-through: {e}",
                    exc_info=True,
                )
                continue
            if result is None:
                # 显式返回 None：丢弃事件，立即终止链
                self.logger.debug(f"拦截器 '{interceptor.name}' 丢弃事件 {event_name}（来源: {source}）")
                return None
            # 返回 dict（即使与入参是同一对象）：保持向后兼容，原地修改亦可
            payload = result
        return payload


__all__ = ["InterceptorChain"]
