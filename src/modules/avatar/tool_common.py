"""形象平台 Provider 契约工具的公共实现。

三个形象平台 Provider（VTS / Warudo / 表情差分图）共享三个契约工具
（``set_expression`` / ``list_preset_actions`` / ``trigger_preset_action``）
的参数 schema、ToolSpec 声明样板、invoke 分发骨架与"未知预设动作"失败
结果构造；平台差异（描述文案、前缀、情绪映射表与落平台渲染）全部留在
各 Provider——本模块只收跨平台逐字同构的部分，不做平台语义。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Dict, List, Optional, Tuple

from src.modules.logging import ModuleLogger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
    tool_result_fail,
)
from src.modules.types.emotion_vocab import Emotion

# 契约工具名（provider 内唯一，不带前缀；对外全名 = <provider>_<此名>）
SET_EXPRESSION_TOOL = "set_expression"
LIST_PRESET_ACTIONS_TOOL = "list_preset_actions"
TRIGGER_PRESET_ACTION_TOOL = "trigger_preset_action"


def set_expression_schema() -> Dict[str, Any]:
    """set_expression 参数 schema 的单一定义源（17 Emotion 枚举 + 强度）。

    intensity 描述含"1.0 为该情绪的完整幅度"，即强度语义的契约表述；
    各 Provider 本体按此语义做线性缩放。
    """
    return {
        "type": "object",
        "properties": {
            "emotion": {
                "type": "string",
                "enum": [e.value for e in Emotion],
                "description": "情绪（17 枚举值之一，小写）",
            },
            "intensity": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.5,
                "description": "情绪强度（0.0–1.0；1.0 为该情绪的完整幅度）",
            },
        },
        "required": ["emotion"],
    }


def trigger_action_schema(list_tool_name: str) -> Dict[str, Any]:
    """trigger_preset_action 参数 schema；描述引用自家 list 工具全名。"""
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": f"预设动作名（取自 {list_tool_name} 返回的目录）",
            },
        },
        "required": ["action"],
    }


def contract_tool_specs(
    provider_name: str,
    *,
    set_expression: str,
    list_preset_actions: str,
    trigger_preset_action: str,
) -> list[ToolSpec]:
    """生成三个契约工具的 ToolSpec（kind="sync"、provider=provider_name、name 不带前缀）。

    三个 description 是平台文案，关键字参数传入——各 Provider 的工具描述
    差异全部在此承载，声明样板本身不重复。
    """
    return [
        ToolSpec(
            name=SET_EXPRESSION_TOOL,
            description=set_expression,
            kind="sync",
            provider=provider_name,
            parameters_schema=set_expression_schema(),
        ),
        ToolSpec(
            name=LIST_PRESET_ACTIONS_TOOL,
            description=list_preset_actions,
            kind="sync",
            provider=provider_name,
        ),
        ToolSpec(
            name=TRIGGER_PRESET_ACTION_TOOL,
            description=trigger_preset_action,
            kind="sync",
            provider=provider_name,
            parameters_schema=trigger_action_schema(f"{provider_name}_{LIST_PRESET_ACTIONS_TOOL}"),
        ),
    ]


async def dispatch_tool(
    invocation: ToolInvocation,
    provider_name: str,
    handlers: Mapping[str, Callable[[], Awaitable[ToolExecutionResult]]],
    logger: ModuleLogger,
) -> ToolExecutionResult:
    """invoke 分发骨架：按全名查 handlers 并执行，统一未知名与异常兜底。

    handlers 的键是工具全名（``<provider>_<name>``），值是零参异步闭包——
    闭包捕获本次 invocation 的参数解析结果，每次 invoke 现构造，不做实例
    缓存（缓存会串参数）。未知名与执行异常的失败结果形状各 Provider 一致。
    """
    handler = handlers.get(invocation.tool_name)
    if handler is None:
        return tool_result_fail(
            invocation.tool_name,
            f"工具 '{invocation.tool_name}' 不属于 Provider '{provider_name}'",
        )
    try:
        return await handler()
    except Exception as exc:  # noqa: BLE001 — Provider 边界兜底
        logger.exception(f"{provider_name} 工具 {invocation.tool_name} 调用异常: {exc}")
        return tool_result_fail(invocation.tool_name, f"{type(exc).__name__}: {exc}")


def unknown_action_fail(tool_name: str, action: str, catalog_names: List[str]) -> ToolExecutionResult:
    """未知预设动作的失败结果；目录随结果返回（失败即发现，调用方可自纠）。"""
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=f"未知预设动作 '{action}'",
        structured_content={"available_actions": catalog_names},
        content=str(catalog_names),
    )


def clamp_intensity(intensity: float) -> float:
    """情绪强度钳制到 [0.0, 1.0]（各 Provider set_expression 本体共用）。"""
    return min(1.0, max(0.0, float(intensity)))


# 17 Emotion 枚举值集合（词表编译期已知；set_expression 的合法性边界）
_EMOTION_VALUES = frozenset(e.value for e in Emotion)


def scaled_emotion_weights(
    emotion: str,
    intensity: float,
    weights_by_emotion: Mapping[str, Mapping[str, float]],
) -> Optional[Tuple[float, Dict[str, float]]]:
    """按强度线性缩放情绪权重表，返回 (收敛后强度, 缩放后权重)；词表外情绪返回 None。

    各形象平台 set_expression 共用的缩放语义：0.0 收回全部表情参数、1.0 为
    权重表完整幅度、线性内插，权重统一保留 4 位小数。权重表本身由各平台
    自持（参数名与每档幅度是平台各自的事；档案未列出的合法情绪按空表 =
    中性处理），本函数收拢"词表校验 + 强度收敛 + 线性缩放"的公共语义，
    保证同一情绪在不同平台上幅度一致。
    """
    if emotion not in _EMOTION_VALUES:
        return None
    factor = clamp_intensity(intensity)
    weights = weights_by_emotion.get(emotion, {})
    return factor, {key: round(weight * factor, 4) for key, weight in weights.items()}


__all__ = [
    "SET_EXPRESSION_TOOL",
    "LIST_PRESET_ACTIONS_TOOL",
    "TRIGGER_PRESET_ACTION_TOOL",
    "set_expression_schema",
    "trigger_action_schema",
    "contract_tool_specs",
    "dispatch_tool",
    "unknown_action_fail",
    "clamp_intensity",
    "scaled_emotion_weights",
]
