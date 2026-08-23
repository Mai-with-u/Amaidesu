"""
事件拦截器抽象基类

定义 ``EventInterceptor`` 接口，供具体拦截器（rate_limit/similar_filter 等，
W5 实现）继承。

设计要点：
- ``intercept`` 入参 ``payload`` 是 ``dict[str, Any]``（来自 ``model_dump()``），
  **不是** ``BaseModel`` 实例。原因：EventBus 在 ``emit()`` 中已对
  ``BaseModel`` 做 ``model_dump()`` 转为 dict 才进入分发链，拦截器挂在
  同一阶段，与下游 handler 看到一致的数据形态。
- 返回 ``dict`` 表示放行（可原地修改 payload）；返回 ``None`` 表示丢弃事件
- 拦截器应保持无状态或自带隔离状态；并发安全由调用方负责（EventBus 不做加锁）
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class EventInterceptor(ABC):
    """
    事件拦截器抽象基类

    子类需实现：
    - ``name``：拦截器唯一标识（用于 ``InterceptorChain.unregister(name)`` 去重）
    - ``intercept``：核心拦截逻辑；返回 ``dict`` 放行（可修改 payload）、返回
      ``None`` 丢弃事件

    注意：``intercept`` 看到的 ``payload`` 是 ``model_dump()`` 后的 ``dict``，
    而非 ``BaseModel`` 实例——与下游 handler 接收的数据形态一致。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        拦截器唯一标识

        用于 ``InterceptorChain.unregister(name)`` 去重。建议用稳定短串
        （如 "rate_limit"/"similar_filter"），不要带动态时间戳/UUID。
        """
        raise NotImplementedError

    @abstractmethod
    async def intercept(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        """
        拦截 / 处理单个事件

        Args:
            event_name: 事件名（具体名，如 "room.message.danmaku"；不含通配模式）
            payload: 事件数据（``model_dump()`` 后的 dict，可原地修改；**不可替换对象引用**）
            source: 事件源（通常是发布者类名）

        Returns:
            - 返回 ``dict``（可与入参 ``payload`` 是同一对象，原地修改）：事件放行，
              handler 将接收修改后的 payload
            - 返回 ``None``：事件被丢弃，handler **不会** 被调用
        """
        raise NotImplementedError


__all__ = ["EventInterceptor"]
