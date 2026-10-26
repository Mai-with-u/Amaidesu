"""WakeQueue 直测：队列项协议（谁能开批、谁能被替换、谁能被撤）一处验证。"""

from __future__ import annotations

from src.agents.minecraft.state import MinecraftInstruction, WakeEvenIdle
from src.agents.minecraft.wake_queue import SkyNotice, WakeQueue, can_wake_idle, opens_task


def test_opens_task_dispatches_by_item_kind_and_task_state() -> None:
    """开批判定：指令永远开任务；闲着也叫醒的通知只在已交付时开；普通与天色通知永远不开。"""
    instruction = MinecraftInstruction("t1", "建房子")
    wake = WakeEvenIdle("", "计时到点")
    notice = ("", "目标结束了")
    sky = SkyNotice("", "天黑了")
    assert opens_task(instruction, task_finished=True)
    assert opens_task(instruction, task_finished=False)
    assert opens_task(wake, task_finished=True)
    assert not opens_task(wake, task_finished=False), "挂起或进行中时只是接着原任务读的通知"
    assert not opens_task(notice, task_finished=True)
    assert not opens_task(notice, task_finished=False)
    assert not opens_task(sky, task_finished=True)


def test_can_wake_idle_agrees_with_opens_task_on_both_idle_states() -> None:
    """闲着叫醒判定与开批判定同源：两种闲着状态任一能开即能叫醒。"""
    assert can_wake_idle(MinecraftInstruction("t1", "建房子"))
    assert can_wake_idle(WakeEvenIdle("", "计时到点"))
    assert not can_wake_idle(("", "目标结束了"))
    assert not can_wake_idle(SkyNotice("", "天黑了"))


def test_drop_task_removes_only_matching_entries() -> None:
    """按任务号撤单：只撤该任务的条目，返回是否撤到；其他任务的与无任务号的都不动。"""
    queue = WakeQueue()
    queue.push_instruction("t1", "建房子")
    queue.push_notice("目标结束了")
    queue.push_instruction("t2", "去挖矿")
    queue.push_wake_even_idle("计时到点")
    assert queue.drop_task("t1")
    assert [(item[0], item[-1]) for item in queue] == [
        ("", "目标结束了"),
        ("t2", "去挖矿"),
        ("", "计时到点"),
    ]
    assert not queue.drop_task("t1"), "没有该任务的条目时返回 False"
    assert not queue.drop_task(""), "空任务号不参与撤单，普通通知不被误撤"


def test_replace_sky_notice_keeps_only_latest_by_identity() -> None:
    """天色替换按对象身份撤下旧条目：同文新条目不会让旧条目躲过替换，队列里只留最新一条。"""
    queue = WakeQueue()
    queue.push_notice("目标结束了")
    queue.replace_sky_notice("天黑了")
    queue.replace_sky_notice("天黑了")  # 两次天色逐字相同也只留一条
    assert len(queue) == 2, "普通通知 + 最新一条天色"
    assert [item[-1] for item in queue] == ["目标结束了", "天黑了"]
    latest = queue.items[-1]
    assert isinstance(latest, SkyNotice)
    queue.pop()
    queue.pop()
    assert len(queue) == 0


def test_drop_sky_notice_removes_pending_sky_only() -> None:
    """撤天色：只撤还没被读到的天色条目，其他消息原样保留。"""
    queue = WakeQueue()
    queue.push_notice("目标结束了")
    queue.replace_sky_notice("开始下雨了")
    queue.drop_sky_notice()
    assert [item[-1] for item in queue] == ["目标结束了"]
    queue.drop_sky_notice()  # 没有挂着的天色时撤单是空操作
    assert len(queue) == 1


def test_clear_drops_everything_including_pending_sky() -> None:
    """清空队列：天色簿记一并复位，之后替换不会试图撤已清掉的旧条目。"""
    queue = WakeQueue()
    queue.push_instruction("t1", "建房子")
    queue.replace_sky_notice("天黑了")
    queue.clear()
    assert len(queue) == 0 and not queue
    queue.replace_sky_notice("天亮了")
    assert [item[-1] for item in queue] == ["天亮了"]


def test_can_wake_reports_whether_idle_agent_would_open_a_batch() -> None:
    """能否开批：只有指令与闲着也叫醒的通知算数，普通通知与天色不算。"""
    queue = WakeQueue()
    queue.push_notice("目标结束了")
    queue.replace_sky_notice("天黑了")
    assert not queue.can_wake()
    queue.push_wake_even_idle("计时到点")
    assert queue.can_wake()
