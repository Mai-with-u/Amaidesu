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


# 判断能否施工、能否重试和能否取料的证据始终直接呈现；大教材与枚举可按引用展开。
_DECISION_FIELDS = frozenset(
    {
        "error",
        "errors",
        "issues",
        "diagnostics",
        "design_diagnostics",
        "validation",
        "missing",
        "missing_materials",
        "material_deficits",
        "pending_decision",
        "decision",
        "decisions",
        "constraints",
        "authorization",
        "permissions",
        "forbidden_mods",
        "expected_output",
        "blockers",
        "ok",
        "success",
        "accepted",
        "status",
        "state",
        "complete",
        "buildable",
        "outcome_known",
        "physical_layout_compiled",
        "task_id",
        "artifact_ref",
        "blueprint_id",
        "snapshot_id",
        "plan_id",
        "ready_to_execute",
    }
)


class MinecraftObservations:
    """只保存本玩家已经取得的证据，重复查询仍真实执行，旧观察不会冒充最新世界状态。"""

    def __init__(self, inline_chars: int = 6000, archive_chars: int = 8_000_000) -> None:
        self.inline_chars = inline_chars
        self.archive_chars = archive_chars
        self._entries: dict[str, dict[str, Any]] = {}
        self._sizes: dict[str, int] = {}
        self._scope = uuid.uuid4().hex[:8]
        self._total_chars = 0
        self.repeated_results = 0

    def present(self, tool: str, arguments: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
        """先保存完整回执再投影；相同请求得到相同内容时复用正文引用并明确指出重复。"""
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
            self._total_chars += self._sizes[ref]
        entry["observed_at_ms"] = int(time.time() * 1000)
        self._entries[ref] = entry
        # 优先淘汰最久未用的原文；最新一次巨大回执仍可读，过期引用明确报错而不返回别的资料。
        while self._total_chars > self.archive_chars and len(self._entries) > 1:
            oldest = next(iter(self._entries))
            del self._entries[oldest]
            self._total_chars -= self._sizes.pop(oldest)
        # 明确选中的单份工艺/蓝图资料优先作为完整阅读单元；整本目录和超大文档仍按引用展开。
        resources = value.get("resources")
        selected_document = (
            tool == "maicraft_perceive"
            and arguments.get("view") == "knowledge"
            and bool(arguments.get("resource_uri"))
            and value.get("content_loaded") is True
            and isinstance(resources, list)
            and len(resources) == 1
        )
        budget = min(24_000, self.inline_chars * 4) if selected_document else self.inline_chars
        result = self._project(value, ref, "", budget)
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

    def _project(self, value: Any, ref: str, path: str, budget: int) -> Any:
        """保留对象外形与决策字段，大正文明确换成可定位的阅读入口。"""
        text = json_text(value)
        if len(text) <= max(budget, 256) or not isinstance(value, (dict, list, str)):
            return deepcopy(value)
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            remaining = budget
            for key, child in value.items():
                pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
                shown = deepcopy(child) if key in _DECISION_FIELDS else self._project(child, ref, pointer, remaining)
                result[key] = shown
                remaining -= len(json_text(shown)) + len(key)
            return result
        marker: dict[str, Any] = {"deferred": True, "ref": ref, "path": path, "type": type(value).__name__}
        if isinstance(value, str):
            marker.update(chars=len(value), preview=value[: min(1000, max(0, budget // 3))])
            headings = [line[:160] for line in value.splitlines() if line.startswith("#")]
            if headings:
                marker["headings"] = headings[:12]
        else:
            marker.update(
                items=len(value),
                preview=[self._project(child, ref, f"{path}/{index}", 256) for index, child in enumerate(value[:2])],
            )
        return marker

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
        offset, limit = int(arguments.get("offset", 0)), int(arguments.get("limit", 4000))
        if offset < 0 or not 1 <= limit <= 12000:
            raise ValueError("offset 必须非负，limit 必须在 1 到 12000 之间")
        if query:
            found = text.find(query, offset)
            if found < 0:
                return {"ok": True, "ref": ref, "path": path, "found": False, "total_chars": len(text)}
            offset = max(offset, found - min(200, limit // 4))
        end = min(len(text), offset + limit)
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
        """只列最近或匹配的证据入口，不把所有原件再次灌入上下文。"""
        entries = []
        for ref, entry in reversed(self._entries.items()):
            request = json_text(entry["arguments"])
            if query and query not in entry["tool"] + request:
                continue
            entries.append(
                {
                    "ref": ref,
                    "tool": entry["tool"],
                    "request_preview": request[:350],
                    "observed_at_ms": entry["observed_at_ms"],
                    "original_chars": self._sizes[ref],
                }
            )
            if len(entries) >= 20:
                break
        return entries
