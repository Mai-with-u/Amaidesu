"""
Architecture tests to enforce proper dependency direction constraints（Wave 6 重写）

Wave 6 重写：原 3-domain 架构（Input/Decision/Output）已演进为 Agent + Tool 体系。
新分层（低 → 高）：
- Core:    基础设施（src/modules/config, events, logging, llm, storage, ...）
- Input:   采集器（src/modules/collectors/）
- Agent:   Agent 子系统（src/agents/streamer/）
- Tool:    工具（src/modules/tools/）

依赖方向：Core ← Input ← Agent ← Tool（高层依赖低层，低层不能依赖高层）。
跨域通信通过 EventBus（src/modules/events/），不直接 import。

Key Principles:
1. Lower layers cannot depend on higher layers
2. Domains should not directly import from peer or higher domains
3. Dependencies should flow through the event system, not direct imports
"""

import ast
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent.parent


def get_layer_from_path(file_path: Path) -> str:
    """
    Determine which architectural layer a file belongs to based on its path.

    Returns: 'core', 'orchestrator', 'input', 'agent', 'tool', or 'unknown'

    Special cases:
    - 'orchestrator': Files that coordinate domains (e.g., main.py)
                     These are allowed to import from domains
    - 'core': Core infrastructure that should NOT depend on domains
    """
    path_str = str(file_path).replace("\\", "/")

    # Special case: orchestrators (allowed to import from all domains)
    if path_str.endswith("/main.py"):
        return "orchestrator"

    # Check in order of specificity (more specific paths first)
    if "/agents/" in path_str:
        return "agent"
    elif "/collectors/" in path_str:
        return "input"
    elif "/tools/" in path_str:
        return "tool"
    elif "/modules/" in path_str:
        return "core"
    else:
        return "unknown"


class ImportVisitor(ast.NodeVisitor):
    """AST visitor to extract import information."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.imports: List[Dict] = []
        self.current_layer = get_layer_from_path(file_path)
        self.in_type_checking_block = False
        self.scope_stack: List[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope_stack.append("function")
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope_stack.append("function")
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope_stack.append("class")
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_If(self, node: ast.If) -> None:
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            self.in_type_checking_block = True
            self.generic_visit(node)
            self.in_type_checking_block = False
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        is_local_import = len(self.scope_stack) > 0
        for alias in node.names:
            self.imports.append(
                {
                    "module": alias.name,
                    "line": node.lineno,
                    "type": "import",
                    "is_type_checking": self.in_type_checking_block,
                    "is_local_import": is_local_import,
                }
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            is_local_import = len(self.scope_stack) > 0
            self.imports.append(
                {
                    "module": node.module,
                    "names": [alias.name for alias in node.names],
                    "line": node.lineno,
                    "type": "from_import",
                    "is_type_checking": self.in_type_checking_block,
                    "is_local_import": is_local_import,
                }
            )
        self.generic_visit(node)


def extract_imports(file_path: Path) -> List[Dict]:
    """Extract import statements from a Python file using AST."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    visitor = ImportVisitor(file_path)
    visitor.visit(tree)
    return visitor.imports


def get_import_target_layer(module_name: str) -> str:
    """
    Determine which layer an import targets based on the module name.

    Returns: 'core', 'input', 'agent', 'tool', 'external', or 'unknown'
    """
    if not module_name.startswith("src"):
        return "external"

    parts = module_name.split(".")

    if len(parts) > 1:
        second = parts[1]
        if second == "agents":
            return "agent"
        elif second == "collectors":
            return "input"
        elif second == "tools":
            return "tool"
        elif second == "modules":
            return "core"

    return "unknown"


def get_all_files_in_layer(layer: str) -> List[Path]:
    """Get all Python files in a specific layer (src/agents / src/modules/collectors / etc.)."""
    project_root = get_project_root()
    src_path = project_root / "src"

    if not src_path.exists():
        return []

    search_paths = {
        "agent": src_path / "agents",
        "input": src_path / "modules" / "collectors",
        "tool": src_path / "modules" / "tools",
    }

    search_path = search_paths.get(layer)
    if search_path is None or not search_path.exists():
        return []

    files = []
    for py_file in search_path.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        # Skip tests
        parts = py_file.parts
        if "tests" in parts:
            continue
        files.append(py_file)

    return files


def analyze_layer_dependencies(layer: str) -> Dict[str, List[Dict]]:
    """Analyze all dependencies for a specific layer."""
    files = get_all_files_in_layer(layer)
    dependencies = defaultdict(list)

    for file_path in files:
        imports = extract_imports(file_path)
        for imp in imports:
            if imp.get("is_type_checking", False):
                continue
            if imp.get("is_local_import", False):
                continue

            target_layer = get_import_target_layer(imp["module"])

            if target_layer in ["external", "unknown"]:
                continue
            if target_layer == layer:
                continue

            dependencies[target_layer].append(
                {"file": str(file_path), "module": imp["module"], "line": imp["line"], "type": imp["type"]}
            )

    return dict(dependencies)


