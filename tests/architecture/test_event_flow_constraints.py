"""架构数据流约束：collectors emit → agent subscribe（room.message.*）→ tool invoke。

约束以 AST 扫描源码订阅点核验，事件名清单以 CoreEvents 为唯一事实源。核心边界：
Tool 域是被动调用面，不得订阅行为流事件与 Agent 域输出事件；
Agent 域订阅行为流之外只允许场次生命周期 / 游戏事件族 / 工具结果等基础设施事件。
"""

import ast
from pathlib import Path
from typing import Dict, List

import pytest

from src.modules.events.names import CoreEvents

SRC = Path(__file__).resolve().parents[2] / "src"
DOMAIN_ROOTS = {"agent": SRC / "agents", "tool": SRC / "modules" / "tools"}

CORE_EVENT_VALUES: Dict[str, str] = {
    name: getattr(CoreEvents, name)
    for name in dir(CoreEvents)
    if not name.startswith("_") and isinstance(getattr(CoreEvents, name), str)
}

# 世界侧输入事件：弹幕行为流 + 房间状态推送（采集器发布）
INPUT_EVENTS = {
    "room.message.danmaku",
    "room.message.gift",
    "room.message.super_chat",
    "room.message.enter",
    "room.message.partner_speech",
    "room.state.watched_count",
}
# Agent 域允许的非输入订阅：场次生命周期（主动发言闸门）、游戏事件族、工具结果
AGENT_ALLOWED_PREFIXES = ("live.started", "live.ended", "game.", "tool.result.")
# Agent 域输出事件（决策 / 流程 / 发言 / 任务 / 控制面事实）——Tool 域不得订阅
DECISION_EVENTS = {
    "rundown.changed",
    "planner.decision",
    "planner.verdict",
    "streamer.speech",
    "streamer.stage",
    "task.changed",
    "agent.prompted",
    "agent.replied",
}


def _event_name(arg: ast.AST) -> str | None:
    """订阅点第一个参数解析为事件名：字符串字面量或 ``CoreEvents.NAME`` 引用。"""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) and arg.value.id == "CoreEvents":
        return CORE_EVENT_VALUES.get(arg.attr, arg.attr)
    return None


def _subscriptions(domain: str) -> List[Dict]:
    found = []
    for path in DOMAIN_ROOTS[domain].rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            is_subscribe = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("subscribe", "on")
            )
            if is_subscribe and node.args and (event := _event_name(node.args[0])) is not None:
                found.append({"event": event, "where": f"{path.relative_to(SRC.parent)}:{node.lineno}"})
    return found


def _describe(violations: List[Dict]) -> str:
    return "\n".join(f"  - {v['where']} subscribes to {v['event']}" for v in violations)


def test_tool_domain_never_subscribes_to_input_or_decision_events() -> None:
    violations = [s for s in _subscriptions("tool") if s["event"] in INPUT_EVENTS | DECISION_EVENTS]
    assert not violations, "Tool 是被动调用面，不订阅行为流与 Agent 输出事件：\n" + _describe(violations)


def test_agent_domain_subscribes_only_to_input_and_infrastructure_events() -> None:
    violations = [
        s
        for s in _subscriptions("agent")
        if s["event"] not in INPUT_EVENTS and not s["event"].startswith(AGENT_ALLOWED_PREFIXES)
    ]
    assert not violations, (
        "Agent 域只允许订阅 room.message.* / room.state.* / live.* / game.* / tool.result.*：\n" + _describe(violations)
    )


@pytest.mark.parametrize("name", sorted(INPUT_EVENTS | DECISION_EVENTS))
def test_boundary_event_names_still_exist_in_core_events(name: str) -> None:
    """清单里手抄的事件名必须仍是 CoreEvents 的真实常量值，否则边界检查会静默失效。"""
    assert name in set(CORE_EVENT_VALUES.values())
