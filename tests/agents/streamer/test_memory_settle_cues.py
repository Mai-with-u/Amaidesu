"""下播整理给档案行写的触发词与地点：模型提名、代码核对，对不上的丢掉。"""

from __future__ import annotations

from src.agents.streamer.memory.settle_cues import (
    MAX_KEYWORDS,
    PLACE_NEAR_WINDOW_MS,
    remembered_places,
    render_places,
    verified_keywords,
    verified_place,
)
from src.modules.storage.models.events import GamePlaceMark


def _mark(kind: str, place: str, at_ms: int, save_id: str = "a") -> GamePlaceMark:
    return GamePlaceMark(game="minecraft", kind=kind, place=place, timestamp_ms=at_ms, save_id=save_id)


def test_keywords_must_appear_in_title_or_quote_or_be_a_homophone_of_three_chars() -> None:
    title, quote = "Minecraft：给小羊取名雪球", "这只羊就叫雪球，倒立洗头的那只"
    assert verified_keywords(["雪球", "到立洗头", "门口那位", "羊", "雪 球", "", 3], title, quote) == [
        "雪球",
        "到立洗头",
    ]
    assert verified_keywords("雪球", title, quote) == [], "不是列表就不收"
    many = ["小羊", "雪球", "倒立", "洗头", "那只"]
    assert len(verified_keywords(many, title, quote)) == MAX_KEYWORDS


def test_remembered_places_skip_ones_forgotten_later() -> None:
    marks = [
        _mark("near", "家", 1),
        _mark("remembered", "矿洞口", 2),
        _mark("left", "家", 3),
        _mark("forgotten", "矿洞口", 4),
    ]
    assert remembered_places(marks) == ["家"]
    assert render_places(["家", "灯塔"]) == "【本场记住的地点】\n家、灯塔"
    assert render_places([]) == "【本场记住的地点】\n没有"


def test_place_by_name_in_the_line_or_by_being_there_when_she_said_it() -> None:
    marks = [_mark("near", "家", 1_000), _mark("left", "家", 50_000), _mark("near", "灯塔", 60_000)]
    places = ["家", "灯塔"]

    def check(raw: str, at_ms: int, texts: tuple[str, ...] = ("随便说说",), save_id: str = "a") -> str:
        return verified_place(raw, places=places, texts=texts, marks=marks, at_ms=at_ms, save_id=save_id)

    assert check("矿洞口", 2_000, ("矿洞口挖到铁了",)) == "", "只能挑本场记住的地点"
    assert check("家", 90_000, ("回家路上",)) == "家", "台词里说到了这个地名"
    assert check("家", 2_000) == "家", "说这句话时就在家跟前"
    assert check("家", 55_000) == "", "已经离开了"
    assert check("灯塔", 60_000 + PLACE_NEAR_WINDOW_MS) == "灯塔"
    assert check("灯塔", 60_001 + PLACE_NEAR_WINDOW_MS) == "", "太久没再记走近，不算还在那里"
    assert check("家", 2_000, save_id="b") == "", "别的存档里的走近不算"
    assert check("", 2_000) == ""
