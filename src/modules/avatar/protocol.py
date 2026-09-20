"""皮套 Provider 契约（结构类型，非基类）

三个皮套后端 Provider（``VTSProvider`` / ``WarudoProvider`` / ``VRChatProvider``）
同语义工具跨后端同名 + 同参数形状（``set_expression`` / ``list_preset_actions``
/ ``trigger_preset_action``），LLM 据此以同一套词汇操纵任意一具皮套。本
``Protocol`` 把这条契约显式化（structural typing，**非 ABC**），用于：

- 契约测试断言 ``isinstance(provider, AvatarProvider)``，防止后续
  重构时成员形状意外漂移；
- 新增皮套后端时的规范载体：实现全部成员即满足契约；
- 适配器内部的自动情绪路径（订阅 ``streamer.speech`` 后的反射）直接调用
  ``set_expression`` 方法实体，与工具调用共用同一条渲染入口。

设计约束（对齐 TTS Provider 协议先例）：

- **结构类型，不抽基类**——三后端执行模型差异大（VTS WebSocket 推参数
  / Warudo 状态件 + 蓝图动作 / VRChat OSC 单向写），强抽基类 = 抽象泄漏；
  本协议只声明成员形状，不约束实现方式；
- **定位 = 规范载体 + 测试锚点，非运行时接缝**——没有"以同一接口对待
  任意后端"的多态调用方（LLM 看到的是各自前缀的工具），``@runtime_checkable``
  的 ``isinstance`` 只在契约测试与装配兜底中使用；
- 同语义跨后端**同名 + 同参数形状**是 LLM 面契约（``set_expression(emotion,
  intensity)`` 三后端一致）；情绪到本平台参数的解析是适配器实现自由，
  架构不规定映射结果。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AvatarProvider(Protocol):
    """皮套 Provider 契约（结构类型，``@runtime_checkable``）。

    任何实现了全部成员的类都被视为满足契约，无需显式继承本协议。
    ``@runtime_checkable`` 只检查成员存在性，不校验类型签名。
    """

    PROVIDER_NAME: str

    @property
    def name(self) -> str: ...

    async def setup(self) -> None: ...

    async def cleanup(self) -> None: ...

    async def set_expression(self, emotion: str, intensity: float) -> object:
        """设置当前情绪（17 枚举值之一，小写）与强度（0.0–1.0）。

        同语义跨后端同名 + 同参数形状；返回 ``ToolExecutionResult`` 形态
        的调用结果（成功/失败 + 结构化载荷）。
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
