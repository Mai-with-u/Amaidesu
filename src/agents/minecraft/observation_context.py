"""只引用本次模型请求中仍然可见的相同证据，原始回执继续留在工作历史。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from src.modules.logging import get_logger

logger = get_logger("MinecraftObservationContext")


def _canonical(value: Any) -> str:
    """对象字段顺序不改变游戏事实，数组顺序和物品组件则完整参与比较。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _child(path: str, key: str) -> str:
    """引用指向前一份可见回执的精确字段，不让同名箱子或不同槽位混淆。"""
    return path + "/" + key.replace("~", "~0").replace("/", "~1")


def _body(message: dict[str, Any]) -> tuple[dict[str, Any], str, str] | None:
    """只识别工具正文和宿主任务通知，用户指令、历史推理摘要保持原文。"""
    content = message.get("content")
    if not isinstance(content, str):
        return None
    prefix = ""
    if message.get("role") != "tool":
        if message.get("role") != "user" or not content.startswith("[系统] ") or "\n任务快照：" not in content:
            return None
        prefix, content = content.split("\n任务快照：", 1)
        prefix += "\n任务快照："
    stripped = content.lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(stripped)
    except json.JSONDecodeError as exc:
        # 普通文本和不完整 JSON 仍原样交付，解析不成不代表观察为空。
        logger.debug("观察正文不是完整 JSON，保留原文", exc=exc)
        return None
    if not isinstance(value, dict) or not isinstance(value.get("_observation"), dict):
        return None
    if not isinstance(value["_observation"].get("ref"), str):
        return None
    return value, prefix, stripped[end:]


def project_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐条建立可见证据索引；旧正文被整理移走后，新请求自动重新展示完整事实。"""
    visible: dict[str, dict[str, str]] = {}
    result: list[dict[str, Any]] = []

    def render(value: Any, *, root: bool = False) -> Any:
        if isinstance(value, (dict, list)):
            key = _canonical(value)
            if not root and key in visible:
                return {"$context": visible[key]}
        if isinstance(value, dict):
            return {key: deepcopy(item) if key == "_observation" else render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [render(item) for item in value]
        return value

    def remember(value: Any, ref: str, path: str = "") -> None:
        if not isinstance(value, (dict, list)):
            return
        key = _canonical(value)
        # 短状态直接显示更清楚；只替换明显长于定位信息的相同结构，从不按总字数删掉新事实。
        reference = {"ref": ref, "path": path}
        if len(key) > max(240, len(_canonical({"$context": reference})) * 2):
            visible.setdefault(key, reference)
        children = value.items() if isinstance(value, dict) else enumerate(value)
        for name, item in children:
            if name != "_observation":
                remember(item, ref, _child(path, str(name)))

    for message in messages:
        decoded = _body(message)
        if decoded is None:
            result.append(message)
            continue
        body, prefix, suffix = decoded
        presented = render(body, root=True)
        if presented != body:
            presented["_observation"]["context_references"] = (
                "$context 指向本次上下文中前面已经完整展示的相同字段，直接复用，无需工具补读。"
                "其余字段均为本次回执；数组顺序保持不变。"
            )
        result.append({**message, "content": prefix + _canonical(presented) + suffix})
        # 只在本条真正进入请求之后登记；引用总指向最早可见的等值正文，避免增长为多层引用链。
        remember(body, body["_observation"]["ref"])
    return result
