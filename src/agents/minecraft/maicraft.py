"""MaiCraft v1 对外接口的宿主侧知识：五个工具的注册名、统一返回格式与目标运行状态。

v1 的每个工具都回一份 JSON：成功是 ``{ok: true, data, notes?, next?}``，失败是
``{ok: false, error: {code, message, fields?}}``。需要后台推进的目标由 ``execute`` 下达后
返回 ``task_id`` 与 ``state``；目标的处境（进行中 / 等回答 / 暂停 / 结束）与结束后的结果
都在 ``task(get)`` 里，变化经 ``events`` 推送。这些格式只在本模块解读一次，Agent 其余部分
按这里给出的含义使用，不各自猜字段。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.modules.tools.models import ToolExecutionResult

# MCP 工具经 ToolRegistry 注册后的全名：provider 名 maicraft 加上服务端原名。
PROVIDER = "maicraft"
OBSERVE = "maicraft_observe"
LOOKUP = "maicraft_lookup"
EXECUTE = "maicraft_execute"
TASK = "maicraft_task"
EVENTS = "maicraft_events"

# 事件流只给宿主自己读：模型看到的是宿主整理后的通知，不需要自己长轮询。
HOST_ONLY_TOOLS = frozenset({EVENTS})

# 目标运行的处境（task 与 execute 里的 state）
RUNNING = "running"
AWAITING_ANSWER = "awaiting_answer"
PAUSED = "paused"
FINISHED = "finished"

# 结果的达成情况 → 通用任务账本的终态。账本没有"部分完成"，记成失败并在摘要里写明部分完成。
_LEDGER_TERMINAL = {"done": "succeeded", "partial": "failed", "failed": "failed", "cancelled": "cancelled"}


@dataclass(slots=True)
class MaicraftReply:
    """一次 MaiCraft 工具调用的回复：成败、数据与错误，原样保留整份回复供模型阅读。"""

    ok: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


# MCP 客户端把服务端的业务错误（isError）包成异常，异常文字前缀是这个；后面跟着服务端原样的回复。
_BUSINESS_ERROR_PREFIX = "MCP 业务错误:"


def envelope_of(result: ToolExecutionResult) -> Dict[str, Any]:
    """找回 MaiCraft 原样的那份 JSON 回复：结构化结果、正文，或业务错误异常里带着的原文。"""
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for text in (result.content, result.error_message):
        body = (text or "").strip()
        if body.startswith(_BUSINESS_ERROR_PREFIX):
            body = body[len(_BUSINESS_ERROR_PREFIX) :].strip()
        if body.startswith("{"):
            try:
                value = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return {}


def reply_of(result: ToolExecutionResult) -> MaicraftReply:
    """把工具执行结果读成 MaiCraft 的统一回复；连接失败等拿不到回复的情况按失败如实记下。"""
    raw = envelope_of(result)
    if "ok" not in raw:
        # 没拿到 MaiCraft 自己的回复：连接断开、超时，或者服务端不是 v1 接口。
        message = result.error_message or result.content or "没有收到 MaiCraft 的回复"
        return MaicraftReply(
            ok=False, error_code="no_reply", error_message=message, raw={"ok": False, "error": message}
        )
    if raw.get("ok") is True:
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        return MaicraftReply(ok=True, data=data, raw=raw)
    error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
    return MaicraftReply(
        ok=False,
        error_code=str(error.get("code") or ""),
        error_message=str(error.get("message") or ""),
        raw=raw,
    )


@dataclass(slots=True)
class GoalRun:
    """一个目标运行此刻的样子（``task(get)`` / ``execute`` 的 data）。"""

    goal_id: int
    ability: str
    purpose: str
    state: str
    question: Optional[Dict[str, Any]]
    result: Optional[Dict[str, Any]]
    doing: str
    data: Dict[str, Any]

    @property
    def finished(self) -> bool:
        return self.state == FINISHED

    @property
    def result_status(self) -> str:
        """结束后的达成情况：done / partial / failed / cancelled；没结束时为空串。"""
        return str((self.result or {}).get("status") or "")

    @property
    def summary(self) -> str:
        """一句话：结束了就是结果的结论，在等回答就是问题，其余是此刻在做什么。"""
        if self.result:
            return str(self.result.get("summary") or "")
        if self.question:
            return str(self.question.get("text") or "")
        return self.doing


def goal_run_of(data: Dict[str, Any]) -> Optional[GoalRun]:
    """从 execute / task(get) 的 data 读出目标运行；不是目标运行的形状时为 None。"""
    goal_id = data.get("task_id")
    state = data.get("state")
    if not isinstance(goal_id, int) or isinstance(goal_id, bool) or not isinstance(state, str):
        return None
    question = data.get("question") if isinstance(data.get("question"), dict) else None
    result = data.get("result") if isinstance(data.get("result"), dict) else None
    return GoalRun(
        goal_id=goal_id,
        ability=str(data.get("ability") or ""),
        purpose=str(data.get("purpose") or ""),
        state=state,
        question=question,
        result=result,
        doing=str(data.get("doing") or ""),
        data=data,
    )


def ledger_status_of(run: GoalRun) -> str:
    """目标运行在通用任务账本里的状态：等回答是决策点，结束按结果定终态，其余算进行中。

    暂停不在账本词表里：账面仍是进行中，"身体停着"由 Agent 自己另外记下并处理。
    """
    if run.finished:
        return _LEDGER_TERMINAL.get(run.result_status, "failed")
    if run.state == AWAITING_ANSWER:
        return "waiting_for_decision"
    return "running"


def ledger_task_id(goal_id: int) -> str:
    """目标在通用任务账本里的编号：和主播委派的任务号分开，一眼看出是游戏里的目标。"""
    return f"maicraft-goal-{goal_id}"


def goal_id_of_ledger(task_id: str) -> Optional[int]:
    """账本编号还原成目标编号；不是本模块登记的目标时为 None。"""
    prefix = "maicraft-goal-"
    if not task_id.startswith(prefix):
        return None
    tail = task_id[len(prefix) :]
    return int(tail) if tail.isdigit() else None


@dataclass(slots=True)
class EventsPage:
    """一页任务事件：流编号、读到哪、这一页的事件、后面还有没有、游标是否还有效。"""

    stream_id: str
    cursor: int
    events: List[Dict[str, Any]]
    has_more: bool
    cursor_status: str


def events_page_of(data: Dict[str, Any]) -> Optional[EventsPage]:
    """读 events 的 data；形状不对时为 None。"""
    stream_id = data.get("stream_id")
    cursor = data.get("cursor")
    events = data.get("events")
    if not isinstance(stream_id, str) or not isinstance(cursor, int) or not isinstance(events, list):
        return None
    return EventsPage(
        stream_id=stream_id,
        cursor=cursor,
        events=[event for event in events if isinstance(event, dict)],
        has_more=bool(data.get("has_more")),
        cursor_status=str(data.get("cursor_status") or "valid"),
    )


__all__ = [
    "AWAITING_ANSWER",
    "EVENTS",
    "EXECUTE",
    "EventsPage",
    "FINISHED",
    "GoalRun",
    "HOST_ONLY_TOOLS",
    "LOOKUP",
    "MaicraftReply",
    "OBSERVE",
    "PAUSED",
    "PROVIDER",
    "RUNNING",
    "TASK",
    "envelope_of",
    "events_page_of",
    "goal_id_of_ledger",
    "goal_run_of",
    "ledger_status_of",
    "ledger_task_id",
    "reply_of",
]
