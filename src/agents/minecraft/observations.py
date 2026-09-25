"""当前 Minecraft 任务的原始观察与按需阅读视图，不代替 Mod 查询世界。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from copy import deepcopy
from typing import Any


def json_text(value: Any) -> str:
    """稳定序列化工作证据，避免空格变化制造重复正文。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class MinecraftObservations:
    """只保存本玩家已经取得的证据，重复查询仍真实执行，旧观察不会冒充最新世界状态。"""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._sizes: dict[str, int] = {}
        self._scope = uuid.uuid4().hex[:8]
        self.repeated_results = 0

    def present(self, tool: str, arguments: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
        """完整保存并返回回执；相同请求与结果复用引用并标明重复。"""
        body = json_text(value)
        request = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256((tool + request + body).encode()).hexdigest()[:24]
        ref = f"obs_{self._scope}_{digest}"
        repeated = ref in self._entries
        if repeated:
            self.repeated_results += 1
            entry = self._entries.pop(ref)
        else:
            entry = {"value": json.loads(body), "tool": tool, "arguments": deepcopy(arguments)}
            self._sizes[ref] = len(body) + len(request)
        entry["observed_at_ms"] = int(time.time() * 1000)
        self._entries[ref] = entry
        # 当前任务的回执与参数完整保留；正文直接进入模型，集中摘要后仍可按引用查询原文。
        result = deepcopy(entry["value"])
        result["_observation"] = {
            "ref": ref,
            "observed_at_ms": entry["observed_at_ms"],
            "source_tool": tool,
            "original_chars": len(body),
            "same_request_and_result": repeated,
            "scope": "historical_observation_not_current_world",
        }
        if repeated:
            result["_observation"]["hint"] = "本次真实查询没有新增内容；复用已有证据，或说明还缺少哪个具体字段。"
        return result

    def read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """通过 JSON Pointer 定位并分页阅读历史原文，搜索结果也保留可继续读取的偏移。"""
        ref = str(arguments.get("ref") or "")
        query = str(arguments.get("query") or "")
        if not ref:
            return {"ok": True, "observations": self.index(query), "scope": "current_task_history"}
        entry = self._entries.get(ref)
        if entry is None:
            raise ValueError("观察引用已过期或不属于当前任务，请查询索引或重新取得实际资料")
        source = arguments.get("source", "result")
        if source not in {"result", "request"}:
            raise ValueError("source 必须是 result 或 request")
        # 请求原件也能补读，整理历史后仍可定位当时提交的目标、蓝图与约束。
        value = entry["arguments"] if source == "request" else entry["value"]
        path = str(arguments.get("path") or "")
        if path and not path.startswith("/"):
            raise ValueError("path 必须为空或以 / 开头的 JSON Pointer")
        for component in path.split("/")[1:]:
            key = component.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and key.isdecimal() and int(key) < len(value):
                value = value[int(key)]
            else:
                raise ValueError(f"原始观察中不存在路径 {path}")
        text = value if isinstance(value, str) else json_text(value)
        offset = int(arguments.get("offset", 0))
        limit = int(arguments["limit"]) if arguments.get("limit") is not None else None
        if offset < 0 or (limit is not None and limit < 1):
            raise ValueError("offset 必须非负，显式指定的 limit 必须为正数")
        if query:
            found = text.find(query, offset)
            if found < 0:
                return {"ok": True, "ref": ref, "path": path, "found": False, "total_chars": len(text)}
            offset = max(offset, found - (200 if limit is None else min(200, limit // 4)))
        end = len(text) if limit is None else min(len(text), offset + limit)
        return {
            "ok": True,
            "ref": ref,
            "path": path,
            "source_tool": entry["tool"],
            "source": source,
            "observed_at_ms": entry["observed_at_ms"],
            "text": text[offset:end],
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_chars": len(text),
            "complete": offset == 0 and end == len(text),
            "historical": True,
        }

    def index(self, query: str = "") -> list[dict[str, Any]]:
        """列出本任务全部匹配证据及完整请求，保留原文引用供整理历史后查阅。"""
        entries = []
        for ref, entry in reversed(self._entries.items()):
            request = json_text(entry["arguments"])
            if query and query not in entry["tool"] + request:
                continue
            entries.append(
                {
                    "ref": ref,
                    "tool": entry["tool"],
                    "request_preview": request,
                    "observed_at_ms": entry["observed_at_ms"],
                    "original_chars": self._sizes[ref],
                }
            )
        return entries
