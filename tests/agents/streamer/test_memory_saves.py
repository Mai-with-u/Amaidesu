"""记忆挂存档：下播整理按台词时刻认一句话是在哪个存档里说的。"""

from __future__ import annotations

from src.agents.streamer.memory.saves import SaveTimeline
from src.modules.storage.models.events import GameSaveSpan
from src.modules.types.game_save import GameSave


def _span(save_id: str, name: str, started: int, ended: int, game: str = "minecraft") -> GameSaveSpan:
    return GameSaveSpan(game=game, save_id=save_id, save_name=name, started_ms=started, ended_ms=ended)


def test_line_belongs_to_the_save_entered_most_recently_before_it() -> None:
    """A → B → 回 A：每句话认它说出口之前最近进的那个存档；比所有段都早的认最早那个。"""
    timeline = SaveTimeline(
        [_span("b", "新档", 2_000, 2_500), _span("a", "老档", 1_000, 1_500), _span("a", "老档", 3_000, 3_100)]
    )

    assert timeline.save_at("minecraft", 500) == GameSave(id="a", name="老档")
    assert timeline.save_at("minecraft", 1_800) == GameSave(id="a", name="老档")
    assert timeline.save_at("minecraft", 2_000) == GameSave(id="b", name="新档")
    assert timeline.save_at("minecraft", 2_900) == GameSave(id="b", name="新档")
    assert timeline.save_at("minecraft", 9_000) == GameSave(id="a", name="老档")
    assert timeline.saves_of("minecraft") == [GameSave(id="a", name="老档"), GameSave(id="b", name="新档")]


def test_unknown_when_no_game_or_no_save_recorded() -> None:
    """没挂游戏、这个游戏这一场没记下存档：认不出（空存档），读的时候按不确定处理。"""
    timeline = SaveTimeline([_span("a", "老档", 1_000, 1_500)])

    assert timeline.save_at(None, 1_200) == GameSave()
    assert timeline.save_at("text_adv", 1_200) == GameSave()
    assert SaveTimeline().save_at("minecraft", 1_200) == GameSave()


def test_renamed_save_shows_its_latest_name() -> None:
    """同一存档中途改了名：哪一段里说的话都显示最后报的名字。"""
    timeline = SaveTimeline(
        [_span("a", "旧名", 1_000, 1_100), _span("b", "新档", 1_200, 1_300), _span("a", "新名", 1_400, 1_500)]
    )

    assert timeline.save_at("minecraft", 1_050) == GameSave(id="a", name="新名")
    assert GameSave(id="0123456789abcdef").label == "编号 01234567"
    assert GameSave().label == ""
