"""现场卡：嘴开口前读的事实快照——目标与现状分开、按秒标时间、超长时按优先级取舍。"""

from __future__ import annotations

from src.agents.streamer.scene_card import GoalLine, SceneInputs, brief_of, build_scene_card

NOW = 1_800_000_000_000


def _inputs(**overrides: object) -> SceneInputs:
    base: dict = {
        "now_ms": NOW,
        "trigger": "刚才游戏里：被苦力怕炸到一下，血掉了不少",
        "scene_brief": "在主世界的平原。白天，晴。血掉了不少，不饿。身边有苦力怕在左后方，是敌对的。",
        "scene_brief_ms": NOW - 12_000,
        "goals": (GoalLine(game="minecraft", instruction="回到基地家中", started_ms=NOW - 180_000),),
        "recent_events": ((NOW - 8_000, "被苦力怕炸到一下，血掉了不少"), (NOW - 70_000, "天色变了：天亮了")),
        "segment_title": "互动闲聊",
        "room_activity": "近 1 分钟弹幕 0 条（热度 low）",
        "notebook": "床的名字定了：苦力怕拆迁床",
        "recent_speeches": ("走走走，回家！", "天总算亮了。"),
    }
    base.update(overrides)
    return SceneInputs(**base)


def test_card_reads_in_order_and_marks_goals_as_plans() -> None:
    """段落按阅读顺序排；手上的目标明写"是打算不是现状"；时间按秒说，一分钟外按分钟说。"""
    card = build_scene_card(_inputs())
    titles = [line.split("】", 1)[0] + "】" for line in card.split("\n") if line.startswith("【")]
    assert titles == [
        "【现在的状态】",
        "【手上的目标】",
        "【刚发生的事】",
        "【直播间】",
        "【悬着的事】",
        "【你刚说过】",
        "【这次的由头】",
    ]
    assert "【现在的状态】（12 秒前看的）" in card
    # 实验里嘴把"退回屋内关好门"这条指令说成了已经发生的事
    assert "（这是打算，不一定已经做到；现状以上面和下面为准）" in card
    assert "- 3 分钟前在 minecraft 里开始：回到基地家中（进行中）" in card
    assert "- 刚刚 被苦力怕炸到一下，血掉了不少" in card and "- 1 分钟前 天色变了：天亮了" in card
    assert "【你刚说过】（别再说一遍）\n- 走走走，回家！\n- 天总算亮了。" in card


def test_finished_goal_and_stale_scene_are_marked() -> None:
    """做完、卡住的目标带上是什么时候知道的；现场信息过了一分钟提醒可能已经变了。"""
    card = build_scene_card(
        _inputs(
            scene_brief_ms=NOW - 90_000,
            goals=(
                GoalLine(
                    game="", instruction="采白色羊毛", started_ms=NOW - 600_000, status="卡住了", status_ms=NOW - 20_000
                ),
            ),
        )
    )
    assert "【现在的状态】（1 分钟前看的，可能已经变了）" in card
    assert "- 10 分钟前开始：采白色羊毛（20 秒前卡住了）" in card


def test_empty_sections_are_left_out() -> None:
    """没有的事不写段落标题：只有由头时卡里就只有由头。"""
    card = build_scene_card(SceneInputs(now_ms=NOW, trigger="弹幕：麦麦晚上好"))
    assert card == "【这次的由头】弹幕：麦麦晚上好"


def test_long_card_drops_low_priority_sections_first() -> None:
    """超长时先减事件与台词条数，再丢悬着的事、直播间；现状、目标与由头一直保留。"""
    events = tuple((NOW - i * 5_000, f"第{i}件事" + "很长的描述" * 6) for i in range(10, 0, -1))
    speeches = tuple(f"说过的第{i}句话" + "啰嗦" * 10 for i in range(8))
    card = build_scene_card(
        _inputs(recent_events=events, recent_speeches=speeches, notebook="备忘" * 80), max_chars=500
    )
    assert len(card) <= 500
    assert "【现在的状态】" in card and "【手上的目标】" in card and "【这次的由头】" in card
    assert "【悬着的事】" not in card


def test_brief_of_reads_only_the_brief_field() -> None:
    """参考工具结果只取 brief 一句；没有就是空串，不把结构化原文塞进卡。"""
    assert brief_of({"tool": "glance", "brief": " 在主世界的森林。 ", "body": {"health": 20}}) == "在主世界的森林。"
    assert brief_of({"tool": "glance", "body": {}}) == ""
    assert brief_of("not a dict") == ""
