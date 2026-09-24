"""验证阶段通知复用已有成果、保留原目标，且不替模型决定开工。"""

from unittest.mock import MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.events.payloads.tasks import TaskChangedPayload


def make_agent() -> MinecraftAgent:
    """只测试任务事实装配，不连接 Mod 或调用外部模型。"""
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock())
    agent._running = True
    agent._task_finished = False
    agent._task_instructions = ["建好机器；禁用 Mek；禁止搜索私人箱子"]
    return agent


def test_design_result_retains_original_goal_and_links_exact_request() -> None:
    """局部只审阅目标与原建造目标同时保留，完整蓝图参数可由原件引用读取。"""
    agent = make_agent()
    request = {
        "goal": {
            "ability": "maicraft:design_machine",
            "outcome": "只审阅，不施工",
            "parameters": {"blueprint": {"blocks": []}},
        }
    }
    receipt = {"accepted": True, "task_id": "design"}
    shown = agent._observations.present("maicraft_execute", request, receipt)
    agent._remember_result("maicraft_execute", request, receipt, shown)
    notice = TaskChangedPayload(
        task_id="design",
        status="succeeded",
        initiator="minecraft",
        executor="maicraft",
        snapshot={"buildable": True, "blueprint_id": "bp1", "content": "场地细节" * 5000},
    )
    agent.on_task_notification(notice)
    agent.on_task_notification(notice)
    assert len(agent._message_queue) == 1
    message = agent._message_queue[0][1]
    assert "bp1" in message and "沿原目标" in message and len(message) < 8000
    context = agent._current_task_context()
    assert context["original_instructions"] == ["建好机器；禁用 Mek；禁止搜索私人箱子"]
    stage = context["background_tasks"][0]
    assert stage["status"] == "succeeded" and stage["requested_goal"]["outcome"] == "只审阅，不施工"
    original = agent._read_observation(
        {"ref": stage["request_ref"], "source": "request", "path": "/goal/parameters/blueprint"}
    )
    assert original["text"] == '{"blocks":[]}' and stage["result_ref"]
    agent._llm.generate.assert_not_called()


def test_running_progress_is_silent_and_decision_includes_existing_evidence() -> None:
    """普通进度只更新事实，待决策通知才把已知缺口交给模型。"""
    agent = make_agent()
    for progress in range(5):
        agent.on_task_notification(
            TaskChangedPayload(task_id="work", status="running", initiator="minecraft", snapshot={"progress": progress})
        )
    assert not agent._message_queue
    agent.on_task_notification(
        TaskChangedPayload(
            task_id="work",
            status="waiting_for_decision",
            initiator="minecraft",
            snapshot={"decision": {"question": "选择已授权材料来源"}},
        )
    )
    assert len(agent._message_queue) == 1 and "已授权材料来源" in agent._message_queue[0][1]


def test_compaction_keeps_ready_plan_for_direct_execution() -> None:
    """规划后即使整理历史，模型仍拿得到计划编号，不需要重查蓝图或再发起设计审阅。"""
    agent = make_agent()
    request = {"goal": {"ability": "maicraft:build_machine", "parameters": {"snapshot_id": "site"}}}
    result = {"plan_id": "compiled-plan", "ready_to_execute": True, "status": "compiled"}
    shown = agent._observations.present("maicraft_plan", request, result)
    agent._remember_result("maicraft_plan", request, result, shown)
    recent = agent._current_task_context()["recent_results"][-1]
    assert recent["plan_id"] == "compiled-plan" and recent["ready_to_execute"] is True
    # 复现生产环境：规划后连续读取能力，旧笔记仍有“待校验”；有效计划不能被六条近期回执淘汰。
    agent._mc_state.notebook = "当前蓝图待校验"
    for index in range(9):
        query = {"view": "abilities", "query": str(index)}
        observed = {"semantic_abilities": [], "total_matches": 0}
        display = agent._observations.present("maicraft_perceive", query, observed)
        agent._remember_result("maicraft_perceive", query, observed, display)
        assert display["_pending_execution"][0]["plan_id"] == "compiled-plan"
    context = agent._current_task_context()
    assert all("plan_id" not in result for result in context["recent_results"])
    assert context["plan_facts"][0]["state"] == "ready"
    assert context["plan_facts"][0]["result_ref"] == shown["_observation"]["ref"]


@pytest.mark.parametrize(
    ("receipt", "state"),
    [
        ({"accepted": True, "task_id": "build"}, "submitted"),
        ({"ok": False, "error": {"outcome_known": True}}, "execution_rejected"),
        ({"ok": False, "error": {"outcome_known": False}}, "execution_unknown"),
    ],
)
def test_plan_execution_receipt_removes_pending_hint(receipt: dict, state: str) -> None:
    """执行受理或结果未知都不能继续提示直接开工，防止等待期间或失败后重复施工。"""
    agent = make_agent()
    goal = {"ability": "maicraft:build_machine", "outcome": "建造"}
    agent._remember_result("maicraft_plan", {"goal": goal}, {"plan_id": "plan", "ready_to_execute": True}, {})
    agent._remember_result("maicraft_execute", {"plan_id": "plan"}, receipt, {})
    assert agent._current_task_context()["plan_facts"][0]["state"] == state
    assert not agent._plan_facts.pending()


def test_rejected_revision_does_not_revive_previous_plan() -> None:
    """同一目标的新版本未通过时保留旧编号作为历史，避免按过时约束开工。"""
    agent = make_agent()
    goal = {"ability": "maicraft:build_machine", "outcome": "建造"}
    agent._remember_result("maicraft_plan", {"goal": goal}, {"plan_id": "old", "ready_to_execute": True}, {})
    agent._remember_result("maicraft_plan", {"goal": goal}, {"ready_to_execute": False}, {})
    assert agent._current_task_context()["plan_facts"][0]["state"] == "superseded"
    assert not agent._plan_facts.pending()


def test_builder_completion_delivers_artifact_without_automatic_execution() -> None:
    """建筑产物引用随完成通知交付，父模型可直接决策执行，无需先空查一次状态。"""
    agent = make_agent()
    agent.on_task_notification(
        TaskChangedPayload(
            task_id="building",
            status="succeeded",
            initiator="minecraft",
            executor="minecraft_builder",
            snapshot={"intent": "build", "result": {"artifact_ref": "scene-1", "validated": True}},
        )
    )
    message = agent._message_queue[0][1]
    assert "scene-1" in message and "action=execute" in message and "缺少具体信息" in message
    agent._llm.generate.assert_not_called()
