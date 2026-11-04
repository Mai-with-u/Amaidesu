"""记忆挂存档：下播整理按台词时刻认一句话是在哪个存档里说的。"""

from __future__ import annotations

from typing import Dict, Optional

import pytest

from src.agents.streamer.memory.saves import SavePlace, SaveTimeline, place_note, place_of, session_note
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


_IN_B = {"minecraft": GameSave(id="b", name="新档")}
_NOT_IN_WORLD = {"minecraft": GameSave()}


@pytest.mark.parametrize(
    ("game", "save_id", "current", "expected"),
    [
        (None, "", _IN_B, SavePlace.UNSPLIT),  # 没挂游戏
        ("minecraft", "a", {}, SavePlace.UNSPLIT),  # 游戏报不出存档（旧版、没启用）：照旧
        ("text_adv", "a", _IN_B, SavePlace.UNSPLIT),  # 别的游戏不分存档
        ("minecraft", "b", _IN_B, SavePlace.SAME),
        ("minecraft", "a", _IN_B, SavePlace.OTHER),
        ("minecraft", "", _IN_B, SavePlace.UNSURE),  # 旧数据、当时没认出
        ("minecraft", "a", _NOT_IN_WORLD, SavePlace.OTHER),  # 还没进存档：记着存档的一律算别的存档
        ("minecraft", "", _NOT_IN_WORLD, SavePlace.UNSURE),
    ],
)
def test_place_of_follows_the_table(
    game: Optional[str], save_id: str, current: Dict[str, GameSave], expected: SavePlace
) -> None:
    assert place_of(game, save_id, current) is expected


def test_notes_say_which_save_and_whether_it_is_here() -> None:
    assert place_note("minecraft", "b", "新档", _IN_B) == "在 minecraft「新档」里，就是眼下这个存档"
    assert place_note("minecraft", "a", "老档", _IN_B) == "在 minecraft「老档」里，不是眼下这个存档"
    assert place_note("minecraft", "", "", _IN_B) == "在 minecraft 里，不确定是哪个存档"
    assert place_note(None, "", "", _IN_B) == ""

    only_old = [_span("a", "老档", 1_000, 1_500)]
    assert session_note(only_old, _IN_B) == "那场 minecraft 在「老档」里，不是眼下这个存档"
    # 那场也进过眼下这个存档、没记下存档、游戏眼下不分存档：都不加注
    assert session_note([*only_old, _span("b", "新档", 1_600, 1_700)], _IN_B) == ""
    assert session_note([], _IN_B) == ""
    assert session_note(only_old, {}) == ""
