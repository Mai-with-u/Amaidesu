"""验证阶段通知复用已有成果、保留原目标，且不替模型决定开工。"""

from unittest.mock import MagicMock

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
