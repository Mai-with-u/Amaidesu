"""TTS 基础设施装配入口

按核心 ``[tts]`` 配置段构造选中的 TTS 引擎 Provider 实例，供
``StreamerAgent`` 直接持有并调用其 ``handle_speech``。

设计要点
--------

- **基础模块自治装配**——TTS 是基础设施（非工具），装配逻辑放在
  ``src/modules/tts/`` 包内自治完成，不经过工具层
- **装配适配行统一**——引擎工厂仅持真实依赖（形状各异，不为准统一而
  收无用参数）；本模块为每个引擎提供一行装配适配函数（``_assemble_*``），
  统一接受 ``(config, event_bus, audio_sink, llm_service)`` 并决定向该
  引擎工厂转发什么，注册表登记适配行，调用点无分支。``llm_service``
  （LLMManager）仅 omni_tts 适配行转发消费
- **fail-soft 语义**——``enabled=False`` / 缺段 / 子配置构造异常时统一返回
  ``None``，调用方按"未装配"语义降级；未知 ``provider`` 名记 ERROR 并回退
  到 ``edge_tts``（装配兜底）
- **已知 provider**——``edge_tts`` / ``gptsovits`` / ``voicebox`` /
  ``omni_tts``（全模态大模型语音合成，OpenAI 兼容线协议）；其余名字一律
  视作未知
"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from src.modules.logging import get_logger

from .backends.edge.provider import create_edge_tts_provider
from .backends.gptsovits.provider import create_gptsovits_provider
from .backends.omni.provider import create_omni_tts_provider
from .protocol import TTSProvider
from .backends.voicebox.provider import create_voicebox_provider

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus


# --- 装配适配行：注册表统一契约的执行点 ------------------------------
# 引擎工厂只持真实依赖；"共享装配上下文中的哪些东西喂给哪个引擎"由各
# 适配行决定。四行统一签名 (config, event_bus, audio_sink, llm_service)，


def _assemble_edge_tts(
    config: Dict[str, Any],
    event_bus: Optional["EventBus"],
    audio_sink: Optional[Any],
    llm_service: Optional[Any],
) -> TTSProvider:
    """edge_tts 装配行：不消费 LLM 服务。"""
    return create_edge_tts_provider(config, event_bus=event_bus, audio_sink=audio_sink)


def _assemble_gptsovits(
    config: Dict[str, Any],
    event_bus: Optional["EventBus"],
    audio_sink: Optional[Any],
    llm_service: Optional[Any],
) -> TTSProvider:
    """gptsovits 装配行：不消费 LLM 服务。"""
    return create_gptsovits_provider(config, event_bus=event_bus, audio_sink=audio_sink)


def _assemble_voicebox(
    config: Dict[str, Any],
    event_bus: Optional["EventBus"],
    audio_sink: Optional[Any],
    llm_service: Optional[Any],
) -> TTSProvider:
    """voicebox 装配行：不消费 LLM 服务。"""
    return create_voicebox_provider(config, event_bus=event_bus, audio_sink=audio_sink)


def _assemble_omni_tts(
    config: Dict[str, Any],
    event_bus: Optional["EventBus"],
    audio_sink: Optional[Any],
    llm_service: Optional[Any],
) -> TTSProvider:
    """omni_tts 装配行：LLM 服务是本引擎真实依赖，透传给工厂。"""
    return create_omni_tts_provider(config, event_bus=event_bus, audio_sink=audio_sink, llm_service=llm_service)


# provider 名 → 装配适配行（见上方 _assemble_*）；本映射只是注册关系。
_PROVIDER_FACTORIES = {
    "edge_tts": _assemble_edge_tts,
    "gptsovits": _assemble_gptsovits,
    "voicebox": _assemble_voicebox,
    "omni_tts": _assemble_omni_tts,
}

# TTS 引擎名单的唯一事实源 = 装配表键集：schema 的候选选项（读侧注入）与
# 装配校验同源，加引擎只改装配表一处。装配时按 [tts].provider 单选；
# 未知 provider 回退到 edge_tts 并记 ERROR 日志。
TTS_PROVIDER_NAMES: tuple[str, ...] = tuple(sorted(_PROVIDER_FACTORIES))
_KNOWN_PROVIDERS = frozenset(_PROVIDER_FACTORIES)

# provider 名 → 中文描述（用于日志/文档）
_PROVIDER_DESCRIPTIONS = {
    "edge_tts": "Edge TTS 语音合成",
    "gptsovits": "GPT-SoVITS 本地 TTS",
    "voicebox": "Voicebox TTS",
    "omni_tts": "全模态大模型语音合成（OpenAI 兼容）",
}


def build_tts_infrastructure(
    tts_config: Any,
    event_bus: Optional["EventBus"] = None,
    audio_sink: Optional[Any] = None,
    llm_service: Optional[Any] = None,
) -> Optional[TTSProvider]:
    """按 ``[tts]`` 配置构造选中的 TTS 引擎实例；未启用返回 ``None``。

    Args:
        tts_config: ``core.toml [tts]`` 段字典，至少包含 ``enabled`` 与
            ``provider``；缺失或非字典视为未启用。
        event_bus: 可选事件总线，传入后引擎内会发布 ``tts.utterance.*``
            生命周期事件；为 None 时引擎静默跳过事件发布（手动 / 直调场景）。
        audio_sink: 可选音频分接接收方（``AudioSink`` 协议形状），经构造链
            注入到引擎的播放器（口型分析等消费者）；None 表示不分接。
        llm_service: 可选 LLMManager，经装配适配行透传；仅 omni_tts 引擎消费
            （经其语音 profile 调用带语音输出模态的模型），其余适配行忽略。

    Returns:
        构造成功的 ``TTSProvider`` 协议实例（结构契约见 ``protocol.py``）；
        以下情况返回 ``None``：

        - ``enabled=False`` 或缺段；
        - 选中 provider 工厂调用异常（依赖缺失 / 配置非法等），fail-soft；
        - 工厂返回的对象**结构上不满足** ``TTSProvider`` 契约（缺方法 /
          形态异常），fail-fast（装配边界兜底，避免脏对象注入下游）。
    """
    logger = get_logger("TTSAssembly")

    # 输入防御：非 dict / None 一律视作未启用
    if not isinstance(tts_config, dict):
        return None

    if not bool(tts_config.get("enabled", False)):
        return None

    requested_provider = str(tts_config.get("provider", "edge_tts") or "edge_tts").strip()
    was_fallback = False
    if requested_provider not in _KNOWN_PROVIDERS:
        logger.error(f"build_tts_infrastructure: [tts].provider='{requested_provider}' 未知，回退到 'edge_tts'")
        provider = "edge_tts"
        was_fallback = True
    else:
        provider = requested_provider

    factory = _PROVIDER_FACTORIES.get(provider)
    if factory is None:  # pragma: no cover - 防御性兜底（_KNOWN_PROVIDERS 已保证）
        logger.error(f"build_tts_infrastructure: 内部错误，未找到 provider '{provider}' 的工厂函数")
        return None

    # 引擎子配置：取 [tts.<provider>] 子段（缺则空 dict，工厂函数走 Schema 默认）
    sub_config = tts_config.get(provider)
    if not isinstance(sub_config, dict):
        sub_config = {}

    try:
        engine = factory(
            config=dict(sub_config),
            event_bus=event_bus,
            audio_sink=audio_sink,
            llm_service=llm_service,
        )
    except Exception as exc:
        logger.error(
            f"build_tts_infrastructure: 构造 {provider} 引擎失败（{_PROVIDER_DESCRIPTIONS[provider]}）"
            f"：{type(exc).__name__}: {exc}",
            exc=True,
        )
        return None

    # 装配边界 fail-fast：工厂返回的对象必须结构上满足 TTSProvider 契约
    # （PROVIDER_NAME / name / setup / cleanup / handle_speech / get_stats /
    # ConfigSchema）。@runtime_checkable 仅校验成员存在性，签名留给静态
    # 类型系统；任何意外形态（None / dict / 缺方法实例）都视作装配失败。
    if not isinstance(engine, TTSProvider):
        logger.error(
            f"build_tts_infrastructure: {provider} 工厂返回的对象不满足 TTSProvider 契约"
            f"（type={type(engine).__name__}），装配失败"
        )
        return None

    logger.info(
        f"TTS 引擎已构造: provider={provider}（{_PROVIDER_DESCRIPTIONS[provider]}）"
        + ("（fallback from unknown）" if was_fallback else "")
    )
    return engine


__all__ = [
    "TTS_PROVIDER_NAMES",
    "build_tts_infrastructure",
    "_KNOWN_PROVIDERS",
    "_PROVIDER_FACTORIES",
]
