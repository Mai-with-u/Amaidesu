"""嘴与决策环节对账（mouth_reconcile）：欢迎去重、口头流水与出声前四态裁决。"""

from __future__ import annotations

import pytest

from src.agents.streamer.mouth_reconcile import (
    SpokenEntry,
    SpokenLog,
    extract_player_join_name,
    mentions_name,
    reconcile_with_mouth,
    strip_duplicate_greetings,
)
from src.modules.llm.payload import Response
from tests.modules.llm.fakes import FakeLLMManager


@pytest.mark.parametrize(
    ("chat_line", "expected"),
    [
        ("系统消息：玩家甲加入了游戏", "玩家甲"),
        ("Player_abc加入了游戏", "Player_abc"),
        ("系统消息: Player_abc joined the game.", "Player_abc"),
        ("玩家 玩家甲：我也加入了游戏里的队伍", ""),
    ],
)
def test_extract_player_join_name_matches_only_join_notices(chat_line: str, expected: str) -> None:
    assert extract_player_join_name(chat_line) == expected


@pytest.mark.parametrize(
    ("name", "speech", "expected"),
    [
        ("Player_abc", "诶，Player 也进来了？欢迎欢迎！", True),
        ("观众乙Abc", "欢迎观众乙进来坐～", True),
        ("Player_abc", "今天天气真不错，先去砍树吧。", False),
    ],
)
def test_mentions_name_handles_shortened_nicknames(name: str, speech: str, expected: bool) -> None:
    assert mentions_name(name, speech) is expected


@pytest.mark.parametrize(
    ("speech", "welcomed", "expected_text", "expected_stripped"),
    [
        # 整条只有重复欢迎：剥空后调用方按重复处理不再发声
        ("诶，欢迎玩家甲上线呀，快来一起玩！", {"玩家甲"}, "", True),
        # 前半句顺口重复欢迎、后半句是新的游戏进展：只剥前半句欢迎，保留后半句
        ("欢迎 Player_abc 来啦！这边橡树苗还差两棵没种完。", {"Player_abc"}, "这边橡树苗还差两棵没种完。", True),
        # 正常互动里提到名字但不是欢迎招呼：原样保留不误剥
        ("玩家甲刚才递给我的铁镐还挺顺手的。", {"玩家甲"}, "玩家甲刚才递给我的铁镐还挺顺手的。", False),
    ],
)
def test_strip_duplicate_greetings_preserves_non_greeting_clauses(
    speech: str, welcomed: set[str], expected_text: str, expected_stripped: bool
) -> None:
    assert strip_duplicate_greetings(speech, welcomed) == (expected_text, expected_stripped)


def test_spoken_log_deduplicates_rapid_same_source_and_attaches_trigger() -> None:
    log = SpokenLog()
    log.record("先在岸边站稳看看。", 1_000, source="mouth")
    log.record("先在岸边站稳看看。", 1_050, source="mouth", trigger="换到了新世界")
    log.record("我去前头砍两棵橡树。", 2_000, source="planner")

    mouth_rows = log.mouth_since(900)
    assert len(mouth_rows) == 1
    assert mouth_rows[0].trigger == "换到了新世界"
    assert log.recent_texts() == ["先在岸边站稳看看。", "我去前头砍两棵橡树。"]


async def test_reconcile_skips_llm_when_mouth_was_silent() -> None:
    """常态快路径：本轮嘴没开口时零模型调用放行（FakeLLMManager 设 fail=True 若被调用即暴露）。"""
    llm = FakeLLMManager(fail=True, fail_error="嘴未开口不应调用模型")
    verdict = await reconcile_with_mouth(
        "先把这几块圆石垫在脚下。",
        mouth_entries=[],
        llm_service=llm,
    )
    assert verdict.action == "pass"
    assert verdict.speech == "先把这几块圆石垫在脚下。"
    assert llm.calls == []


@pytest.mark.parametrize(
    ("model_reply", "candidate", "expected_action", "expected_speech"),
    [
        ("放行", "那我顺路去看看前头的村庄。", "pass", "那我顺路去看看前头的村庄。"),
        ("重复", "刚才那只蜘蛛突然扑过来吓我一跳！", "duplicate", ""),
        (
            "保留：顺便把旁边的火把也补上。",
            "蜘蛛扑过来吓死我了！顺便把旁边的火把也补上。",
            "trim",
            "顺便把旁边的火把也补上。",
        ),
        (
            "作废：已经切入新世界站在河岸边，不再卡在门外",
            "怎么还卡在服务器门口进不去呀。",
            "contradicted",
            "",
        ),
    ],
)
async def test_reconcile_parses_four_verdicts_when_mouth_spoke(
    model_reply: str, candidate: str, expected_action: str, expected_speech: str
) -> None:
    llm = FakeLLMManager(responses=[Response(success=True, content=model_reply, request_id="req_rec")])
    mouth_entries = [SpokenEntry(at_ms=1_500, source="mouth", text="诶，换到新世界啦，先在岸边站稳看看周围。")]
    verdict = await reconcile_with_mouth(
        candidate,
        mouth_entries=mouth_entries,
        new_game_lines=["游戏·minecraft·milestone: 已进入新世界出生点河岸"],
        llm_service=llm,
    )
    assert verdict.action == expected_action
    assert verdict.speech == expected_speech
    assert len(llm.calls) == 1
