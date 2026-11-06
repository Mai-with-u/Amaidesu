"""一场的"别唠叨"指标：从本场台词点名的记忆编号和线索卡记录算出来。

记忆系统每加一项"记得住"（召回、推卡），都要有一项"别唠叨、别说错"和它成对：召回调高了，
同一件旧事一场被提几次、没人问时主动提几次、推出去的卡有多少她根本没用上，不能跟着变差。

只有编号和数字，不带台词原文与观众身份：结果写进整理报告，在 WebUI 显示，也供离线脚本在
导出的直播库上复算。阈值先只报告不拦人：前几场测试直播的实测数字出来后再定。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from src.agents.streamer.memory.passive import SOURCE_PLAYER
from src.agents.streamer.memory.render import (
    memory_ref_archive_numbers,
    memory_ref_numbers,
    render_archive_number,
    render_commitment_number,
)
from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.memory import CardKindStat, CueLogEntry, SideEffectStats

#: 点名前多久之内有人提到同一条，就算"有人问起"（观众弹幕或游戏玩家开口命中了那张卡）
PROMPTED_WINDOW_MS = 60_000
#: "围着一件事绕"的窗口与次数：任意 30 分钟里同一条被点名 3 次及以上
CIRCLING_WINDOW_MS = 30 * 60_000
CIRCLING_MIN_MENTIONS = 3
# 线索卡里算"有人提到"的来源：观众弹幕、游戏玩家开口；地点、情境、她自己的台词不算
_SOURCE_DANMAKU = "danmaku"
_PEOPLE_SOURCES = frozenset({_SOURCE_DANMAKU, SOURCE_PLAYER})

ItemKey = Tuple[str, int]


def _speech_items(line: SettleSpeechLine) -> List[str]:
    """一句台词点名的条目编号（档#N 在前、约#N 在后，各自按编号排）。"""
    archive = sorted(memory_ref_archive_numbers(line.memory_refs))
    commitments = sorted(memory_ref_numbers(line.memory_refs))
    return [render_archive_number(n) for n in archive] + [render_commitment_number(n) for n in commitments]


def _card_item(card: CueLogEntry) -> Optional[str]:
    """线索卡指向的条目编号；玩家卡、老梗卡不指向档案行或承诺时为 None。"""
    if card.archive_line_id is not None:
        return render_archive_number(card.archive_line_id)
    if card.commitment_id is not None:
        return render_commitment_number(card.commitment_id)
    return None


def _p95(values: Sequence[int]) -> int:
    """最近秩法的 95 分位：条目少时就是最大值附近，不插值。"""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _circling(times: Sequence[int]) -> bool:
    """同一条的点名时刻里，有没有 3 次落在 30 分钟之内。"""
    ordered = sorted(times)
    span = CIRCLING_MIN_MENTIONS - 1
    return any(ordered[i + span] - ordered[i] <= CIRCLING_WINDOW_MS for i in range(len(ordered) - span))


def compute_side_effects(
    speech: Sequence[SettleSpeechLine],
    cards: Sequence[CueLogEntry],
    *,
    duration_ms: int,
) -> Optional[SideEffectStats]:
    """算一场的唠叨指标；本场既没点名旧事、也没推过卡时返回 None（报告里就不占一节）。

    没送达的台词不算她说过。"没人问时主动提"按两条判：这句不在回应观众弹幕或游戏玩家，
    而且点名前一分钟内没有观众弹幕或玩家开口命中同一条的线索卡。
    """
    delivered = sorted((line for line in speech if line.dropped_at_ms is None), key=lambda line: line.timestamp_ms)
    # 有人提到某一条的时刻：那张卡的来源里有观众弹幕或玩家开口
    asked_at: Dict[str, List[int]] = defaultdict(list)
    for card in cards:
        item = _card_item(card)
        if item is not None and _PEOPLE_SOURCES.intersection(card.sources):
            asked_at[item].append(int(card.pushed_at_ms))

    mention_times: Dict[str, List[int]] = defaultdict(list)
    unprompted = 0
    for line in delivered:
        items = _speech_items(line)
        replying = line.reply_to_message_id is not None or line.reply_player_id is not None
        for item in items:
            mention_times[item].append(int(line.timestamp_ms))
            if replying:
                continue
            recently_asked = any(0 <= line.timestamp_ms - at <= PROMPTED_WINDOW_MS for at in asked_at.get(item, ()))
            if not recently_asked:
                unprompted += 1

    if not mention_times and not cards:
        return None

    counts = Counter({item: len(times) for item, times in mention_times.items()})
    # 并列时取先出现的那条：mention_times 按台词时序插入，most_common 对并列保持插入序
    max_item, max_per_item = counts.most_common(1)[0] if counts else ("", 0)

    by_kind: Dict[str, CardKindStat] = {}
    per_player: Counter[int] = Counter()
    for card in cards:
        stat = by_kind.setdefault(card.card_kind, CardKindStat())
        stat.pushed += 1
        if card.used_at_ms is not None:
            stat.used += 1
        player_id = card.detail.get("player_id") if card.card_kind == "player" else None
        if isinstance(player_id, int):
            per_player[player_id] += 1

    hours = duration_ms / 3_600_000 if duration_ms > 0 else 0.0
    return SideEffectStats(
        mentions=sum(counts.values()),
        items=len(counts),
        max_per_item=max_per_item,
        max_item=max_item,
        p95_per_item=_p95(list(counts.values())),
        unprompted=unprompted,
        unprompted_per_hour=round(unprompted / hours, 2) if hours else 0.0,
        circling_items=sum(1 for times in mention_times.values() if _circling(times)),
        cards=by_kind,
        self_triggered_cards=sum(1 for card in cards if card.self_triggered),
        max_cards_per_player=max(per_player.values(), default=0),
    )
