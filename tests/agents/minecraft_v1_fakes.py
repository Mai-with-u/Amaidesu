"""测试用的 MaiCraft v1 替身：五个工具的统一回复、目标运行与事件流，行为照真实接口写。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider

# 只改记忆、只读分析的能力当场完成，其余在后台推进。
_ASIDE_ABILITIES = frozenset({"maicraft:remember"})


def ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def error(code: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


class FakeMaicraft(BaseToolProvider):
    """MaiCraft v1 的替身：observe / lookup / execute / task / events。

    目标运行按真实接口走：要动手的目标返回 running，测试用 ``finish`` / ``ask`` / ``pause``
    推进它，同时往事件流里追加对应事件；事件流按游标分页，没有新事件时等一小会儿再空手返回。
    """

    category = "mcp"
    name = "maicraft"

    def __init__(self) -> None:
        self.goals: Dict[int, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self.stream_id = "stream-1"
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.self_view: Dict[str, Any] = {
            "position": {"x": 3.6, "y": 64.0, "z": -7.2, "dimension": "minecraft:overworld"},
            "facing": "南",
            "health": 18,
            "food": 15,
            "air": 300,
            "max_air": 300,
            "held": "minecraft:stone_axe",
            "armor": [],
            "effects": [],
            "inventory": [{"item": "minecraft:oak_log", "count": 6}],
            "control": "automation",
            "task": {"doing": "空闲，没有主任务"},
        }
        self.scene_view: Dict[str, Any] = {
            "time": "白天",
            "weather": "晴",
            "biome": "minecraft:forest",
            "summary": "前方：e1（minecraft:cow，6格）",
            "entities": [
                {"id": "e1", "type": "minecraft:cow", "direction": "前方", "distance": 6, "hostile": False},
                {"id": "e2", "type": "minecraft:creeper", "direction": "后方", "distance": 90, "hostile": True},
            ],
            "facilities": [{"id": "b1", "block": "minecraft:chest", "direction": "左侧", "distance": 4}],
        }
        self._next_id = 1
        self._new_event = asyncio.Event()

    # ----- 工具清单与分发 -----

    def list_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name=name, description=name, parameters_schema={"type": "object"}, kind="sync", provider="maicraft"
            )
            for name in ("observe", "lookup", "execute", "task", "events")
        ]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        name = invocation.tool_name.removeprefix("maicraft_")
        arguments = dict(invocation.arguments or {})
        self.calls.append((name, arguments))
        reply = await getattr(self, f"_{name}")(arguments)
        if reply["ok"]:
            return ToolExecutionResult(tool_name=invocation.tool_name, success=True, structured_content=reply)
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=False,
            structured_content=reply,
            error_message=reply["error"]["message"],
            failure_kind="business",
        )

    def calls_to(self, name: str) -> List[Dict[str, Any]]:
        return [arguments for called, arguments in self.calls if called == name]

    # ----- 五个工具 -----

    async def _observe(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        what = arguments.get("what", "scene")
        return ok(dict(self.self_view if what == "self" else self.scene_view))

    async def _lookup(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return ok({"abilities": [{"ability": "maicraft:gather"}, {"ability": "maicraft:remember"}]})

    async def _execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        goal = arguments.get("goal") or {}
        ability = str(goal.get("ability") or "")
        ability = ability if ":" in ability else f"maicraft:{ability}"
        goal_id = self._next_id
        self._next_id += 1
        run: Dict[str, Any] = {"task_id": goal_id, "ability": ability, "state": "running"}
        if goal.get("purpose"):
            run["purpose"] = goal["purpose"]
        if ability in _ASIDE_ABILITIES:
            run["state"] = "finished"
            run["result"] = {"status": "done", "summary": "记住了「家」", "changes": [], "remaining": []}
        self.goals[goal_id] = run
        return ok(dict(run))

    async def _task(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        operation = arguments.get("operation")
        if operation == "list":
            return ok({"tasks": [dict(run) for run in self.goals.values()]})
        run = self.goals.get(arguments.get("task_id"))
        if run is None:
            return error("unknown_id", f"没有编号为 {arguments.get('task_id')} 的任务")
        if operation == "answer":
            run.pop("question", None)
            run["state"] = "running"
            self._append("resumed", run["task_id"], "收到回答")
        elif operation == "resume":
            run["state"] = "running"
        elif operation == "cancel":
            run["state"] = "finished"
            run["result"] = {"status": "cancelled", "summary": "目标被取消", "changes": [], "remaining": []}
        return ok(dict(run))

    async def _events(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        expected = arguments.get("stream_id")
        after = int(arguments.get("after_cursor") or 0)
        status = "valid"
        if expected is not None and expected != self.stream_id:
            status, after = "stream_changed", 0
        wait_s = int(arguments.get("wait_ms") or 0) / 1000
        if wait_s and not any(event["cursor"] > after for event in self.events):
            self._new_event.clear()
            # 真实服务端最多等 wait_ms，有新事件立刻返回；测试里最多等 1.5 秒，比宿主的空转下限长。
            with_timeout = min(wait_s, 1.5)
            try:
                await asyncio.wait_for(self._new_event.wait(), timeout=with_timeout)
            except asyncio.TimeoutError:
                pass
        page = [event for event in self.events if event["cursor"] > after]
        cursor = page[-1]["cursor"] if page else (self.events[-1]["cursor"] if self.events else 0)
        cursor = max(cursor, after) if status == "valid" else cursor
        return ok(
            {"stream_id": self.stream_id, "cursor": cursor, "has_more": False, "cursor_status": status, "events": page}
        )

    # ----- 测试推进目标 -----

    def _append(self, kind: str, goal_id: int, message: str, status: Optional[str] = None) -> None:
        event: Dict[str, Any] = {"cursor": len(self.events) + 1, "kind": kind, "task_id": goal_id, "message": message}
        if status:
            event["status"] = status
        self.events.append(event)
        self._new_event.set()

    def finish(self, goal_id: int, status: str = "done", summary: str = "砍了 5 块原木") -> None:
        run = self.goals[goal_id]
        run["state"] = "finished"
        run.pop("question", None)
        run["result"] = {
            "status": status,
            "summary": summary,
            "changes": [{"kind": "item_gained", "what": "minecraft:oak_log", "count": 5}],
            "remaining": [],
        }
        self._append("finished", goal_id, summary, status)

    def ask(self, goal_id: int, text: str = "动哪个箱子？") -> None:
        run = self.goals[goal_id]
        run["state"] = "awaiting_answer"
        run["question"] = {
            "reason": "choose_one",
            "text": text,
            "options": [{"id": "b5", "meaning": "门口那个"}, {"id": "b6", "meaning": "屋里那个"}],
        }
        self._append("asked", goal_id, text)

    def pause(self, goal_id: int) -> None:
        self.goals[goal_id]["state"] = "paused"
        self._append("paused", goal_id, "暂停")

    def body_event(self, kind: str, message: str) -> None:
        self._append(kind, -1, message)

    def change_world(self) -> None:
        """换了世界：事件流换新编号，旧目标都不在了。"""
        self.stream_id = "stream-2"
        self.goals.clear()
        self.events.clear()
        # 每个世界的目标编号各自从 1 数起，可能撞上上一个世界的编号。
        self._next_id = 1
        self._new_event.set()
