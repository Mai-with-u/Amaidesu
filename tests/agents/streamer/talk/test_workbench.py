"""话头工作台：提纲要点按轮推进、话题卡在聊、被打断尾句与防早退查询。"""

from __future__ import annotations

from src.agents.streamer.rundown.rundown_state import RundownState
from src.agents.streamer.talk.workbench import (
    MAX_WORKBENCH_CHARS,
    TANGENT_TTL_MS,
    TalkWorkbench,
)
from src.modules.storage.models.rundown import Rundown, RundownSegment

_BEATS = ["名字与招呼", "四亿年的来历", "平时播什么"]


def _bench() -> TalkWorkbench:
    bench = TalkWorkbench(max_rounds_per_beat=3)
    bench.sync_segment("origin", list(_BEATS))
    return bench


def test_beat_advances_on_declared_done_or_round_cap() -> None:
    bench = _bench()
    assert bench.beat_statuses() == ["pending", "pending", "pending"]

    bench.record_round()
    assert bench.beat_statuses()[0] == "talking"
    bench.record_round(beat_done=True)
    assert bench.beat_statuses()[:2] == ["done", "pending"]

    # 同一点连讲满 3 轮自动收束，不会卡死在车轱辘话
    for _ in range(3):
        bench.record_round()
    assert bench.beat_statuses() == ["done", "done", "pending"]
    assert bench.remaining_beats("origin", list(_BEATS)) == ["平时播什么"]

    bench.record_round(beat_done=True)
    assert bench.has_open_beat() is False
    bench.record_round()  # 全聊完后再记轮不抛、不变
    assert bench.beat_statuses() == ["done", "done", "done"]


def test_resync_same_segment_keeps_progress_but_new_segment_resets() -> None:
    bench = _bench()
    bench.record_round(beat_done=True)
    bench.note_interrupted_tail("说到维C的时候")
    bench.sync_segment("origin", list(_BEATS))
    assert bench.beat_statuses()[0] == "done"

    bench.sync_segment("outfit", ["头顶绿叶"])
    assert bench.beat_statuses() == ["pending"]
    assert bench.consume_interrupted_tail() == ""  # 换环节后旧尾句不再相关


def test_card_topic_counts_rounds_without_touching_beats() -> None:
    bench = _bench()
    bench.start_card(5, "腰上的旋钮能拧吗")
    assert bench.has_active_card() is True
    bench.record_round()
    bench.record_round()
    assert bench.beat_statuses() == ["pending", "pending", "pending"]
    assert "话题卡#5" in bench.render()
    bench.record_round(beat_done=True)
    assert bench.has_active_card() is False


def test_interrupted_tail_is_consumed_once_and_rendered() -> None:
    bench = _bench()
    bench.note_interrupted_tail("   然后  那年苹果 ")
    assert "上一轮被打断没说完：然后 那年苹果" in bench.render()
    assert bench.consume_interrupted_tail() == "然后 那年苹果"
    assert bench.consume_interrupted_tail() == ""


def test_render_is_bounded_and_empty_without_material() -> None:
    assert TalkWorkbench().render() == ""
    bench = TalkWorkbench()
    bench.sync_segment("long", [f"要点{i}" * 10 for i in range(20)])
    text = bench.render()
    assert text.startswith("【当前话头与本节提纲】") and len(text) <= MAX_WORKBENCH_CHARS


def test_unsynced_segment_reports_all_beats_remaining() -> None:
    bench = _bench()
    assert bench.remaining_beats("qna", ["抽提问箱"]) == ["抽提问箱"]


def test_workbench_drives_rundown_early_exit_guard() -> None:
    """接到流程单状态机后：提纲没聊完时 Agent next 被拒，聊完即放行。"""
    clock = [1_000_000]
    rundown = Rundown(
        rundown_id="bench_rd",
        title="工作台联动",
        segments=[
            RundownSegment(
                id="origin", title="来历", task_description="聊来历", expected_ms=600_000, beats=list(_BEATS)
            ),
            RundownSegment(id="closing", title="道别", task_description="道别", expected_ms=120_000),
        ],
    )
    bench = _bench()
    state = RundownState(clock=lambda: clock[0], remaining_beats_fn=bench.remaining_beats)
    state.load(rundown)
    state.begin(by="system")

    reject = state.next(by="agent")
    assert reject is not None and reject.remaining_beats == _BEATS
    for _ in range(3):
        bench.record_round(beat_done=True)
    assert state.next(by="agent") is None
    assert state.current_segment_id == "closing"