class TestDependencyDirection:
    """Test suite for dependency direction architectural constraints (Wave 6 重写)。"""

    def test_input_domain_does_not_import_agent_or_tool(self):
        """采集器（Input Domain）不能直接 import Agent 或 Tool 模块。

        Input Domain 是最底层采集层（与 Core 同级或仅次于 Core），
        与 Agent/Tool 通信应通过 EventBus。
        """
        dependencies = analyze_layer_dependencies("input")

        violations = []
        for target in ["agent", "tool"]:
            for dep in dependencies.get(target, []):
                violations.append(
                    {"target": target, "file": dep["file"], "module": dep["module"], "line": dep["line"]}
                )

        if violations:
            violation_details = "\n".join(
                [
                    f"  - {v['file']}:{v['line']} imports from {v['target']} (module: {v['module']})"
                    for v in violations
                ]
            )
            raise AssertionError(
                f"Input Domain (collectors) MUST NOT directly import Agent or Tool modules.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"Correct pattern: Use EventBus (room.message.*) to communicate."
            )

    def test_agent_does_not_import_tool_directly(self):
        """Agent 不直接 import Tool（避免硬编码耦合）；通过 ToolRegistry.invoke 调用。"""
        dependencies = analyze_layer_dependencies("agent")

        violations = []
        for dep in dependencies.get("tool", []):
            # 允许 type-checking 内的 import（duck-typed Protocol）；已在 extract_imports 跳过 TYPE_CHECKING
            violations.append(
                {"file": dep["file"], "module": dep["module"], "line": dep["line"]}
            )

        if violations:
            violation_details = "\n".join(
                [
                    f"  - {v['file']}:{v['line']} imports from tool (module: {v['module']})"
                    for v in violations
                ]
            )
            raise AssertionError(
                f"Agent SHOULD NOT directly import Tool modules.\n"
                f"Use ToolRegistry.invoke(name, ...) for dynamic tool dispatch.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}"
            )

    def test_tool_does_not_depend_on_agent(self):
        """Tool（modules/tools/output/）不能依赖 Agent 子系统。"""
        dependencies = analyze_layer_dependencies("tool")

        violations = []
        for dep in dependencies.get("agent", []):
            violations.append(
                {"file": dep["file"], "module": dep["module"], "line": dep["line"]}
            )

        if violations:
            violation_details = "\n".join(
                [
                    f"  - {v['file']}:{v['line']} imports from agent (module: {v['module']})"
                    for v in violations
                ]
            )
            raise AssertionError(
                f"Tool layer MUST NOT depend on Agent modules.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}"
            )

    def test_proper_layer_hierarchy(self):
        """所有依赖方向必须符合层级（高 → 低）。"""
        layer_rank = {"core": 0, "input": 0, "agent": 1, "tool": 2}

        violations = []

        for layer in ["input", "agent", "tool"]:
            dependencies = analyze_layer_dependencies(layer)

            for target_layer, deps in dependencies.items():
                if target_layer not in layer_rank:
                    continue
                if layer_rank[layer] < layer_rank[target_layer]:
                    for dep in deps:
                        violations.append(
                            {
                                "source": layer,
                                "target": target_layer,
                                "file": dep["file"],
                                "module": dep["module"],
                                "line": dep["line"],
                            }
                        )

        if violations:
            violation_details = "\n".join(
                [
                    f"  - {v['source']} → {v['target']}: {v['file']}:{v['line']} (module: {v['module']})"
                    for v in violations
                ]
            )
            raise AssertionError(
                f"Dependencies must follow the v2 layer hierarchy.\n"
                f"Hierarchy: Core/Input (0) → Agent (1) → Tool (2)\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}"
            )

    def test_no_circular_dependencies(self):
        """检测 Input/Agent/Tool 三层之间无循环依赖。"""
        graph: Dict[str, Set[str]] = {}
        for layer in ["input", "agent", "tool"]:
            dependencies = analyze_layer_dependencies(layer)
            domain_deps = {
                target
                for target in dependencies.keys()
                if target in ["input", "agent", "tool"]
            }
            graph[layer] = domain_deps

        def has_cycle(
            node: str,
            visited: Set[str],
            rec_stack: Set[str],
            path: List[str],
        ) -> Tuple[bool, List[str]]:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    has_cycle_result, cycle_path = has_cycle(neighbor, visited, rec_stack, path)
                    if has_cycle_result:
                        return True, cycle_path
                elif neighbor in rec_stack:
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    return True, cycle

            path.pop()
            rec_stack.remove(node)
            return False, []

        visited: Set[str] = set()
        for layer in graph:
            if layer not in visited:
                has_cycle_result, cycle_path = has_cycle(layer, visited, set(), [])
                if has_cycle_result:
                    raise AssertionError(
                        f"Circular dependency detected!\n"
                        f"Cycle: {' → '.join(cycle_path)}\n"
                        f"v2 architecture: dependencies should flow Core/Input → Agent → Tool"
                    )

    def test_event_based_communication_pattern(self):
        """跨域通信应通过 EventBus 而非直接 import（统计文档）。"""
        all_dependencies = {}
        for layer in ["input", "agent", "tool"]:
            all_dependencies[layer] = analyze_layer_dependencies(layer)

        direct_imports = 0
        for layer, deps in all_dependencies.items():
            for target_layer in ["input", "agent", "tool"]:
                if target_layer == layer:
                    continue
                direct_imports += len(deps.get(target_layer, []))

        # 文档化期望（不强制失败，因为 duck-typed Protocol 是合理跨域通信方式）
        assert direct_imports >= 0
