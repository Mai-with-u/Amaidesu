"""唠叨指标：同一条提几次、没人问时主动提几次、绕圈、线索卡推了用没用上。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.streamer.memory.side_effects import compute_side_effects
from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.memory import CueLogEntry

_MINUTE = 60_000
_HOUR = 60 * _MINUTE


def _line(
    at_ms: int,
    refs: Optional[str],
    *,
    reply_to: Optional[str] = None,
    reply_player: Optional[int] = None,
    dropped: bool = False,
) -> SettleSpeechLine:
    return SettleSpeechLine(
        id=at_ms,
        timestamp_ms=at_ms,
        content="台词",
        memory_refs=refs,
        reply_to_message_id=reply_to,
        reply_player_id=reply_player,
        dropped_at_ms=at_ms if dropped else None,
    )


def _card(
    at_ms: int,
    kind: str,
    *,
    archive: Optional[int] = None,
    commitment: Optional[int] = None,
    sources: Optional[List[str]] = None,
    used: bool = False,
    self_triggered: bool = False,
    detail: Optional[Dict[str, Any]] = None,
) -> CueLogEntry:
    return CueLogEntry(
        id=at_ms,
        live_session_id=1,
        pushed_at_ms=at_ms,
        card_kind=kind,
        archive_line_id=archive,
        commitment_id=commitment,
        sources=sources or [],
        detail=detail or {},
        self_triggered=self_triggered,
        used_at_ms=at_ms + 1 if used else None,
    )


def test_nothing_named_and_no_cards_means_no_section() -> None:
    assert compute_side_effects([_line(1_000, None), _line(2_000, "[]")], [], duration_ms=_HOUR) is None


def test_counts_mentions_per_item_and_the_most_named_one() -> None:
    """第 58 场的形态：十几条各提一两次。没送达的那句不算她说过。"""
    speech = [
        _line(1 * _MINUTE, '["档#27"]'),
        _line(5 * _MINUTE, '["档#27", "约#5"]'),
        _line(9 * _MINUTE, '["档#30"]'),
        _line(12 * _MINUTE, '["档#30"]', dropped=True),
    ]

    stats = compute_side_effects(speech, [], duration_ms=2 * _HOUR)

    assert stats is not None
    assert (stats.mentions, stats.items, stats.max_per_item, stats.max_item) == (4, 3, 2, "档#27")
    assert stats.p95_per_item == 2


def test_unprompted_excludes_replies_and_items_someone_just_brought_up() -> None:
    """回应观众或玩家的那句、前一分钟有人提到同一条的那句都算有人问；地点卡、她自己触发的卡不算。"""
    speech = [
        _line(10 * _MINUTE, '["档#1"]', reply_to="m1"),
        _line(20 * _MINUTE, '["档#2"]', reply_player=7),
        _line(30 * _MINUTE, '["档#3"]'),
        _line(40 * _MINUTE, '["档#4"]'),
        _line(50 * _MINUTE, '["约#9"]'),
    ]
    cards = [
        _card(30 * _MINUTE - 20_000, "rule", archive=3, sources=["danmaku"]),
        _card(40 * _MINUTE - 20_000, "place", archive=4, sources=["place"]),
        _card(50 * _MINUTE - 2 * _MINUTE, "vector", commitment=9, sources=["danmaku"]),
    ]

    stats = compute_side_effects(speech, cards, duration_ms=2 * _HOUR)

    assert stats is not None
    # 档#4 只有地点卡；约#9 的弹幕在两分钟前，超出一分钟
    assert stats.unprompted == 2
    assert stats.unprompted_per_hour == 1.0


def test_circling_needs_three_mentions_within_half_an_hour() -> None:
    speech = [_line(minute * _MINUTE, '["档#5"]') for minute in (0, 10, 29)] + [
        _line(minute * _MINUTE, '["档#6"]') for minute in (0, 20, 40)
    ]

    stats = compute_side_effects(speech, [], duration_ms=_HOUR)

    assert stats is not None and stats.circling_items == 1


def test_cards_by_kind_used_self_triggered_and_per_player() -> None:
    cards = [
        _card(1_000, "game", commitment=1, sources=["game"], used=True),
        _card(2_000, "game", commitment=2, sources=["game"]),
        _card(3_000, "vector", archive=4, sources=["situation"]),
        _card(4_000, "rule", archive=5, sources=["self"], self_triggered=True),
        _card(5_000, "player", sources=["player"], detail={"player_id": 3}),
        _card(6_000, "player", sources=["player"], detail={"player_id": 3}),
        _card(7_000, "player", sources=["player"], detail={"player_id": 8}),
    ]

    stats = compute_side_effects([], cards, duration_ms=_HOUR)

    assert stats is not None
    assert {kind: (stat.pushed, stat.used) for kind, stat in stats.cards.items()} == {
        "game": (2, 1),
        "vector": (1, 0),
        "rule": (1, 0),
        "player": (3, 0),
    }
    assert stats.self_triggered_cards == 1
    assert stats.max_cards_per_player == 2
    assert stats.mentions == 0 and stats.unprompted_per_hour == 0.0


def test_live_side_effects_script_self_check_passes() -> None:
    """直播库复算脚本（scripts/memory_eval/live_side_effects.py）和整理报告用同一个函数：
    取料口的列名或口径跟不上，补出来的旧场次基线就和报告对不上。"""
    script = Path(__file__).resolve().parents[3] / "scripts" / "memory_eval" / "live_side_effects.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--selftest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-3000:]
