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


def repeated_read(tool: str, arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    """只读工具再次返回相同证据不算推进；新页面、变化后的现场和实际操作仍由原流程处理。"""
    reading = (
        tool in {"minecraft_observation", "maicraft_perceive"}
        or (tool == "maicraft_task" and arguments.get("action") in {"get", "list"})
        or (tool == "maicraft_plan" and bool(arguments.get("plan_id")) and "goal" not in arguments)
        or (tool in {"minecraft_todo", "minecraft_notebook"} and arguments.get("action") == "read")
    )
    return reading and (
        result.get("same_request_and_result") is True
        or result.get("_observation", {}).get("same_request_and_result") is True
    )


class MinecraftObservations:
    """只保存本玩家已经取得的证据，重复查询仍真实执行，旧观察不会冒充最新世界状态。"""

    INDEX_PAGE_SIZE = 20
    TEXT_PAGE_SIZE = 4000
    REQUEST_PREVIEW_CHARS = 320

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._sizes: dict[str, int] = {}
        self._scope = uuid.uuid4().hex[:8]
        self.repeated_results = 0
        self._read_fingerprints: set[str] = set()

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
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit")
        limit = (self.TEXT_PAGE_SIZE if ref else self.INDEX_PAGE_SIZE) if limit is None else limit
        maximum = 16000 if ref else 50
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= maximum:
            raise ValueError(f"offset 必须为非负整数，limit 必须为 1 到 {maximum} 的整数")
        if not ref:
            return self.mark_read(
                {"ok": True, **self._index_page(query, offset, limit), "scope": "current_task_history"}
            )
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
        if query:
            found = text.find(query, offset)
            if found < 0:
                return self.mark_read(
                    {"ok": True, "ref": ref, "path": path, "query": query, "found": False, "total_chars": len(text)}
                )
            offset = max(offset, found - min(200, limit // 4))
        end = min(len(text), offset + limit)
        # 历史原件保留完整，当前页同时限制转义后的体积；调用者沿 next_offset 续读，不重新灌入整份蓝图。
        while end > offset and len(json_text(text[offset:end])) > max(6000, limit):
            end = offset + (end - offset) // 2
        return self.mark_read(
            {
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
        )

    def mark_read(self, result: dict[str, Any]) -> dict[str, Any]:
        """记住真正读到的原文片段；换分页参数却读到同一段，或刷新时间戳，都不会制造新证据。"""
        evidence = {key: value for key, value in result.items() if key != "observed_at_ms"}
        if isinstance(evidence.get("observations"), list):
            # 索引的访问时间和近期排序不产生新游戏事实，原文引用与请求内容变化才有信息增量。
            evidence["observations"] = sorted(
                (
                    {key: value for key, value in row.items() if key != "observed_at_ms"}
                    for row in evidence["observations"]
                ),
                key=lambda row: row["ref"],
            )
        signature = hashlib.sha256(json_text(evidence).encode()).hexdigest()
        repeated = signature in self._read_fingerprints
        self._read_fingerprints.add(signature)
        result["same_request_and_result"] = repeated
        if repeated:
            result["hint"] = "这段历史原文已经读过；请用已有事实推进，只有新的具体缺口才需要继续补读。"
        return result

    def index(self, query: str = "") -> list[dict[str, Any]]:
        """整理上下文只保留近期目录，旧记录仍可搜索或分页找回。"""
        return self._index_page(query, 0, self.INDEX_PAGE_SIZE)["observations"]

    @property
    def count(self) -> int:
        """总量用于说明目录还有未显示的历史，不把未显示误当成不存在。"""
        return len(self._entries)

    def _index_page(self, query: str, offset: int, limit: int) -> dict[str, Any]:
        """索引只带定位信息和短请求摘要，完整参数继续由 source=request 读取。"""
        matches = []
        for ref, entry in reversed(self._entries.items()):
            if query and query not in entry["tool"] + json_text(entry["arguments"]):
                continue
            matches.append((ref, entry))
        if offset > len(matches):
            raise ValueError("索引 offset 超出匹配记录数量")
        entries = []
        for ref, entry in matches[offset : offset + limit]:
            arguments = entry["arguments"]
            identity = {key: value for key, value in arguments.items() if not isinstance(value, (dict, list))}
            goal = arguments.get("goal")
            if isinstance(goal, dict):
                identity["goal"] = {key: goal[key] for key in ("ability", "outcome", "target") if key in goal}
            request = json_text(identity)
            entries.append(
                {
                    "ref": ref,
                    "tool": entry["tool"],
                    "request_preview": request[: self.REQUEST_PREVIEW_CHARS],
                    "request_is_preview": True,
                    "observed_at_ms": entry["observed_at_ms"],
                    "original_chars": self._sizes[ref],
                }
            )
        end = min(len(matches), offset + limit)
        return {"observations": entries, "total": len(matches), "next_offset": end if end < len(matches) else None}
