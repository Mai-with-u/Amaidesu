"""游戏观察正文留在任务内存，模型先读有界摘要，必要时按路径取回原文。"""

from __future__ import annotations

import json
from typing import Any


def json_text(value: Any) -> str:
    """保持观察的序列化形式稳定，不把时间或随机值混入可复用正文。"""
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def brief(value: Any, depth: int = 0) -> Any:
    """优先保留决策、失败和施工事实；省略的集合明确标记，不能被当作空集合。"""
    if isinstance(value, str):
        return value if len(value) <= 360 else value[:360] + "…（原文按路径读取）"
    if isinstance(value, dict):
        if depth >= 5:
            return {"omitted_object_keys": list(value)[:16], "key_count": len(value)}
        important = {
            "task_id",
            "state",
            "status",
            "success",
            "ok",
            "error",
            "failure_code",
            "decision",
            "buildable",
            "construction_complete",
            "outcome_uncertain",
            "phase",
            "message",
            "summary",
            "next_attention",
            "task",
            "design_review",
            "layout_compiler",
        }
        keys = sorted(value, key=lambda key: key not in important)
        result = {key: brief(value[key], depth + 1) for key in keys[:16]}
        if len(keys) > 16:
            result["omitted_keys"] = keys[16:32]
            result["key_count"] = len(keys)
        return result
    if isinstance(value, list):
        if depth >= 5:
            return {"item_count": len(value), "items_omitted": True}
        items = [brief(item, depth + 1) for item in value[:4]]
        return items if len(value) <= 4 else {"items": items, "item_count": len(value), "has_more": True}
    return value


class MinecraftObservations:
    """完整观察属于当前游戏任务；缓存只覆盖任务内不变的能力目录和教材。"""

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}
        self._static: dict[str, str] = {}
        self._sequence = 0

    def clear(self) -> None:
        """新任务释放旧正文，但编号不复用，避免旧引用误指向另一份观察。"""
        self._results.clear()
        self._static.clear()

    def invalidate_static(self) -> None:
        """MCP 重新装配后重新读能力，已取得的原文引用仍然有效。"""
        self._static.clear()

    @staticmethod
    def _key(name: str, arguments: dict[str, Any]) -> str | None:
        if name.endswith("perceive") and arguments.get("view") in {"abilities", "knowledge"}:
            return name + json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        return None

    def cached(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """重复查教材时返回已有引用，不再访问 MCP；动态世界和任务状态始终重新观察。"""
        key = self._key(name, arguments)
        result_id = self._static.get(key) if key else None
        if result_id is None:
            return None
        return {"reused_observation": result_id, **self._envelope(result_id)}

    def present(self, name: str, arguments: dict[str, Any], value: Any) -> Any:
        """第一次进入上下文时就确定表示，后续不再原地改写已发送的工具消息。"""
        self._sequence += 1
        result_id = f"observation-{self._sequence}"
        # 用序列化副本隔离后台更新，保证编号引用的始终是当时那份观察。
        self._results[result_id] = json.loads(json_text(value))
        key = self._key(name, arguments)
        if (
            key
            and isinstance(value, dict)
            and value.get("ok") is not False
            and value.get("success") is not False
            and not value.get("error")
        ):
            self._static[key] = result_id
        return value if len(json_text(value)) <= 6000 else self._envelope(result_id)

    def _envelope(self, result_id: str) -> dict[str, Any]:
        value = self._results[result_id]
        summary = brief(value)
        # 极宽的回执仍可超过摘要预算；只缩摘要，原文和查询入口始终保留。
        if len(json_text(summary)) > 4500:
            summary = brief(value, 3)
        if len(json_text(summary)) > 4500:
            summary = brief(value, 5)
        return {
            "observation_id": result_id,
            "summary": summary,
            "full_chars": len(json_text(value)),
            "detail": {"tool": "minecraft_observation", "observation_id": result_id, "path": ""},
            "notice": "摘要未包含全部字段；只在当前决策缺少事实时按 JSON Pointer 路径读取原文。",
        }

    def read(self, result_id: str, path: str = "", offset: int = 0, limit: int = 4000) -> dict[str, Any]:
        """按 JSON Pointer 定位后分页读取；越界或失效引用报错，不伪造空结果。"""
        if result_id not in self._results:
            raise ValueError("观察引用不存在或所属任务已结束，请重新查询对应游戏事实")
        if offset < 0 or not 1 <= limit <= 8000 or path and not path.startswith("/"):
            raise ValueError("path 须为 JSON Pointer，offset 不小于零，limit 为 1..8000")
        value = self._results[result_id]
        for segment in path.split("/")[1:]:
            key = segment.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list) and key.isdigit():
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise ValueError("该路径不指向可读取的观察字段")
        text = json_text(value)
        if offset > len(text):
            raise ValueError("offset 超出该字段原文长度")
        end = min(len(text), offset + limit)
        return {
            "observation_id": result_id,
            "path": path,
            "content": text[offset:end],
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_chars": len(text),
        }
