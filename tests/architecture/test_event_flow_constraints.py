"""
Architecture tests to enforce proper data flow constraints（Wave 6 重写）

Wave 6 重写：原 3-domain Input→Decision→Output 架构已被 EventBus + Agent + Tool 体系取代。
新数据流：collectors emit → agent subscribe（room.message.*）→ tool invoke（reply tool）。

Wave 6 事件约定（src/modules/events/names.py）：
- 行为流（Input Domain emit）：
    - room.message.danmaku / gift / super_chat / enter
- Agent Domain subscribe：
    - room.message.*（消费弹幕驱动 Planner）
- Agent Domain emit：
    - planner.checkpoint（空转探测器）
    - agenda.update（节目单变更）
- Tool Domain 不订阅任何 room.message.*（工具是被动调用）
"""

import ast
from pathlib import Path
from typing import Dict, List


# v2 语义域事件（Wave 6 实际定义）
INPUT_EVENTS = {
    "room.message.danmaku",
    "room.message.gift",
    "room.message.super_chat",
    "room.message.enter",
}

DECISION_EVENTS = {
    "planner.checkpoint",
    "agenda.update",
}

OUTPUT_EVENTS: set[str] = set()  # v2 暂无 Output Domain 事件订阅


def _load_core_events_name_to_value() -> Dict[str, str]:
    """从 CoreEvents 类加载常量名→实际值的映射（用于解析 ``CoreEvents.X`` 引用）。"""
    try:
        from src.modules.events.names import CoreEvents

        return {
            name: getattr(CoreEvents, name)
            for name in dir(CoreEvents)
            if not name.startswith("_") and isinstance(getattr(CoreEvents, name), str)
        }
    except Exception:
        return {}


_CORE_EVENTS_MAP: Dict[str, str] = _load_core_events_name_to_value()


def get_project_root() -> Path:
    return Path(__file__).parent.parent.parent


def _resolve_event_name(event_arg: ast.AST) -> str | None:
    """从 AST 节点解析事件名（支持字符串字面量和 ``CoreEvents.NAME`` 引用）。"""
    if isinstance(event_arg, ast.Constant) and isinstance(event_arg.value, str):
        return event_arg.value
    if isinstance(event_arg, ast.Attribute) and isinstance(event_arg.value, ast.Name):
        if event_arg.value.id == "CoreEvents":
            # 查表映射（如果找不到就返回原 attribute 名作为兜底）
            return _CORE_EVENTS_MAP.get(event_arg.attr, event_arg.attr)
    return None


