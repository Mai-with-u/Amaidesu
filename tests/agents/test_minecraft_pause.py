"""后台任务被 Mod 暂停时唤醒游戏 Agent 的回归测试。

通用任务词表没有"暂停"，跟踪器会忽略它，账面一直是 running——实测中身体停着
5 分钟，游戏 Agent 却在 minecraft_wait 里干等。覆盖：
- 新版简短通知：核实快照为 paused 时唤醒自己、记下原因、通报主播
- 自卫离位等不会自行恢复的暂停：等待被拒，提示先处理
- 控制权暂不可用的暂停：允许等待，但如实说明身体没在动
- 同一次暂停只唤醒一次；恢复、终态、待答问题都会撤掉暂停记录
- 旧版完整事件体同样识别暂停/恢复
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.config import MinecraftConfig
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.tasks import TaskChangedPayload
from src.modules.tools.tasks import TaskLedger


class _FakeAttentionProvider:
    """只实现任务查询：按预置状态返回 Mod 快照。"""

    def __init__(self) -> None:
        self.states: Dict[str, Dict[str, Any]] = {}

    async def query_task(self, task_id: str) -> Dict[str, Any]:
        snapshot = dict(self.states[task_id])
        return {"status": snapshot["state"], "snapshot": snapshot, "summary": snapshot["state"]}


def _agent() -> tuple[MinecraftAgent, _FakeAttentionProvider, MagicMock]:
    bus = MagicMock()
    bus.emit = AsyncMock()
    tracker = MagicMock()
    tracker.ledger = TaskLedger()
    agent = MinecraftAgent(MinecraftConfig(), llm_manager=MagicMock(), event_bus=bus, task_tracker=tracker)
    provider = _FakeAttentionProvider()
    agent._attention_provider = provider
    agent._task_finished = False
    tracker.ledger.register(
        task_id="t1",
        provider="maicraft",
        tool="maicraft_execute",
        initiator="minecraft",
        executor="maicraft",
        status="running",
        source="provider",
    )
    return agent, provider, bus


def _queued(agent: MinecraftAgent) -> List[str]:
    return [content for _task_id, content in agent._message_queue]


async def _drain(agent: MinecraftAgent) -> None:
    for _ in range(5):
        if not agent._bg_tasks:
            return
        await asyncio.gather(*list(agent._bg_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_displaced_pause_wakes_agent_and_blocks_idle_wait() -> None:
    """自卫离位暂停：唤醒自己说明原因与可选处理，主播得到叙事；此时等待被拒。"""
    agent, provider, bus = _agent()
    provider.states["t1"] = {"task_id": "t1", "state": "paused", "pause_reason": "self_defense_displaced"}

    await agent._verify_short_task_event({"task_id": "t1", "type": "paused", "message": "Task paused: moved away"})
    await _drain(agent)

    assert agent._paused_tasks == {"t1": "self_defense_displaced"}
    wake = _queued(agent)
    assert len(wake) == 1 and "已被 Mod 暂停" in wake[0] and "自卫时被带离了工位" in wake[0]
    assert "maicraft_task(action=resume)" in wake[0] and "Task paused: moved away" in wake[0]
    narrated = [
        call.args[1].message for call in bus.emit.await_args_list if call.args[0] == CoreEvents.GAME_ATTENTION_REQUIRED
    ]
    assert narrated == ["身体手上的游戏内动作暂停了：自卫时被带离了工位。"]
    refused = agent._request_wait()
    assert refused["ok"] is False and refused["paused_tasks"] == {"t1": "self_defense_displaced"}


@pytest.mark.asyncio
async def test_control_unavailable_pause_allows_wait_but_says_body_is_idle() -> None:
    """控制权暂不可用：Mod 会自动恢复，允许等待，但回执写明身体此刻没在动。"""
    agent, provider, _bus = _agent()
    provider.states["t1"] = {"task_id": "t1", "state": "paused", "pause_reason": "control_unavailable"}

    await agent._verify_short_task_event({"task_id": "t1", "type": "paused"})
    assert "第一人称控制暂时不可用" in _queued(agent)[0] and "自动继续" in _queued(agent)[0]
    agent._message_queue.clear()
    waiting = agent._request_wait()

    assert waiting["ok"] is True and waiting["waiting"] is True
    assert waiting["paused_tasks"] == {"t1": "control_unavailable"} and "没有在执行" in waiting["note"]


@pytest.mark.asyncio
async def test_same_pause_wakes_once_and_resume_or_terminal_clears_it() -> None:
    """同一次暂停只唤醒一次；Mod 自动恢复后撤销暂停，终态通知同样撤销。"""
    agent, provider, _bus = _agent()
    provider.states["t1"] = {"task_id": "t1", "state": "paused", "pause_reason": "control_unavailable"}

    await agent._verify_short_task_event({"task_id": "t1", "type": "paused"})
    await agent._verify_short_task_event({"task_id": "t1", "type": "paused"})
    assert len(_queued(agent)) == 1

    provider.states["t1"]["state"] = "running"
    await agent._verify_short_task_event({"task_id": "t1", "type": "resumed"})
    assert agent._paused_tasks == {}

    agent._paused_tasks["t1"] = "self_defense_displaced"
    agent.on_task_notification(
        TaskChangedPayload(task_id="t1", status="cancelled", initiator="minecraft", executor="maicraft")
    )
    assert agent._paused_tasks == {}


@pytest.mark.asyncio
async def test_legacy_full_events_recognize_pause_and_resume() -> None:
    """旧版注意流的完整事件体：paused 唤醒、resumed 撤销，都不写进通用账面。"""
    agent, _provider, _bus = _agent()

    agent._absorb_task_event(
        {"task_id": "t1", "type": "paused", "message": "paused by model", "data": {"reason": "model_pause"}}
    )
    assert agent._paused_tasks == {"t1": "model_pause"} and len(_queued(agent)) == 1
    assert agent._task_tracker.ledger.get("t1").status == "running"

    agent._absorb_task_event({"task_id": "t1", "type": "resumed", "message": "Task resumed", "data": {}})
    assert agent._paused_tasks == {}
    await _drain(agent)
