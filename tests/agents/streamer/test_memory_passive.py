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
    """从 48 格外进到 24 格内才算到达；之前在哪不清楚的第一次读数只记下来；同一个地点一场只算一次。"""
    arrivals = PlaceArrivals()

    assert arrivals.update(_at(("营地", 10)), _SAVES) == [], "开播第一次读到就在跟前：之前在哪不清楚，不算到达"
    assert arrivals.update(_at(("营地", 60)), _SAVES) == []
    assert arrivals.update(_at(("营地", 5)), _SAVES) == [("minecraft", "营地", 5)], "真的从 48 格外走回来"
    assert arrivals.update(_at(("营地", 60)), _SAVES) == []
    assert arrivals.update(_at(("营地", 5)), _SAVES) == [], "营地这场已经到过"

    # 中间那一圈（24 到 48 格）走进来不算：要先离开到 48 格外
    assert arrivals.update(_at(("北边农场", ARRIVE_WITHIN + 6)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", ARRIVE_WITHIN)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", LEAVE_BEYOND + 1)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", 3)), _SAVES) == [("minecraft", "北边农场", 3)]

    arrivals.reset()
    assert arrivals.update(_at(("营地", 60)), _SAVES) == []
    assert arrivals.update(_at(("营地", 10)), _SAVES) == [("minecraft", "营地", 10)], "换场后重新算"


def test_unlisted_places_count_as_far_only_when_the_whole_list_was_far() -> None:
    """读数只列最近的几个：上一次没列出的地点，那次列到的最远一个也在 48 格外，才算它当时在 48 格外。"""
    arrivals = PlaceArrivals()
    assert arrivals.update(_at(("营地", 30), ("矿洞口", 40)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", 10)), _SAVES) == [], "上一次列到的最远才 40 格，农场当时在哪说不准"

    arrivals.reset()
    assert arrivals.update(_at(("营地", 100)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", 10)), _SAVES) == [("minecraft", "北边农场", 10)]

    arrivals.reset()
    assert arrivals.update(_at(), _SAVES) == []
    assert arrivals.update(_at(("北边农场", 10)), _SAVES) == [], "上一次一个地点都没列（如在别的维度），不算走进来"


def test_arrival_ignores_stale_positions_and_tells_saves_apart() -> None:
    """位置读到太久的这一批不判；换了存档，同名地点是另一个地方。"""
    arrivals = PlaceArrivals()

    assert arrivals.update(_at(("营地", 60), age_ms=SITUATION_STALE_MS + 1), _SAVES) == []
    assert arrivals.update(_at(("营地", 10)), _SAVES) == [], "太旧的那次不算读到过"
    assert arrivals.update(_at(("北边农场", 60)), _SAVES) == []
    assert arrivals.update(_at(("北边农场", 60), age_ms=SITUATION_STALE_MS + 1), _SAVES) == []
    assert arrivals.update(_at(("北边农场", 10)), _SAVES) == [], "中间位置太旧：之前记下的远处作废，不算走进来"
    assert arrivals.update(_at(("营地", 60)), _SAVES) == []
    assert arrivals.update(_at(("营地", 10)), _SAVES) == [("minecraft", "营地", 10)]

    other_save = {"minecraft": GameSave(id="c", name="三号档")}
    assert arrivals.update(_at(("营地", 10)), other_save) == [], "换存档后第一次读到：只记下来"
    assert arrivals.update(_at(("营地", 60)), other_save) == []
    assert arrivals.update(_at(("营地", 10)), other_save) == [("minecraft", "营地", 10)], "同名地点是另一个地方"