def extract_event_subscriptions(file_path: Path) -> List[Dict]:
    """Extract event subscriptions from a Python file using AST."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
    except SyntaxError:
        return []

    subscriptions = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("subscribe", "on"):
                if node.args and len(node.args) > 0:
                    event_name = _resolve_event_name(node.args[0])
                    if event_name is not None:
                        class_name = find_containing_class(tree, node)
                        subscriptions.append(
                            {
                                "class_name": class_name,
                                "event_name": event_name,
                                "line_number": node.lineno,
                                "file_path": str(file_path),
                            }
                        )

    return subscriptions


def find_containing_class(tree: ast.AST, node: ast.AST) -> str:
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef):
            for child in ast.walk(parent):
                if child is node:
                    return parent.name
    return "<module>"


def get_domain_from_path(file_path: Path) -> str:
    """Determine which domain a file belongs to (v2: collector / agent / tool / unknown)."""
    path_str = str(file_path).replace("\\", "/")

    if "/collectors/" in path_str:
        return "input"
    elif "/agents/" in path_str:
        return "agent"
    elif "/tools/" in path_str:
        return "tool"
    else:
        return "unknown"


def get_all_subscriptions_in_domain(domain: str) -> List[Dict]:
    """Get all event subscriptions in a specific v2 domain."""
    project_root = get_project_root()
    src_path = project_root / "src"

    search_paths = {
        "input": src_path / "modules" / "collectors",
        "agent": src_path / "agents",
        "tool": src_path / "modules" / "tools",
    }

    search_path = search_paths.get(domain)
    if search_path is None or not search_path.exists():
        return []

    subscriptions = []
    for py_file in search_path.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        # skip tests
        if "tests" in py_file.parts:
            continue
        file_subscriptions = extract_event_subscriptions(py_file)
        subscriptions.extend(file_subscriptions)

    return subscriptions


class TestEventFlowConstraints:
    """v2 架构的事件流约束。"""

    def test_tool_does_not_subscribe_to_input_events(self):
        """Tool（modules/tools）不能订阅 Input 事件（room.message.*）。

        Tool 是被动调用（reply tool / should_speak_proactively tool），不订阅事件。
        """
        tool_subscriptions = get_all_subscriptions_in_domain("tool")
        violations = [
            sub
            for sub in tool_subscriptions
            if sub["event_name"] in INPUT_EVENTS
        ]
        if violations:
            violation_details = "\n".join(
                [f"  - {v['class_name']} in {v['file_path']}:{v['line_number']} subscribes to {v['event_name']}" for v in violations]
            )
            raise AssertionError(
                f"Tool layer MUST NOT subscribe to Input events.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}"
            )

    def test_agent_does_not_subscribe_to_input_for_decision(self):
        """Agent 不订阅 Input 事件做『决策』（§1.44 决策流已废）。

        v2 架构下 Planner 通过 EventBus 订阅 room.message.*，但 agent 域内『决定订阅
        Decision 事件』的代码路径不再存在（Planner 是 Agent 内部组件）。
        """
        agent_subscriptions = get_all_subscriptions_in_domain("agent")
        # 允许 Agent 订阅 room.message.*（Planner 必须订阅弹幕驱动决策）
        # 仅检测是否订阅了其他 Agent 的输出事件
        violations = [
            sub for sub in agent_subscriptions
            if sub["event_name"] not in INPUT_EVENTS
            and not sub["event_name"].startswith("tool.result.")
        ]
        if violations:
            violation_details = "\n".join(
                [f"  - {v['class_name']} in {v['file_path']}:{v['line_number']} subscribes to {v['event_name']}" for v in violations]
            )
            raise AssertionError(
                f"Agent layer subscribes to unexpected events (allowed: room.message.* or tool.result.*).\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}"
            )

    def test_event_subscriptions_follow_domain_boundaries(self):
        """检查所有订阅是否跨域违规（Tool 不订阅 Input 事件等）。"""
        all_violations = []

        for domain in ["input", "agent", "tool"]:
            subscriptions = get_all_subscriptions_in_domain(domain)
            for sub in subscriptions:
                event_name = sub["event_name"]
                violation = None

                if domain == "tool":
                    if event_name in INPUT_EVENTS or event_name in DECISION_EVENTS:
                        violation = f"Tool Domain subscribing to {event_name}"

                if violation:
                    all_violations.append(
                        {
                            "domain": domain,
                            "class": sub["class_name"],
                            "event": event_name,
                            "file": sub["file_path"],
                            "line": sub["line_number"],
                            "violation": violation,
                        }
                    )

        if all_violations:
            violation_details = "\n".join(
                [f"  - [{v['domain']}] {v['class']} in {v['file']}:{v['line']}: {v['violation']}" for v in all_violations]
            )
            raise AssertionError(
                f"Found {len(all_violations)} domain boundary violation(s):\n"
                f"{violation_details}"
            )

    def test_proper_event_chain_documented(self):
        """文档化数据流：Input emit room.message.* → Agent subscribe → Tool invoke。

        此测试不强制失败（仅文档化），因为 v2 架构下 Tool 是被动调用，无 Output
        Domain 订阅模式（与 v1 三阶段 Input→Decision→Output 不同）。
        """
        agent_subs = get_all_subscriptions_in_domain("agent")
        agent_subscribes_input = any(
            sub["event_name"] in INPUT_EVENTS for sub in agent_subs
        )
        # 此测试仅作文档化：Agent 应订阅 Input 事件
        assert agent_subscribes_input or len(agent_subs) == 0
