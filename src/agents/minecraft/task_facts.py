"""把待应答决策和失败事实留在任务上下文，完整施工回执继续由观察原件保存。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def task_decision(snapshot: dict[str, Any]) -> dict[str, Any]:
    """兼容注意流决策和任务快照，只认原生决策位置，避免误读蓝图中的同名字段。"""
    task = snapshot.get("task", snapshot)
    if not isinstance(task, dict):
        return {}
    decision = task.get("decision")
    if not isinstance(decision, dict) and snapshot.get("event_type") == "decision":
        decision = snapshot.get("data")
    return decision if isinstance(decision, dict) else {}


def decision_facts(snapshot: dict[str, Any], summary: str = "") -> dict[str, Any]:
    """编号、选项和实际缺口独立于模型摘要保存，读过蓝图后可以直接回答同一个决策。"""
    decision = task_decision(snapshot)
    if not decision:
        return {}
    facts = {key: deepcopy(decision[key]) for key in ("decision_id", "question", "options") if key in decision}
    if "question" not in facts and summary:
        facts["question"] = summary
    context = decision.get("context")
    if isinstance(context, dict):
        facts["context"] = {
            key: deepcopy(context[key])
            for key in ("ability", "failure_code", "ordinary_retry_allowed", "semantic_goal_rule")
            if key in context
        }
        # 诊断路径从保存的整份回执根部计算，注意流和 attention 的包装层不能在补读时丢掉。
        prefix = (
            "/data"
            if snapshot.get("event_type") == "decision"
            else ("/task/decision" if isinstance(snapshot.get("task"), dict) else "/decision")
        )
        prefix += "/context/failure" if "failure" in context else "/context"
        facts["failure_evidence"] = failure_evidence(context.get("failure", context), prefix)
    return facts


def failure_evidence(value: Any, path: str = "") -> list[dict[str, Any]]:
    """沿原生失败链保留诊断与材料数量；布局和端口全集仍从同一份完整原件补读。"""
    if isinstance(value, list):
        return [row for index, item in enumerate(value) for row in failure_evidence(item, f"{path}/{index}")]
    if not isinstance(value, dict):
        return []
    keys = (
        "message",
        "failure_code",
        "cause_code",
        "failure_type",
        "phase",
        "outcome_uncertain",
        "world_change_uncertain",
        "requires_narration",
        "item_ids",
        "item_id",
        "required_final_count",
        "observed_final_count",
        "missing",
        "blocked_need",
        "planning_handoff",
        "recipe_trace",
        "issues",
        "recovery_options",
        "build_diagnostics",
    )
    facts = {key: deepcopy(value[key]) for key in keys if key in value}
    rows = [{"path": path, **facts}] if facts else []
    # 只沿执行器公开的结果链取事实，不扫描 goal/blueprint，避免把旧设计参数误当成当前缺口。
    for key in ("data", "result", "last_native_stage", "batches", "supply", "last_build_evidence", "child_data"):
        if key in value:
            rows.extend(failure_evidence(value[key], f"{path}/{key}"))
    return rows
