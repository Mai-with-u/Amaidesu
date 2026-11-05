"""互动叫醒按人节流的簿记：同一个人 20 秒内叫醒一次，攒下的到点合成一批、或随别的叫醒提前交出去。

覆盖：
- 认人：先按 uuid、再按名字，都没有的共用一个桶
- 同一个人 20 秒内第二条攒着；到点合成一批交出去，并从那一刻重新计时
- 两个人各计各的；批次正在跑时不攒
- 别的事叫醒时攒着的全部交出去；收工时清空
"""

from __future__ import annotations

from typing import List

from src.agents.minecraft.interaction_throttle import InteractionThrottle, player_key_of

_ALEX = {"player": {"name": "Alex", "uuid": "00000000-0000-0000-0000-00000000a1ec", "id": "e4"}}


def _throttle() -> tuple[InteractionThrottle, List[float]]:
    now = [100.0]
    return InteractionThrottle(clock=lambda: now[0]), now


def test_player_key_prefers_uuid_then_name_then_a_shared_bucket() -> None:
    assert player_key_of(_ALEX) == "uuid:00000000-0000-0000-0000-00000000a1ec"
    assert player_key_of({"player": {"name": "Steve", "uuid": "", "id": None}}) == "name:Steve"
    assert player_key_of({"player": {"id": "e4"}}) == ""
    assert player_key_of({}) == "" and player_key_of({"player": "Alex"}) == ""


def test_the_same_player_wakes_once_per_window_and_held_ones_come_out_together() -> None:
    """第一条照常交出；20 秒内的攒着；到点合成一批交出，之后再从那一刻计 20 秒。"""
    throttle, now = _throttle()
    assert throttle.offer("alex", "蹲起 4 下", reading_now=False) == ["蹲起 4 下"]
    now[0] += 5
    assert throttle.offer("alex", "蹲起 3 下", reading_now=False) is None
    now[0] += 6
    assert throttle.offer("alex", "拿出铁剑", reading_now=False) is None
    assert throttle.held_count() == 2
    now[0] += 8
    assert throttle.due() == [], "离上次交出去还不到 20 秒"
    now[0] += 1
    assert throttle.due() == [["蹲起 3 下", "拿出铁剑"]]
    assert throttle.held_count() == 0 and throttle.due() == []
    now[0] += 10
    assert throttle.offer("alex", "又蹲起 2 下", reading_now=False) is None, "到点交出那一刻重新计时"


def test_a_new_one_after_the_window_takes_along_what_was_still_held() -> None:
    """到点后还没来得及合成交出，他又来一条：连同攒着的一起交出，不丢。"""
    throttle, now = _throttle()
    throttle.offer("alex", "蹲起 4 下", reading_now=False)
    now[0] += 5
    throttle.offer("alex", "蹲起 3 下", reading_now=False)
    now[0] += 20
    assert throttle.offer("alex", "拍了你一下", reading_now=False) == ["蹲起 3 下", "拍了你一下"]


def test_two_players_are_throttled_separately() -> None:
    throttle, now = _throttle()
    assert throttle.offer("alex", "蹲起 4 下", reading_now=False) == ["蹲起 4 下"]
    now[0] += 5
    assert throttle.offer("steve", "挥手 2 下", reading_now=False) == ["挥手 2 下"]
    assert throttle.offer("alex", "蹲起 3 下", reading_now=False) is None


def test_nothing_is_held_while_a_batch_is_reading() -> None:
    """批次正在跑：通知跟着下一步一起读，不攒，只记下这一刻。"""
    throttle, now = _throttle()
    throttle.offer("alex", "蹲起 4 下", reading_now=False)
    now[0] += 2
    assert throttle.offer("alex", "蹲起 3 下", reading_now=True) == ["蹲起 3 下"]
    now[0] += 19
    assert throttle.offer("alex", "蹲起 2 下", reading_now=False) is None, "读到的那一刻也算交给模型过"


def test_drain_hands_over_everything_held_and_clear_drops_it() -> None:
    throttle, now = _throttle()
    throttle.offer("alex", "蹲起 4 下", reading_now=False)
    throttle.offer("", "有人挥手", reading_now=False)
    now[0] += 3
    throttle.offer("alex", "蹲起 3 下", reading_now=False)
    throttle.offer("", "有人蹲起", reading_now=False)
    assert sorted(throttle.drain()) == [["有人蹲起"], ["蹲起 3 下"]]
    assert throttle.held_count() == 0

    now[0] += 3
    throttle.offer("alex", "蹲起 2 下", reading_now=False)
    throttle.clear()
    assert throttle.held_count() == 0 and throttle.drain() == []
    assert throttle.offer("alex", "蹲起 1 下", reading_now=False) == ["蹲起 1 下"], "收工后计时也作废"
