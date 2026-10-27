"""调用方对 LLMManager 能力查询的显式依赖协议。

调用方需要的不是整个 LLMManager，而是其中一两个查询方法（如"该 profile 能否
看图"）。把它们声明成 Protocol 后，依赖缺方法在装配期就被校验拦下，而不是
运行期靠 getattr 探测静默退化（例如视觉查询静默 False 后 planner 再也不看屏，
且无任何日志）。同模块先例见 quota/service.py 的 ``ModelProviderLookup``。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ProfileContextWindowLookup(Protocol):
    """能按 profile 名查询可用上下文窗口（LLMManager.get_profile_context_window 满足）。"""

    def get_profile_context_window(self, profile: Optional[str]) -> int: ...


@runtime_checkable
class ProfileVisualCapabilityLookup(Protocol):
    """能按 profile 名查询视觉能力开关（LLMManager.is_profile_visual 满足）。"""

    def is_profile_visual(self, profile: Optional[str]) -> bool: ...


def validate_llm_capability(dependency: object, protocol: type, *, parameter: str) -> None:
    """装配期校验依赖实现了协议要求的方法；缺方法即报错点名。

    dependency 为 None 放行——依赖缺席由各调用方自己的"未注入"语义处理，
    这里只管"注入了但不像样"的情形。校验放各组件构造器（依赖注入处）：
    引擎方法改名/被删时启动即失败，而不是运行期静默退化到 False/0。

    按方法名逐个检查而非 isinstance：runtime_checkable 的 isinstance 对
    MagicMock 等宽松替身会误判不通过，方法存在性检查对真件与替身同样成立。
    """
    if dependency is None:
        return
    # Protocol 类的 vars 只含协议声明的方法与下划线内部标记，过滤后即要求面
    missing = [
        name for name in vars(protocol) if not name.startswith("_") and not callable(getattr(dependency, name, None))
    ]
    if missing:
        raise TypeError(
            f"依赖 {parameter!r} 未实现 {protocol.__name__} 要求的方法：{', '.join(missing)}；"
            f"请注入满足该协议的对象（生产环境为 LLMManager）"
        )