def test_viewer_tangent_renders_once_on_idle_play_window_and_respects_ttl_and_clears() -> None:
    """边玩边说档观众话头余温：空闲窗渲染【刚聊开的话头】，消费 1 轮、90s 超时、游戏急事或换环节即清空；追问刷新计时。"""
    bench = TalkWorkbench()
    bench.sync_segment("play_mc", [])
    clock_ms = 10_000

    # 启动余温（清洗多余空白、裁剪超长文本）并附带一条被打断尾句
    bench.start_viewer_tangent(
        "  像素配乐喜好  ", "  阿杰聊起街机厅像素风配乐，询问她平时爱听什么曲风  ", at_ms=clock_ms
    )
    bench.note_interrupted_tail("刚才那首标题画面的旋律")
    assert bench.has_active_tangent(now_ms=clock_ms + 5_000) is True

    # 有观众弹幕批时不渲染余温段，但余温本身不丢失
    assert bench.render_play_window(now_ms=clock_ms + 5_000, has_viewer_batch=True) == ""
    assert bench.has_active_tangent(now_ms=clock_ms + 5_000) is True

    # 无观众弹幕且无游戏急事时，渲染【刚聊开的话头】并一并带上被打断尾句，总字数受控
    rendered = bench.render_play_window(now_ms=clock_ms + 15_000, has_viewer_batch=False)
    assert rendered.startswith("【刚聊开的话头】\n")
    assert "「像素配乐喜好」——阿杰聊起街机厅像素风配乐" in rendered
    assert "聊完这一轮自然收束回手头游戏" in rendered
    assert "设定档案与记忆未记载的亲身经历不编" in rendered
    assert "上一轮被打断没说完：刚才那首标题画面的旋律" in rendered
    assert len(rendered) <= MAX_WORKBENCH_CHARS
    assert bench.consume_interrupted_tail() == "刚才那首标题画面的旋律"

    # 观众继续发弹幕追问时刷新时间戳并重置轮数：距首答超过 90s、但距追问未满 90s 时依然有效
    followup_ms = clock_ms + 60_000
    bench.start_viewer_tangent("像素配乐喜好", "小夏追问BOSS战快节奏曲子喜不喜欢", at_ms=followup_ms)
    assert bench.has_active_tangent(now_ms=clock_ms + TANGENT_TTL_MS + 10_000) is True

    # 达到 90s TTL 上界（elapsed == TANGENT_TTL_MS）时自动失效清空
    assert bench.has_active_tangent(now_ms=followup_ms + TANGENT_TTL_MS - 1) is True
    assert bench.has_active_tangent(now_ms=followup_ms + TANGENT_TTL_MS) is False
    assert bench.render_play_window(now_ms=followup_ms + TANGENT_TTL_MS, has_viewer_batch=False) == ""

    # 无追问时消费 1 个主动轮（无论开口还是沉默）即清空
    bench.start_viewer_tangent("柑橘汽水", "阿杰问冰镇汽水甜度", at_ms=200_000)
    bench.consume_viewer_tangent_round()
    assert bench.has_active_tangent(now_ms=205_000) is False

    # urgent_game=True 立即清空余温并返回空串
    bench.start_viewer_tangent("柑橘汽水", "阿杰问冰镇汽水甜度", at_ms=300_000)
    assert bench.render_play_window(now_ms=301_000, has_viewer_batch=False, urgent_game=True) == ""
    assert bench.has_active_tangent(now_ms=301_000) is False

    # 切换流程单环节时同步清空余温
    bench.start_viewer_tangent("柑橘汽水", "阿杰问冰镇汽水甜度", at_ms=400_000)
    bench.sync_segment("closing", ["收尾道别"])
    assert bench.has_active_tangent(now_ms=401_000) is False
