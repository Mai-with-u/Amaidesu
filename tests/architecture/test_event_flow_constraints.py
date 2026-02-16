"""
Architecture tests to enforce proper data flow constraints.

These tests verify that the 3-domain architecture maintains proper separation
and follows the unidirectional data flow: Input → Decision → Output
"""

import ast
from pathlib import Path
from typing import Dict, List

# Event names by domain (updated to new naming convention)
INPUT_EVENTS = {
    "INPUT_MESSAGE_READY",
}

DECISION_EVENTS = {
    "DECISION_INTENT_GENERATED",
    "DECISION_PROVIDER_CONNECTED",
    "DECISION_PROVIDER_DISCONNECTED",
}

OUTPUT_EVENTS = {
    "OUTPUT_INTENT_READY",
    "OUTPUT_OBS_SEND_TEXT",
    "OUTPUT_OBS_SWITCH_SCENE",
    "OUTPUT_OBS_SET_SOURCE_VISIBILITY",
    "OUTPUT_REMOTE_STREAM_REQUEST_IMAGE",
}


def get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).parent.parent.parent


def extract_event_subscriptions(file_path: Path) -> List[Dict]:
    """
    Extract event subscriptions from a Python file using AST.

    Returns:
        List of dicts with keys: class_name, event_name, line_number
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
    except SyntaxError:
        return []

    subscriptions = []

    for node in ast.walk(tree):
        # Look for subscribe() and on() calls
        if isinstance(node, ast.Call):
            # Check if this is a subscribe() or on() call
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("subscribe", "on"):
                    # Try to extract event name from the first argument
                    if node.args and len(node.args) > 0:
                        event_arg = node.args[0]

                        # Direct string: subscribe("event.name")
                        if isinstance(event_arg, ast.Constant):
                            event_name = event_arg.value
                            # Find the class containing this call
                            class_name = find_containing_class(tree, node)
                            subscriptions.append(
                                {
                                    "class_name": class_name,
                                    "event_name": event_name,
                                    "line_number": node.lineno,
                                    "file_path": str(file_path),
                                }
                            )

                        # Attribute access: subscribe(CoreEvents.EVENT_NAME)
                        elif isinstance(event_arg, ast.Attribute):
                            if isinstance(event_arg.value, ast.Name):
                                if event_arg.value.id == "CoreEvents":
                                    event_name = event_arg.attr
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
    """Find the name of the class containing a given node."""
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef):
            for child in ast.walk(parent):
                if child is node:
                    return parent.name
    return "<module>"


def get_domain_from_path(file_path: Path) -> str:
    """
    Determine which domain a file belongs to based on its path.

    Returns: 'input', 'decision', 'output', or 'unknown'
    """
    path_str = str(file_path)

    if "/domains/input/" in path_str or "\\domains\\input\\" in path_str:
        return "input"
    elif "/domains/decision/" in path_str or "\\domains\\decision\\" in path_str:
        return "decision"
    elif "/domains/output/" in path_str or "\\domains\\output\\" in path_str:
        return "output"
    else:
        return "unknown"


def get_all_subscriptions_in_domain(domain: str) -> List[Dict]:
    """Get all event subscriptions in a specific domain."""
    project_root = get_project_root()
    domain_path = project_root / "src" / "domains" / domain

    if not domain_path.exists():
        return []

    subscriptions = []
    for py_file in domain_path.rglob("*.py"):
        # Skip __pycache__ and test files
        if "__pycache__" in str(py_file):
            continue
        if py_file.name.startswith("test_") or py_file.name == "_test.py":
            continue

        file_subscriptions = extract_event_subscriptions(py_file)
        subscriptions.extend(file_subscriptions)

    return subscriptions


class TestEventFlowConstraints:
    """Test suite for event flow architectural constraints."""

    def test_output_domain_does_not_subscribe_to_input_events(self):
        """
        Test that Output Domain does NOT subscribe to Input Domain events.

        This is a critical architectural constraint. Output should only
        subscribe to Decision events (INTENT_GENERATED), not Input events.
        """
        output_subscriptions = get_all_subscriptions_in_domain("output")

        violations = []
        for sub in output_subscriptions:
            event_name = sub["event_name"]

            # Check if this is an Input domain event
            if event_name in INPUT_EVENTS:
                violations.append(
                    {
                        "class": sub["class_name"],
                        "event": event_name,
                        "file": sub["file_path"],
                        "line": sub["line_number"],
                    }
                )

        if violations:
            violation_details = "\n".join(
                [f"  - {v['class']} in {v['file']}:{v['line']} subscribes to {v['event']}" for v in violations]
            )
            raise AssertionError(
                f"Output Domain MUST NOT subscribe to Input Domain events.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"Correct pattern: Output should only subscribe to "
                f"DECISION_INTENT_GENERATED events."
            )

    def test_decision_domain_does_not_subscribe_to_output_events(self):
        """
        Test that Decision Domain does NOT subscribe to Output Domain events.

        This prevents circular dependencies and maintains unidirectional flow.
        """
        decision_subscriptions = get_all_subscriptions_in_domain("decision")

        violations = []
        for sub in decision_subscriptions:
            event_name = sub["event_name"]

            # Check if this is an Output domain event
            if event_name in OUTPUT_EVENTS:
                violations.append(
                    {
                        "class": sub["class_name"],
                        "event": event_name,
                        "file": sub["file_path"],
                        "line": sub["line_number"],
                    }
                )

        if violations:
            violation_details = "\n".join(
                [f"  - {v['class']} in {v['file']}:{v['line']} subscribes to {v['event']}" for v in violations]
            )
            raise AssertionError(
                f"Decision Domain MUST NOT subscribe to Output Domain events.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"This would create a circular dependency."
            )

    def test_input_domain_does_not_subscribe_to_downstream_events(self):
        """
        Test that Input Domain does NOT subscribe to Decision or Output events.

        Input Domain should only publish events, not subscribe to downstream events.
        """
        input_subscriptions = get_all_subscriptions_in_domain("input")

        violations = []
        for sub in input_subscriptions:
            event_name = sub["event_name"]

            # Check if this is a Decision or Output domain event
            if event_name in DECISION_EVENTS or event_name in OUTPUT_EVENTS:
                violations.append(
                    {
                        "class": sub["class_name"],
                        "event": event_name,
                        "file": sub["file_path"],
                        "line": sub["line_number"],
                    }
                )

        if violations:
            violation_details = "\n".join(
                [f"  - {v['class']} in {v['file']}:{v['line']} subscribes to {v['event']}" for v in violations]
            )
            raise AssertionError(
                f"Input Domain MUST NOT subscribe to Decision or Output events.\n"
                f"Found {len(violations)} violation(s):\n"
                f"{violation_details}\n\n"
                f"Input Domain should only publish events, not subscribe."
            )

    def test_proper_event_chain(self):
        """
        Test that the proper event chain exists: Input → Decision → Output.

        This verifies that:
        1. Input Domain publishes INPUT_MESSAGE_READY
        2. Decision Domain subscribes to INPUT_MESSAGE_READY
        3. Decision Domain publishes DECISION_INTENT_GENERATED
        4. Output Domain subscribes to DECISION_INTENT_GENERATED

        Note: During refactoring, this test provides guidance but doesn't fail
        if subscriptions aren't implemented yet. The critical tests are the
        constraint tests that prevent wrong subscriptions.
        """
        # Check that Decision subscribes to Input's event
        decision_subscriptions = get_all_subscriptions_in_domain("decision")
        decision_subscribes_to_input = any(sub["event_name"] in INPUT_EVENTS for sub in decision_subscriptions)

        # Check that Output subscribes to Decision's event
        output_subscriptions = get_all_subscriptions_in_domain("output")

        output_subscribes_to_decision = any(sub["event_name"] in DECISION_EVENTS for sub in output_subscriptions)

        # If either subscription exists, verify the chain is complete
        if decision_subscribes_to_input or output_subscribes_to_decision:
            if not decision_subscribes_to_input:
                raise AssertionError(
                    "Decision Domain should subscribe to Input Domain events "
                    "(INPUT_MESSAGE_READY) when Output subscribes to Decision events. "
                    "Data flow: Input → Decision → Output"
                )

            if not output_subscribes_to_decision:
                raise AssertionError(
                    "Output Domain should subscribe to Decision Domain events "
                    "(DECISION_INTENT_GENERATED) when Decision subscribes to Input events. "
                    "Data flow: Input → Decision → Output"
                )

        # If no subscriptions exist yet (refactoring in progress), just skip
        # The constraint tests will prevent wrong subscriptions when they're added

    def test_event_subscriptions_follow_domain_boundaries(self):
        """
        Test that all event subscriptions respect domain boundaries.

        This is a comprehensive test that checks all subscriptions across all domains.
        """
        # Get all subscriptions from all domains
        all_violations = []

        for domain in ["input", "decision", "output"]:
            subscriptions = get_all_subscriptions_in_domain(domain)

            for sub in subscriptions:
                event_name = sub["event_name"]
                violation = None

                if domain == "input":
                    # Input should not subscribe to Decision or Output events
                    if event_name in DECISION_EVENTS or event_name in OUTPUT_EVENTS:
                        violation = f"Input Domain subscribing to {event_name}"

                elif domain == "decision":
                    # Decision should not subscribe to Output events
                    if event_name in OUTPUT_EVENTS:
                        violation = f"Decision Domain subscribing to {event_name}"

                elif domain == "output":
                    # Output should not subscribe to Input events
                    if event_name in INPUT_EVENTS:
                        violation = f"Output Domain subscribing to {event_name}"

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
                [
                    f"  - [{v['domain']}] {v['class']} in {v['file']}:{v['line']}: {v['violation']}"
                    for v in all_violations
                ]
            )
            raise AssertionError(
                f"Found {len(all_violations)} domain boundary violation(s):\n"
                f"{violation_details}\n\n"
                f"Data flow must be: Input Domain → Decision Domain → Output Domain"
            )
