"""别的玩家冲角色做的肢体动作：Mod 那句话改成给主播的一句话。

覆盖：
- 分号后面的方位、观察编号、格数去掉，动作和送的东西照说，"你"换成"我"
- 一分钟合成的那句（没有分号）整句都是动作
- 被拍一下照说"拍了我一下"，不讲成挨打；口径写明俏皮接、不提血量
- 原话缺了也有一句能接的话
"""

from __future__ import annotations

import pytest

from src.agents.minecraft.player_body_language import spoken_fact, spoken_line


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "玩家 Alex（e4）看着你蹲起 4 下，拿出铁剑，对着你挥了 2 下；他在你前方 3 格",
            "玩家 Alex 看着我蹲起 4 下，拿出铁剑，对着我挥了 2 下",
        ),
        (
            "玩家 Alex（e4）朝你扔了 3 个面包（e9），落在你左侧 1 格；他在你左前方 2 格",
            "玩家 Alex 朝我扔了 3 个面包，落在我左侧",
        ),
        (
            "玩家 Alex（e4）看着你蹲起 2 下，往北走了 6 格，又回头看着你；他在你前方 7 格",
            "玩家 Alex 看着我蹲起 2 下，往北走了，又回头看着我",
        ),
        ("玩家 Steve（e2）拍了你一下：空手，没伤到；他在你身后 1 格，看着你", "玩家 Steve 拍了我一下：空手，没伤到"),
        ("玩家 Alex（e4）这一分钟又蹲起 40 下、拍你 12 下", "玩家 Alex 这一分钟又蹲起 40 下、拍我 12 下"),
        ("玩家 Alex 对着橡木原木（b5）挥了 3 下；他在你右侧 4 格", "玩家 Alex 对着橡木原木挥了 3 下"),
        # 挥向方块时 Mod 把方位写在括号里：格数去掉，括号开头的"你"也换成"我"
        (
            "玩家 Alex（e4）对着石头（你左前方 2 格）挥了 3 下；他在你前方 4 格",
            "玩家 Alex 对着石头（我左前方）挥了 3 下",
        ),
    ],
)
def test_spoken_fact_keeps_the_gestures_and_drops_where_he_stands(message: str, expected: str) -> None:
    assert spoken_fact(message) == expected


def test_spoken_line_tells_the_streamer_it_is_play_not_an_attack() -> None:
    """拍一下在主播嘴里是"拍了我一下"，口径说清这是在跟她玩：俏皮接一句，不提血量。"""
    line = spoken_line("玩家 Steve（e2）拍了你一下：空手，没伤到；他在你身后 1 格")
    assert line.startswith("玩家 Steve 拍了我一下：空手，没伤到。")
    assert "不是打我" in line and "俏皮" in line and "不提血量" in line
    assert "格" not in line.split("。", 1)[0], "方位距离不进主播的话"


def test_spoken_line_without_the_mod_message_still_has_something_to_say() -> None:
    assert spoken_line("").startswith("有玩家冲我比划了几下。")
