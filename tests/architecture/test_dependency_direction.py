"""
Architecture tests to enforce proper dependency direction constraints.

These tests verify that the 3-domain architecture maintains proper dependency
direction following the layer hierarchy: Core → Input → Decision → Output

Core Layer: Lowest level, infrastructure
Input Domain: Can depend on Core only
Decision Domain: Can depend on Core and Input (via events, not direct imports)
Output Domain: Can depend on Core and Decision (via events, not direct imports)

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

    Returns: 'core', 'orchestrator', 'input', 'decision', 'output', 'services', or 'unknown'

    Special cases:
    - 'orchestrator': Files that coordinate domains (amaidesu_core.py, flow_coordinator.py)
                     These are allowed to import from domains
    - 'core': Core infrastructure that should NOT depend on domains
    """
    path_str = str(file_path)

    # Special case: orchestrators (allowed to import from all domains)
    if "amaidesu_core.py" in path_str or "flow_coordinator.py" in path_str:
        return "orchestrator"

    # Check in order of specificity (more specific paths first)
    if "/domains/input/" in path_str or "\\domains\\input\\" in path_str:
        return "input"
    elif "/domains/decision/" in path_str or "\\domains\\decision\\" in path_str:
        return "decision"
    elif "/domains/output/" in path_str or "\\domains\\output\\" in path_str:
        return "output"
    elif "/core/" in path_str or "\\core\\" in path_str:
        return "core"
    elif "/services/" in path_str or "\\services\\" in path_str:
        return "services"
    else:
        return "unknown"


class ImportVisitor(ast.NodeVisitor):
    """AST visitor to extract import information."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.imports: List[Dict] = []
        self.current_layer = get_layer_from_path(file_path)
        self.in_type_checking_block = False
        self.scope_stack = []  # Track if we're inside a function/class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Track function scope."""
        self.scope_stack.append("function")
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Track async function scope."""
        self.scope_stack.append("function")
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Track class scope."""
        self.scope_stack.append("class")
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_If(self, node: ast.If) -> None:
        """Detect if we're inside a TYPE_CHECKING block."""
        # Check if this is 'if TYPE_CHECKING:'
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            self.in_type_checking_block = True
            self.generic_visit(node)
            self.in_type_checking_block = False
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Handle 'import x' statements."""
        is_local_import = len(self.scope_stack) > 0
        for alias in node.names:
            self.imports.append({
                "module": alias.name,
                "line": node.lineno,
                "type": "import",
                "is_type_checking": self.in_type_checking_block,
                "is_local_import": is_local_import  # Inside function/class
            })
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Handle 'from x import y' statements."""
        if node.module:
            is_local_import = len(self.scope_stack) > 0
            self.imports.append({
                "module": node.module,
                "names": [alias.name for alias in node.names],
                "line": node.lineno,
                "type": "from_import",
                "is_type_checking": self.in_type_checking_block,
                "is_local_import": is_local_import  # Inside function/class
            })
        self.generic_visit(node)


