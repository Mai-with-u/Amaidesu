"""野生敌对妖精（touhou_little_maid:fairy）译名区分野生敌对属性的回归测试。"""

from __future__ import annotations

import pytest

from src.agents.minecraft.attention_matrix import disambiguate_mob_names, summarize, without_coordinates
from src.agents.minecraft.glance import described_entity, entity_label, glance_brief, glance_scene, kind_of
from src.agents.minecraft.maicraft import MaicraftReply
from src.agents.minecraft.notice_router import body_notice_content, route_body_event


def test_glance_distinguishes_hostile_fairy_from_friendly_maid_and_other_mod_fairy() -> None:
    """速览载荷与一句话现场将 touhou_little_maid:fairy 标为野生敌对妖精，与己方女仆及他模组妖精明确区分。"""
    scene = {
        "entities": [
            {
                "id": "e1",
                "type": "touhou_little_maid:fairy",
                "direction": "前方",
                "distance": 5,
                "hostile": True,
            },
            {
                "id": "e2",
                "type": "touhou_little_maid:maid",
                "direction": "后方",
                "distance": 3,
                "hostile": False,
                "traits": {"驯服": "你的"},
            },
            {
                "id": "e3",
                "type": "other_mod:fairy",
                "direction": "右侧",
                "distance": 6,
                "hostile": False,
            },
        ],
        "distant_entities": {
            "touhou_little_maid:fairy": 4,
            "minecraft:sheep": 2,
        },
        "mod_things": {
            "maid": [{"id": "e2", "distance": 3, "skin_name": "红魔馆样式"}],
        },
    }

    view = glance_scene(MaicraftReply(ok=True, data=scene))
    by_type = {item["type"]: item for item in view["entities"]}

    # 野生敌对妖精显式标注 kind，不带"女仆"歧义词
    assert by_type["touhou_little_maid:fairy"]["kind"] == "野生敌对妖精"
    assert kind_of(by_type["touhou_little_maid:fairy"]) == "野生敌对妖精"
    assert entity_label(by_type["touhou_little_maid:fairy"]) == "野生敌对妖精"

    # 己方女仆仍保留"女仆"，互不混淆
    assert by_type["touhou_little_maid:maid"]["kind"] == "女仆"
    assert entity_label(by_type["touhou_little_maid:maid"]) == "你的女仆（红魔馆样式皮肤）"

    # 其他模组的同名 fairy 不被误伤
    assert "kind" not in by_type["other_mod:fairy"]
    assert kind_of(by_type["other_mod:fairy"]) == "other_mod:fairy"

    # 远处计数通道过滤野生敌对妖精，保留无害绵羊
    assert view["distant_entities"] == ["minecraft:sheep ×2"]

    brief = glance_brief(view)
    assert "身边有野生敌对妖精在前方，是敌对的" in brief
    assert "你的女仆（红魔馆样式皮肤）在身边" in brief
    assert "女仆妖精" not in brief


def test_described_entity_sets_hostile_fairy_kind_directly() -> None:
    """described_entity 对 touhou_little_maid:fairy 补全野生敌对妖精种类标识。"""
    entry = described_entity(
        {"id": "e10", "type": "touhou_little_maid:fairy", "distance": 7, "hostile": True},
        {},
    )
    assert entry["kind"] == "野生敌对妖精"


@pytest.mark.parametrize(
    ("kind", "source", "raw_message", "expected_in", "not_expected"),
    [
        (
            "attacked",
            "hurt",
            "被女仆妖精（e12）用魔法伤到 2 下，掉了 4 点血，还剩 14 点",
            "被野生敌对妖精用魔法伤到好几下",
            "女仆妖精",
        ),
        (
            "died",
            "character_died",
            "角色死了（PlayerA被女仆妖精杀死了），等重生",
            "被野生敌对妖精杀死了",
            "女仆妖精",
        ),
        (
            "attacked",
            "hurt",
            "被touhou_little_maid:fairy打了 1 下，掉了 2 点血，还剩 18 点",
            "被野生敌对妖精打了一下",
            "touhou_little_maid:fairy",
        ),
        # 己方女仆相关措辞绝不误伤
        (
            "follow_status",
            "follow_status",
            "跟着女仆往前走",
            "跟着女仆往前走",
            "野生敌对妖精",
        ),
    ],
    ids=["hurt-zh-name", "died-zh-name", "hurt-raw-id", "friendly-maid-untouched"],
)
def test_attention_matrix_disambiguates_fairy_in_body_events(
    kind: str,
    source: str,
    raw_message: str,
    expected_in: str,
    not_expected: str,
) -> None:
    """身体受击与死亡事件里的车万女仆语言包译名「女仆妖精」与原始 ID 统一替换为「野生敌对妖精」。"""
    text = summarize(kind, source, raw_message)
    assert expected_in in text
    assert not_expected not in text


def test_without_coordinates_and_body_notice_disambiguate_hostile_fairy() -> None:
    """进展结论与注入给游戏 Agent 的死亡身体通知均区分野生敌对妖精与己方女仆。"""
    milestone = without_coordinates("击败了女仆妖精（e5）（10, 64, -2）")
    assert milestone == "击败了野生敌对妖精"
    assert disambiguate_mob_names("女仆正在帮忙，女仆妖精在旁边发射弹幕") == "女仆正在帮忙，野生敌对妖精在旁边发射弹幕"

    route = route_body_event("character_died")
    notice = body_notice_content(route, "角色死了（PlayerA被女仆妖精杀死了）", history=[], queue=[])
    assert "野生敌对妖精" in notice
    assert "女仆妖精" not in notice
