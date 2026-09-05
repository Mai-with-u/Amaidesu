"""TTS 引擎 Provider 协议（结构类型契约）

四个 TTS 引擎 Provider（``EdgeTTSProvider`` / ``GPTSoVITSProvider`` /
``VoiceboxProvider`` / ``OmniTTSProvider``）由装配层单选构造，注入
``StreamerAgent`` 后由编排队列持有并直接调用其 ``handle_speech``。本
``Protocol`` 把这条 duck-typed 契约显式化（structural typing，**非
ABC**），用于：

- 装配入口 ``build_tts_infrastructure`` 在工厂返回后做 ``isinstance``
  fail-fast 校验，避免结构不匹配对象（如意外返回 ``None`` / ``dict`` /
  缺方法的实例）被注入决策循环下游；
- ``StreamerAgent`` 构造参数 ``tts_engine: Optional[TTSProvider]`` 类型
  收窄，IDE / mypy 能在调用点直接看到 ``handle_speech`` 签名；
- 测试统一断言 ``isinstance(provider, TTSProvider)``，证明装配结果满
  足契约（防止后续重构时契约意外漂移）。

设计约束（与 ADR-007 一致）：

- **结构类型，不抽基类**——历史决策明确拒绝 ``BaseTTSProvider`` 抽象类
  抽取（Provider 执行模型差异大，强抽基类 = 抽象泄漏），本协议只
  声明成员形状，不约束实现方式；
- **不引入运行时注册表 / Manager**——装配由 ``build_tts_infrastructure``
  静态映射完成，不存在"协议找实现"的运行时发现；
- **调用方零耦合**——使用方通过 ``TYPE_CHECKING`` 导入本协议，避免
  StreamerAgent 等上游模块在运行期对 TTS 包产生强依赖；
- **运行时检查仅用于 fail-fast 校验**——``@runtime_checkable`` 让
  ``isinstance`` 在装配边界兜底，常规调用路径仍走静态类型收窄。

成员（与 ADR-007 契约一一对应）：

- ``PROVIDER_NAME: str`` 类级标识
- ``name: str`` 实例属性（典型实现为 ``@property`` 返回 ``PROVIDER_NAME``）
- ``async def setup(self) -> None`` 生命周期（幂等，重复调用早退）
- ``async def cleanup(self) -> None`` 生命周期
- ``async def handle_speech(text, utterance_id=None) -> None`` 唯一业务入口
- ``def get_stats(self) -> Dict[str, Any]`` 状态查询
- ``class ConfigSchema(BaseConfig)`` 嵌套配置 Schema

参考模式：``src/modules/tools/provider.py`` 的 ``ToolProvider(Protocol)``。
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional, Protocol, runtime_checkable

from src.modules.config.schemas.base import BaseConfig


@runtime_checkable
class TTSProvider(Protocol):
    """TTS 引擎 Provider 协议（结构类型，``@runtime_checkable``）。

    任何实现了全部成员（``PROVIDER_NAME`` / ``name`` / ``setup`` /
    ``cleanup`` / ``handle_speech`` / ``get_stats`` / ``ConfigSchema``）
    的类都被视为满足契约，无需显式继承本协议——这是 Python 结构类型
    系统的 duck-typed 风格，配套 ADR-007 的"不抽基类"决策。

    装配层 ``build_tts_infrastructure`` 在工厂返回后用
    ``isinstance(engine, TTSProvider)`` 兜底校验；调用方按
    ``TTSProvider`` 静态收窄类型。``@runtime_checkable`` 只检查成员
    存在性，不校验类型签名——避免给鸭子类型增加运行期负担。
    """

    # ----- 类级成员（属性查找走 MRO，实例访问可见）-----

    PROVIDER_NAME: ClassVar[str]
    """引擎标识（与 ``name`` 同值；类级常量便于配置 / 日志直接读取）。"""

    ConfigSchema: ClassVar[type[BaseConfig]]
    """嵌套配置 Schema。``multi_file_loader`` 通过模块路径字符串延迟加
    载各 Provider 的 ``ConfigSchema`` 用于配置补全，不与工具注册耦合。"""

    # ----- 实例成员 -----

    @property
    def name(self) -> str:
        """引擎标识（典型实现：``return self.PROVIDER_NAME``）。

        用 ``@property`` 形式声明，便于实现侧按需返回派生值。
        """
        ...

    async def setup(self) -> None:
        """初始化引擎（幂等，重复调用早退）。

        装配层在工厂返回后**不**主动调用 ``setup``；调用方
        （编排队列）首次 ``handle_speech`` 时由引擎自治入口 ensure_setup。
        """
        ...

    async def cleanup(self) -> None:
        """释放引擎资源（幂等，未启动早退）。

        Agent 停止时由装配根统一遍历调用；未启动时早退避免重复清理。
        """
        ...

    async def handle_speech(self, text: str, utterance_id: Optional[str] = None) -> None:
        """合成并播放语音——TTS 引擎唯一业务入口。

        Args:
            text: 要合成的文本。
            utterance_id: 一次发声实例的唯一 ID；非 None 时按
                ``tts.utterance.*`` 生命周期发布事件；None 时静默跳过
                事件发布（手动 / 直调场景无 utterance 上下文）。

        错误语义：合成失败时**主动发** ``tts.utterance.failed`` 事件并
        向上 raise，由编排队列兜底。
        """
        ...

    def get_stats(self) -> Dict[str, Any]:
        """返回引擎状态统计 dict。

        字段约定（与 ``build_stats_dict`` 一致）：``name`` /
        ``is_connected`` / ``render_count`` / ``error_count``；个别
        引擎可经 ``extra`` 注入额外字段（如 OmniTTS 的 ``buffer_size``）。
        """
        ...


__all__ = ["TTSProvider"]
