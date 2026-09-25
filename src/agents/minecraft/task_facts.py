"""把待应答决策和失败事实留在任务上下文，完整施工回执继续由观察原件保存。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.agents.minecraft.readback import is_reference


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
        context_values = context.get("summary", {}) if is_reference(context) else context
        facts["context"] = {
            key: deepcopy(context_values[key])
            for key in ("ability", "failure_code", "ordinary_retry_allowed", "semantic_goal_rule")
            if key in context_values
        }
        # 诊断路径从保存的整份回执根部计算，注意流和 attention 的包装层不能在补读时丢掉。
        prefix = (
            "/data"
            if snapshot.get("event_type") == "decision"
            else ("/task/decision" if isinstance(snapshot.get("task"), dict) else "/decision")
        )
        prefix += "/context/failure" if "failure" in context else "/context"
        facts["failure_evidence"] = failure_evidence(context.get("failure", context), prefix)
        # 本地观察路径指向收到的摘要；远端 detail_path 指向 Mod 保留的原件，两者不能互相冒充。
        task = snapshot.get("task", snapshot)
        task_id = task.get("task_id") if isinstance(task, dict) else None
        for row in facts["failure_evidence"]:
            if row.get("resource_uri"):
                row["read_arguments"] = {
                    "tool": "maicraft_perceive",
                    "arguments": {"resource_uri": row["resource_uri"]},
                }
            elif row.get("detail_path") and isinstance(task_id, str):
                row["read_arguments"] = {
                    "tool": "maicraft_task",
                    "arguments": {"action": "get", "task_id": task_id, "path": row["detail_path"]},
                }
    return facts


def failure_evidence(value: Any, path: str = "") -> list[dict[str, Any]]:
    """沿原生失败链保留诊断与材料数量；布局和端口全集仍从同一份完整原件补读。"""
    if isinstance(value, list):
        return [row for index, item in enumerate(value) for row in failure_evidence(item, f"{path}/{index}")]
    if not isinstance(value, dict):
        return []
    if is_reference(value):
        # 摘要中的原生布尔值仍是真实事实，缺失的诊断只能保留读取入口，不能猜测为没有问题。
        summary = value.get("summary")
        known = failure_evidence(summary, path + "/summary") if isinstance(summary, dict) else []
        reference = {
            key: deepcopy(value[key]) for key in ("detail_path", "resource_uri", "type", "total") if key in value
        }
        return [{"path": path, "omitted": True, **reference}, *known]
    keys = (
        "message",
        "failure_code",
        "cause_code",
        "failure_type",
        "phase",
        "outcome_uncertain",
        "mechanical_retry_allowed",
        "effects_started",
        "pending_output",
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
    facts = {key: deepcopy(value[key]) for key in keys if key in value and not is_reference(value[key])}
    rows = [{"path": path, **facts}] if facts else []
    for key in keys:
        if is_reference(value.get(key)):
            rows.extend(failure_evidence(value[key], f"{path}/{key}"))
    # 只沿执行器公开的结果链取事实，不扫描 goal/blueprint，避免把旧设计参数误当成当前缺口。
    for key in ("data", "result", "last_native_stage", "batches", "supply", "last_build_evidence", "child_data"):
        if key in value:
            rows.extend(failure_evidence(value[key], f"{path}/{key}"))
    return rows