def extract_imports(file_path: Path) -> List[Dict]:
    """
    Extract import statements from a Python file using AST.

    Returns:
        List of dicts with keys: module, line, type, names (optional)
    """
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

    Returns: 'core', 'input', 'decision', 'output', 'external', or 'unknown'
    """
    # External/stdlib imports (not starting with src)
    if not module_name.startswith("src"):
        return "external"

    # Split the module path
    parts = module_name.split(".")

    # Check the first significant part after 'src'
    if len(parts) > 1:
        if parts[1] == "core":
            return "core"
        elif parts[1] == "services":
            return "services"
        elif parts[1] == "domains":
            if len(parts) > 2:
                if parts[2] == "input":
                    return "input"
                elif parts[2] == "decision":
                    return "decision"
                elif parts[2] == "output":
                    return "output"

    return "unknown"


def get_all_files_in_layer(layer: str) -> List[Path]:
    """Get all Python files in a specific layer."""
    project_root = get_project_root()
    src_path = project_root / "src"

    if not src_path.exists():
        return []

    files = []
    if layer == "core":
        search_path = src_path / "core"
    elif layer == "services":
        search_path = src_path / "services"
    elif layer in ["input", "decision", "output"]:
        search_path = src_path / "domains" / layer
    else:
        return []

    if search_path.exists():
        for py_file in search_path.rglob("*.py"):
            # Skip __pycache__ and test files
            if "__pycache__" in str(py_file):
                continue
            if py_file.name.startswith("test_") or py_file.name == "_test.py":
                continue

            # Exclude orchestrator files from core layer analysis
            if layer == "core" and get_layer_from_path(py_file) == "orchestrator":
                continue

            files.append(py_file)

    return files


def analyze_layer_dependencies(layer: str) -> Dict[str, List[Dict]]:
    """
    Analyze all dependencies for a specific layer.

    Returns:
        Dict mapping target layers to list of import details

    Note: Skips TYPE_CHECKING imports (for type hints) and local imports
    (inside functions/methods) as these are acceptable patterns to avoid
    circular dependencies.
    """
    files = get_all_files_in_layer(layer)
    dependencies = defaultdict(list)

    for file_path in files:
        imports = extract_imports(file_path)
        for imp in imports:
            # Skip TYPE_CHECKING imports (they're only for type hints, not runtime dependencies)
            if imp.get("is_type_checking", False):
                continue

            # Skip local imports inside functions/methods (lazy loading pattern)
            # These are acceptable to avoid circular dependencies
            if imp.get("is_local_import", False):
                continue

            target_layer = get_import_target_layer(imp["module"])

            # Skip external and unknown imports
            if target_layer in ["external", "unknown"]:
                continue

            # Skip imports within the same layer
            if target_layer == layer:
                continue

            dependencies[target_layer].append({
                "file": str(file_path),
                "module": imp["module"],
                "line": imp["line"],
                "type": imp["type"]
            })

    return dict(dependencies)


class TestDependencyDirection:
    """Test suite for dependency direction architectural constraints."""

    def test_input_domain_does_not_import_decision_or_output(self):
        """
        Test that Input Domain does NOT directly import Decision or Output Domain modules.

        Input Domain is the lowest domain layer and should only depend on Core/Services.
        Communication with Decision/Output should happen through events only.
        """
        dependencies = analyze_layer_dependencies("input")

        violations = []

        # Check for Decision imports
        for dep in dependencies.get("decision", []):
            violations.append({
                "target": "decision",
                "file": dep["file"],
                "module": dep["module"],
                "line": dep["line"]
            })

        # Check for Output imports
        for dep in dependencies.get("output", []):
            violations.append({
                "target": "output",
                "file": dep["file"],
                "module": dep["module"],
                "line": dep["line"]
            })

        if violations:
            violation_details = "\n".join([
                f"  - {v['file']}:{v['line']} "
                f"imports from {v['target']} (module: {v['module']})"
                for v in violations
            ])
            raise AssertionError(
                f"Input Domain MUST NOT directly import Decision or Output Domain modules.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"Correct pattern: Use EventBus to communicate. "
                f"Input publishes events, Decision/Output subscribe to them."
            )

    def test_decision_domain_does_not_import_output(self):
        """
        Test that Decision Domain does NOT directly import Output Domain modules.

        Decision Domain should only depend on Core/Services and communicate with
        Output through events only.
        """
        dependencies = analyze_layer_dependencies("decision")

        violations = []

        # Check for Output imports
        for dep in dependencies.get("output", []):
            violations.append({
                "file": dep["file"],
                "module": dep["module"],
                "line": dep["line"]
            })

        if violations:
            violation_details = "\n".join([
                f"  - {v['file']}:{v['line']} "
                f"imports from Output (module: {v['module']})"
                for v in violations
            ])
            raise AssertionError(
                f"Decision Domain MUST NOT directly import Output Domain modules.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"Correct pattern: Use EventBus to communicate. "
                f"Decision publishes INTENT_GENERATED events, Output subscribes to them."
            )

    def test_core_does_not_depend_on_any_domain(self):
        """
        Test that Core layer does NOT depend on any Domain modules.

        Core is the lowest layer and should be independent of business logic domains.
        Domains can depend on Core, but not vice versa.

        Note: Exception for src/core/base/base.py which is a re-export module.
        This is a known technical debt that should be refactored.
        """
        dependencies = analyze_layer_dependencies("core")

        violations = []

        # Check for any domain imports
        for target_layer in ["input", "decision", "output"]:
            for dep in dependencies.get(target_layer, []):
                # Skip the known re-export module (technical debt)
                # Normalize path for Windows compatibility
                import os
                normalized_path = os.path.normpath(dep["file"])
                if normalized_path.endswith("src/core/base/base.py") or \
                   normalized_path.endswith("src\\core\\base\\base.py"):
                    continue

                violations.append({
                    "target": target_layer,
                    "file": dep["file"],
                    "module": dep["module"],
                    "line": dep["line"]
                })

        if violations:
            violation_details = "\n".join([
                f"  - {v['file']}:{v['line']} "
                f"imports from {v['target']} domain (module: {v['module']})"
                for v in violations
            ])
            raise AssertionError(
                f"Core layer MUST NOT depend on any Domain modules.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"Core should be the foundation layer that domains depend on, "
                f"not the other way around.\n"
                f"Note: src/core/base/base.py is allowed as a re-export module (technical debt)."
            )

    def test_no_circular_dependencies_between_domains(self):
        """
        Test that there are no circular dependencies between domains.

        This test builds a dependency graph and checks for cycles.
        """
        # Build dependency graph
        graph = {}
        for layer in ["input", "decision", "output"]:
            dependencies = analyze_layer_dependencies(layer)
            # Filter to only domain-to-domain dependencies
            domain_deps = [
                target for target in dependencies.keys()
                if target in ["input", "decision", "output"]
            ]
            graph[layer] = set(domain_deps)

        # Check for cycles using DFS
        def has_cycle(node: str, visited: Set[str], rec_stack: Set[str], path: List[str]) -> Tuple[bool, List[str]]:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    has_cycle_result, cycle_path = has_cycle(neighbor, visited, rec_stack, path)
                    if has_cycle_result:
                        return True, cycle_path
                elif neighbor in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    return True, cycle

            path.pop()
            rec_stack.remove(node)
            return False, []

        visited = set()
        for layer in graph:
            if layer not in visited:
                has_cycle_result, cycle_path = has_cycle(layer, visited, set(), [])
                if has_cycle_result:
                    raise AssertionError(
                        f"Circular dependency detected between domains!\n"
                        f"Cycle: {' → '.join(cycle_path)}\n\n"
                        f"This violates the unidirectional data flow architecture. "
                        f"Dependencies should flow: Input → Decision → Output"
                    )

    def test_services_layer_independence(self):
        """
        Test that Services layer does not depend on any Domain modules.

        Services (like LLM, Config) should be independent infrastructure
        that domains can use, but services shouldn't know about domains.
        """
        dependencies = analyze_layer_dependencies("services")

        violations = []

        # Check for any domain imports
        for target_layer in ["input", "decision", "output"]:
            for dep in dependencies.get(target_layer, []):
                violations.append({
                    "target": target_layer,
                    "file": dep["file"],
                    "module": dep["module"],
                    "line": dep["line"]
                })

        if violations:
            violation_details = "\n".join([
                f"  - {v['file']}:{v['line']} "
                f"imports from {v['target']} domain (module: {v['module']})"
                for v in violations
            ])
            raise AssertionError(
                f"Services layer MUST NOT depend on Domain modules.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"Services should be independent infrastructure. "
                f"Domains can depend on Services, but not vice versa."
            )

    def test_proper_layer_hierarchy(self):
        """
        Test that all dependencies follow the proper layer hierarchy.

        Hierarchy (low to high): Core/Services → Input → Decision → Output

        Higher layers can depend on lower layers, but lower layers cannot
        depend on higher layers.
        """
        layer_rank = {
            "core": 0,
            "services": 0,
            "input": 1,
            "decision": 2,
            "output": 3
        }

        violations = []

        for layer in ["input", "decision", "output"]:
            dependencies = analyze_layer_dependencies(layer)

            for target_layer, deps in dependencies.items():
                if target_layer not in layer_rank:
                    continue

                # Check if dependency violates hierarchy
                if layer_rank[layer] < layer_rank[target_layer]:
                    for dep in deps:
                        violations.append({
                            "source": layer,
                            "target": target_layer,
                            "file": dep["file"],
                            "module": dep["module"],
                            "line": dep["line"]
                        })

        if violations:
            violation_details = "\n".join([
                f"  - {v['source']} → {v['target']}: "
                f"{v['file']}:{v['line']} (module: {v['module']})"
                for v in violations
            ])
            raise AssertionError(
                f"Dependencies must follow the layer hierarchy.\n"
                f"Hierarchy: Core/Services (0) → Input (1) → Decision (2) → Output (3)\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"Higher layers can depend on lower layers, but not vice versa."
            )

    def test_event_based_communication_pattern(self):
        """
        Test that cross-domain communication follows event-based pattern.

        This is a guidance test that documents the intended communication pattern.
        It doesn't fail but provides useful information about the architecture.
        """
        # Get all cross-domain dependencies
        all_dependencies = {}
        for layer in ["input", "decision", "output"]:
            all_dependencies[layer] = analyze_layer_dependencies(layer)

        # Count direct imports vs expected event-based communication
        direct_imports = 0
        for layer, deps in all_dependencies.items():
            for target_layer in ["input", "decision", "output"]:
                if target_layer == layer:
                    continue
                direct_imports += len(deps.get(target_layer, []))

        # This test provides guidance but doesn't fail
        # It documents the architectural expectation
        if direct_imports == 0:
            # Perfect! No direct imports between domains
            pass
        else:
            # The constraint tests above will catch actual violations
            # This just documents the expectation
            pass

    def test_orchestrators_only_in_core_root(self):
        """
        Test that orchestrator files are only in specific locations.

        Orchestrators (amaidesu_core.py, flow_coordinator.py) should only
        exist at the root of src/core/ to prevent their proliferation.
        """
        project_root = get_project_root()
        src_path = project_root / "src"

        orchestrator_files = []
        for py_file in src_path.rglob("*.py"):
            if get_layer_from_path(py_file) == "orchestrator":
                # Check if it's directly in src/core/ (not in subdirectories)
                rel_path = py_file.relative_to(src_path)
                # Use os.path.normpath to handle both forward and backward slashes
                import os
                normalized_path = os.path.normpath(str(rel_path))
                if normalized_path not in ["core\\amaidesu_core.py", "core/amaidesu_core.py",
                                          "core\\flow_coordinator.py", "core/flow_coordinator.py"]:
                    orchestrator_files.append(str(py_file))

        if orchestrator_files:
            file_list = "\n".join([f"  - {f}" for f in orchestrator_files])
            raise AssertionError(
                f"Orchestrator files should only be at src/core/ root level.\n"
                f"Found orchestrator files in unexpected locations:\n"
                f"{file_list}\n\n"
                f"Only amaidesu_core.py and flow_coordinator.py should be orchestrators."
            )
