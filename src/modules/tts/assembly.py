"""TTS 基础设施装配入口

按核心 ``[tts]`` 配置段构造选中的 TTS 引擎 Provider 实例，供
``StreamerAgent`` 直接持有并调用其 ``handle_speech``。

设计要点
--------

- **基础模块自治装配**——TTS 是基础设施（非工具），装配逻辑放在
  ``src/modules/tts/`` 包内（``src/modules/tts/assembly.py``），
  工具层 ``bind_core_tools`` 不再介入
- **构造签名统一**——四个引擎 Provider 均为 ``Provider(config: dict,
  event_bus: EventBus | None = None)``；本函数按 ``[tts.<engine>]`` 子段
  提取子配置后用对应工厂函数构造
- **fail-soft 语义**——``enabled=False`` / 缺段 / 子配置构造异常时统一返回
  ``None``，调用方按"未装配"语义降级；未知 ``provider`` 名记 ERROR 并回退
  到 ``edge_tts``（装配兜底）
- **已知 provider**——``edge_tts`` / ``gptsovits`` / ``voicebox`` / ``omni_tts``；
  其余名字一律视作未知

装配流程示意::

    tts_section (dict)              # core.toml [tts] 段
        enabled: bool
        provider: str               # edge_tts / gptsovits / voicebox / omni_tts
        max_queue: int              # 队列容量（StreamerAgent 消费）
        render_timeout_ms: int      # 单 utterance 超时（StreamerAgent 消费）
        [tts.<engine>]: dict        # 引擎子配置（位于 tts_section 内部作为子键）

    build_tts_infrastructure(tts_section, event_bus=bus)
        ├─ enabled=False → None
        ├─ provider 未知 → ERROR + 回退到 edge_tts
        ├─ 取 sub = tts_section[provider]（缺则 {}）
        └─ factory(sub, event_bus) → Provider instance | None（异常兜底）
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from src.modules.logging import get_logger

from .edge_tts_tool import create_edge_tts_provider
from .gptsovits_tool import create_gptsovits_provider
from .omni_tts_tool import create_omni_tts_provider
from .voicebox_tool import create_voicebox_provider

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus


# 已知 provider 集合。装配时按 [tts].provider 单选；未知 provider 回退到
# edge_tts 并记 ERROR 日志（与历史 bootstrap 装配语义一致）。
_KNOWN_PROVIDERS = frozenset({"edge_tts", "gptsovits", "voicebox", "omni_tts"})

# provider 名 → 工厂函数。延迟 import 改在工厂函数内部完成；本映射只是
# 注册关系，本身不触发模块加载。
_PROVIDER_FACTORIES = {
    "edge_tts": create_edge_tts_provider,
    "gptsovits": create_gptsovits_provider,
    "voicebox": create_voicebox_provider,
    "omni_tts": create_omni_tts_provider,
}

# provider 名 → 中文描述（用于日志/文档）
_PROVIDER_DESCRIPTIONS = {
    "edge_tts": "Edge TTS 语音合成",
    "gptsovits": "GPT-SoVITS 本地 TTS",
    "voicebox": "Voicebox TTS",
    "omni_tts": "Omni TTS",
}


def build_tts_infrastructure(
    tts_config: Any,
    event_bus: Optional["EventBus"] = None,
) -> Optional[Any]:
    """按 ``[tts]`` 配置构造选中的 TTS 引擎实例；未启用返回 ``None``。

    Args:
        tts_config: ``core.toml [tts]`` 段字典，至少包含 ``enabled`` 与
            ``provider``；缺失或非字典视为未启用。
        event_bus: 可选事件总线，传入后引擎内会发布 ``tts.utterance.*``
            生命周期事件；为 None 时引擎静默跳过事件发布（手动 / 直调场景）。

    Returns:
        构造成功的 TTS 引擎 Provider 实例；以下情况返回 ``None``：

        - ``enabled=False`` 或缺段；
        - 选中 provider 工厂调用异常（依赖缺失 / 配置非法等），fail-soft。
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
        engine = factory(config=dict(sub_config), event_bus=event_bus)
    except Exception as exc:
        logger.error(
            f"build_tts_infrastructure: 构造 {provider} 引擎失败（{_PROVIDER_DESCRIPTIONS[provider]}）"
            f"：{type(exc).__name__}: {exc}",
            exc_info=True,
        )
        return None

    logger.info(
        f"TTS 引擎已构造: provider={provider}（{_PROVIDER_DESCRIPTIONS[provider]}）"
        + ("（fallback from unknown）" if was_fallback else "")
    )
    return engine


__all__ = [
    "build_tts_infrastructure",
    "_KNOWN_PROVIDERS",
    "_PROVIDER_FACTORIES",
]
