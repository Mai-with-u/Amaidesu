"""世界设施短目录构造器（known_places.py）单元测试。"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from src.agents.minecraft.known_places import (
    MAX_KNOWN_PLACES,
    build_known_places_catalog,
    is_anonymous_archive_name,
    short_dimension_name,
)


def _pos(dimension: str = "minecraft:overworld", x: int = 10, y: int = 64, z: int = -8) -> Dict[str, Any]:
    return {"x": x, "y": y, "z": z, "dimension": dimension}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", True),
        ("   ", True),
        ("建筑", True),
        ("机器", True),
        ("建筑@12,64,-8", True),
        ("机器@-100,70,25", True),
        (" 建筑@0,64,0 ", True),
        ("木屋", False),
        ("地狱门（主世界侧）", False),
        ("建筑工坊", False),
        ("机器车间", False),
    ],
)
def test_is_anonymous_archive_name(name: str, expected: bool) -> None:
    """客户端防御性清洗仅拦空白名与明确的自动无名档模式，不误伤正常中文设施名。"""
    assert is_anonymous_archive_name(name) is expected


@pytest.mark.parametrize(
    ("raw_dim", "expected"),
    [
        ("minecraft:overworld", "overworld"),
        ("minecraft:the_nether", "the_nether"),
        ("minecraft:the_end", "the_end"),
        ("twilightforest:twilight_forest", "twilightforest:twilight_forest"),
        ("overworld", "overworld"),
        ("", ""),
    ],
)
def test_short_dimension_name(raw_dim: str, expected: str) -> None:
    """原版维度剥离 minecraft: 前缀，模组自定义维度原样保留。"""
    assert short_dimension_name(raw_dim) == expected


def test_build_known_places_catalog_filters_anonymous_and_strips_coordinates() -> None:
    """过滤匿名建筑/机器档与重复项，仅保留名字与分类，不携带坐标、距离或施工细节。"""
    raw = {
        "places": [
            {"name": "家", "kind": "place", "position": _pos(), "distance": 3},
            {"name": "地狱门（主世界侧）", "kind": "place", "position": _pos(x=20, z=40), "distance": 45},
            {"name": "家", "kind": "place", "position": _pos(x=11), "distance": 4},
            {"name": "   ", "kind": "place", "position": _pos()},
            {"name": "建筑", "kind": "building", "position": _pos()},
            {"name": "建筑@12,64,-8", "kind": "building", "position": _pos()},
            {"name": "机器", "kind": "machine", "position": _pos()},
            {"name": "机器@-4,70,9", "kind": "machine", "position": _pos()},
            {"name": "动力压板机", "kind": "machine", "position": _pos(), "distance": 8},
            {"name": "北岸小屋", "kind": "building", "position": _pos(), "distance": 15},
            # 跨维度：保留 place / machine，排除 building
            {"name": "地狱门（下界侧）", "kind": "place", "position": _pos("minecraft:the_nether", x=2, z=5)},
            {"name": "下界烈焰人机", "kind": "machine", "position": _pos("minecraft:the_nether", x=30, z=10)},
            {"name": "下界临时屋", "kind": "building", "position": _pos("minecraft:the_nether", x=4, z=6)},
        ],
        "stations": [
            {
                "name": "自动冶炼站",
                "via": "smelting",
                "input": _pos(x=5, z=6),
                "output": _pos(x=7, z=6),
                "exclude": ["minecraft:beef"],
                "distance": 6,
            },
            {
                "name": "末地分拣站",
                "via": "sorting",
                "input": _pos("minecraft:the_end", x=0, z=0),
                "output": _pos("minecraft:the_end", x=2, z=0),
                "exclude": [],
            },
        ],
        "regions": [
            {"name": "PlayerA营地", "center": _pos(x=0, z=0), "radius": 32},
            {"name": "下界枢纽区", "center": _pos("minecraft:the_nether", x=0, z=0), "radius": 16},
        ],
        "record_counts": {"container": 99},
    }

    catalog = build_known_places_catalog(raw, current_dimension="minecraft:overworld")

    assert catalog == {
        "places": ["家", "地狱门（主世界侧）"],
        "stations": ["自动冶炼站"],
        "regions": ["PlayerA营地"],
        "machines": ["动力压板机"],
        "buildings": ["北岸小屋"],
        "other_dimensions": [
            "地狱门（下界侧）(the_nether)",
            "末地分拣站(the_end)",
            "下界枢纽区(the_nether)",
            "下界烈焰人机(the_nether)",
        ],
    }
    assert "omitted_count" not in catalog
    serialized = str(catalog)
    assert "建筑@" not in serialized
    assert "机器@" not in serialized
    assert "下界临时屋" not in serialized
    assert "distance" not in serialized
    assert "radius" not in serialized
    assert "exclude" not in serialized


def test_build_known_places_catalog_enforces_priority_and_24_cap() -> None:
    """超过 24 条上限时按 places > stations > regions > machines > buildings > other_dimensions 装入并附 omitted_count。"""
    raw = {
        "places": [
            *[{"name": f"地标{i}", "kind": "place", "position": _pos()} for i in range(1, 11)],
            *[{"name": f"机器{i}", "kind": "machine", "position": _pos()} for i in range(1, 6)],
            *[{"name": f"建筑{i}", "kind": "building", "position": _pos()} for i in range(1, 6)],
            {"name": "下界传送门", "kind": "place", "position": _pos("minecraft:the_nether")},
            # 跨维度建筑不算可展示候选，不应计入 omitted_count
            {"name": "下界旧房", "kind": "building", "position": _pos("minecraft:the_nether")},
            # 同类重复名去重后也不应抬高 omitted_count
            {"name": "地标1", "kind": "place", "position": _pos()},
        ],
        "stations": [{"name": f"加工站{i}", "input": _pos()} for i in range(1, 6)],
        "regions": [{"name": f"地盘{i}", "center": _pos()} for i in range(1, 6)],
    }

    catalog = build_known_places_catalog(raw, current_dimension="minecraft:overworld")

    assert MAX_KNOWN_PLACES == 24
    # 10 places + 5 stations + 5 regions + 4 machines = 24，剩余 1 machine + 5 buildings + 1 other_dim = 7
    assert len(catalog["places"]) == 10
    assert len(catalog["stations"]) == 5
    assert len(catalog["regions"]) == 5
    assert catalog["machines"] == ["机器1", "机器2", "机器3", "机器4"]
    assert "buildings" not in catalog
    assert "other_dimensions" not in catalog
    assert catalog["omitted_count"] == 7


def test_build_known_places_catalog_omits_empty_keys_and_returns_empty_dict_when_all_filtered() -> None:
    """无有效设施或全部被匿名规则过滤时返回空字典 {}，零上下文占用。"""
    assert build_known_places_catalog({}, current_dimension="minecraft:overworld") == {}
    assert (
        build_known_places_catalog(
            {
                "places": [
                    {"name": "建筑", "kind": "building", "position": _pos()},
                    {"name": "建筑@1,64,2", "kind": "building", "position": _pos()},
                    {"name": "机器@3,64,4", "kind": "machine", "position": _pos()},
                    {"name": "   ", "kind": "place", "position": _pos()},
                    {"name": "下界棚子", "kind": "building", "position": _pos("minecraft:the_nether")},
                ],
                "stations": [],
                "regions": [],
            },
            current_dimension="minecraft:overworld",
        )
        == {}
    )
    # 只有一类非空时仅包含该键，不出现空分类键与 omitted_count
    single = build_known_places_catalog(
        {"places": [{"name": "营地", "kind": "place", "position": _pos()}]},
        current_dimension="minecraft:overworld",
    )
    assert single == {"places": ["营地"]}
