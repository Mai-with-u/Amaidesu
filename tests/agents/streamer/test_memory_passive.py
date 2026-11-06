"""被动想起的候选判定：老梗栏怎么拆成触发词、地点"到达"怎么算。"""

from __future__ import annotations

from src.agents.streamer.memory.passive import (
    ARRIVE_WITHIN,
    LEAVE_BEYOND,
    SITUATION_STALE_MS,
    PlaceArrivals,
    parse_inside_jokes,
)
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation, NearbyPlace

_SAVES = {"minecraft": GameSave(id="b", name="新档")}


def _at(*places: tuple[str, int], age_ms: int = 0) -> dict[str, GameSituation]:
    return {
        "minecraft": GameSituation(
            places=tuple(NearbyPlace(name=name, distance=distance) for name, distance in places), age_ms=age_ms
        )
    }


def test_inside_jokes_split_into_main_word_and_aliases() -> None:
    """冒号前用 / 分开的是梗与别名，冒号后的说明不当触发词；没有冒号的整行当主词。"""
    jokes = parse_inside_jokes(
        "倒立洗头/洗头挑战：她说过输了就倒立洗头\n\n- 打不过就加入\n倒立洗头：重复的一行不覆盖前面的\n团子:女仆的名字"
    )
    assert jokes == {
        "倒立洗头": ["倒立洗头", "洗头挑战"],
        "打不过就加入": ["打不过就加入"],
        "团子": ["团子"],
    }
    assert parse_inside_jokes("") == {}


def test_arrival_needs_coming_in_from_far_and_counts_once_per_session() -> None:
    """从 48 格外（或这场没读到过）进到 24 格内才算到达；同一个地点一场只算一次。"""
    arrivals = PlaceArrivals()

    assert arrivals.update(_at(("营地", 10)), _SAVES) == [("minecraft", "营地", 10)], "这场第一次读到就在跟前"
    assert arrivals.update(_at(("营地", 60)), _SAVES) == []
    assert arrivals.update(_at(("营地", 5)), _SAVES) == [], "营地这场已经到过"

    # 中间那一圈（24 到 48 格）走进来不算：要先离开到 48 格外
    assert arrivals.update(_at(("北边农场", ARRIVE_WITHIN + 6)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", ARRIVE_WITHIN)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", LEAVE_BEYOND + 1)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", 3)), _SAVES) == [("minecraft", "北边农场", 3)]

    arrivals.reset()
    assert arrivals.update(_at(("营地", 10)), _SAVES) == [("minecraft", "营地", 10)], "换场后重新算"


def test_arrival_ignores_stale_positions_and_tells_saves_apart() -> None:
    """位置读到太久的这一批不判；换了存档，同名地点是另一个地方。"""
    arrivals = PlaceArrivals()

    assert arrivals.update(_at(("营地", 10), age_ms=SITUATION_STALE_MS + 1), _SAVES) == []
    assert arrivals.update(_at(("营地", 10)), _SAVES) == [("minecraft", "营地", 10)]

    other_save = {"minecraft": GameSave(id="c", name="三号档")}
    assert arrivals.update(_at(("营地", 10)), other_save) == [("minecraft", "营地", 10)]
