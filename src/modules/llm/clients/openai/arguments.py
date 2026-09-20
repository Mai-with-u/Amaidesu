"""完整工具参数的解析；严格调用不把自动修补的半份数据交给执行器。"""

import json
from typing import Any, NoReturn

from json_repair import repair_json

from src.modules.logging import get_logger

logger = get_logger("ToolArguments")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """重复键会悄悄覆盖对象定义，严格模式将这种信息丢失视为失败。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 对象包含重复键：{key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    """非有限数字不是合法 JSON，不能作为坐标等工具参数继续执行。"""
    raise ValueError(f"JSON 不接受非有限数字：{value}")


def decode_tool_call(call_id: str, name: str, raw: Any, *, strict: bool) -> dict[str, Any]:
    """保留原始失败输出供模型修正，普通调用沿用已有语法修复策略。"""
    error = None
    try:
        arguments = (
            json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if strict
            else json.loads(raw or "{}")
        )
        if strict and not isinstance(arguments, dict):
            raise ValueError("工具参数必须是完整 JSON 对象")
    except (ValueError, TypeError) as exc:
        logger.warning(f"工具 {name} 参数解析失败：{exc}", exc=True)
        if strict:
            arguments, error = {}, str(exc)
        else:
            arguments = repair_json(raw, return_objects=True)
    result = {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
    if error is not None:
        result.update(raw_arguments=raw if isinstance(raw, str) else str(raw), arguments_error=error)
    return result
