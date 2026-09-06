"""
事件拦截器框架

提供 EventBus 分发前的可插拔处理层。具体拦截器实例：
- ``rate_limit.RateLimitInterceptor`` —— 滑动窗口限流
- ``similar_filter.SimilarFilterInterceptor`` —— 相似文本过滤
- ``session_stamp.SessionStampInterceptor`` —— 场次归属盖章（业务事件注入当前场次主键）

模块结构：
- base.py：``EventInterceptor`` 抽象基类
- chain.py：``InterceptorChain`` 链式容器（顺序应用 + 异常隔离 + 丢弃传播）
- rate_limit.py / similar_filter.py：限流与相似过滤两个具体拦截器实现
- session_stamp.py：场次盖章拦截器（LiveSessionManager 单点注入，全链一致）

EventBus 集成：通过 ``EventBus.add_interceptor`` / ``remove_interceptor``
将拦截器挂到内部 ``InterceptorChain``；``emit()`` 在数据验证后、handler
分发前调用 ``chain.apply()``，返回 ``None`` 则日志 + 直接 return 不分发。

注意：拦截器看到的是 ``model_dump()`` 后的 dict，与下游 handler 接收的数据形态一致。
"""

from src.modules.events.interceptors.base import EventInterceptor
from src.modules.events.interceptors.chain import InterceptorChain
from src.modules.events.interceptors.rate_limit import RateLimitInterceptor
from src.modules.events.interceptors.session_stamp import SessionStampInterceptor
from src.modules.events.interceptors.similar_filter import SimilarFilterInterceptor

__all__ = [
    "EventInterceptor",
    "InterceptorChain",
    "RateLimitInterceptor",
    "SessionStampInterceptor",
    "SimilarFilterInterceptor",
]
