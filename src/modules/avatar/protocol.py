"""虚拟形象 Provider 契约（结构类型，非基类）

各平台适配器（VTS / Warudo / VRChat / 表情差分图）都是面捕合成器的驱动加一层平台连接：
形象怎么动由面捕合成器决定，主播经 ``avatar_act`` 做有意的动作，适配器不向 LLM 暴露工具。
每个适配器另外实现同一组契约方法（``set_expression`` / ``list_preset_actions`` /
``trigger_preset_action``），给调试台按平台直接试表情、列动作、触发动作用。本 ``Protocol``
把这组方法显式化（structural typing，**非 ABC**），用于：

- 契约测试断言 ``isinstance(provider, AvatarProvider)``，防止重构时成员形状意外漂移；
- 新增平台时的规范载体：实现全部成员即满足契约。

设计约束：

- **结构类型，不抽基类**——各平台执行模型差异大（VTS WebSocket 注入、Warudo iFacialMocap
  UDP 加蓝图通道、VRChat OSC 单向写、差分图由小部件页取图），强抽基类 = 抽象泄漏；本协议
  只声明成员形状，不约束实现方式；
- **定位 = 规范载体 + 测试锚点**——``@runtime_checkable`` 的 ``isinstance`` 只在契约测试与
  装配兜底中使用；``set_expression`` 一律交给面捕合成器，各平台不各自翻译情绪。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AvatarProvider(Protocol):
    """虚拟形象 Provider 契约（结构类型，``@runtime_checkable``）。

    任何实现了全部成员的类都被视为满足契约，无需显式继承本协议。
    ``@runtime_checkable`` 只检查成员存在性，不校验类型签名。
    """

    PROVIDER_NAME: str

    @property
    def name(self) -> str: ...

    async def setup(self) -> None: ...

    async def cleanup(self) -> None: ...

    async def set_expression(self, emotion: str, intensity: float) -> object:
        """设置当前情绪（17 枚举值之一，小写）与强度（0.0–1.0），交给面捕合成器。

        返回 ``ToolExecutionResult`` 形态的调用结果（成功/失败 + 结构化载荷）。
        """
        ...

    async def list_preset_actions(self) -> object:
        """列出可演的预设动作目录（返回结构化目录载荷）。"""
        ...

    async def trigger_preset_action(self, action: str) -> object:
        """触发一个预设动作（名取自 ``list_preset_actions`` 返回的目录）。

        未知动作名时把可用目录随失败结果一并返回（失败即发现，自愈）。
        """
        ...


__all__ = ["AvatarProvider"]
