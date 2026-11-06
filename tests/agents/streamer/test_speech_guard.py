"""台词出口守卫模块：三检整体取用与敏感词净化单取用的组合语义。"""

from __future__ import annotations

import pytest

from src.agents.streamer.speech_guard import WordFilter, apply_word_filter, fit_to_budget, guard, strip_memory_numbers
from src.agents.streamer.topic_gate import SpeechGate


def test_apply_word_filter_replaces_and_drops() -> None:
    flt = WordFilter(words=["脏话"], replacement="***")
    assert apply_word_filter("这是脏话测试", flt) == "这是***测试"
    strict = WordFilter(words=["脏话"], drop_on_match=True)
    assert apply_word_filter("这是脏话测试", strict) is None
    assert apply_word_filter("干净台词", strict) == "干净台词"
    assert apply_word_filter("原样放行", None) == "原样放行"


def test_guard_passes_clean_text_through_all_checks() -> None:
    verdict = guard("今天天气不错呀", ["前一句完全不同的话"], speech_max_chars=40, word_filter=None, gate=SpeechGate())
    assert verdict.text == "今天天气不错呀" and verdict.reason == "passed"


def test_guard_drops_on_sensitive_word_with_drop_on_match() -> None:
    flt = WordFilter(words=["脏话"], drop_on_match=True)
    verdict = guard("说了脏话的一句", [], speech_max_chars=40, word_filter=flt, gate=SpeechGate())
    assert verdict.text is None and verdict.reason == "word_filter"


def test_guard_drops_duplicate_speech() -> None:
    repeated = "这句话和刚才那句几乎一模一样的内容"
    verdict = guard(repeated, [repeated], speech_max_chars=40, word_filter=None, gate=SpeechGate())
    assert verdict.text is None and verdict.reason == "duplicate" and verdict.ratio >= 0.5


def test_guard_truncates_to_budget_before_other_checks() -> None:
    long_text = "第一句完整的话。第二句完整的话。第三句完整的话。"
    assert len(fit_to_budget(long_text, 12)) <= 12
    verdict = guard(long_text, [], speech_max_chars=12, word_filter=None, gate=SpeechGate())
    assert verdict.text == fit_to_budget(long_text, 12)
    # 预算为 0 = 不截短
    assert guard(long_text, [], speech_max_chars=0, word_filter=None, gate=SpeechGate()).text == long_text


@pytest.mark.parametrize(
    ("text", "expected", "count"),
    [
        ("提一下[档#12]倒立洗头那件事", "提一下倒立洗头那件事", 1),
        ("[档#12·上一场] 猜谜输了要倒立洗头", "猜谜输了要倒立洗头", 1),
        ("顺着档#41和约#7往下说", "顺着和往下说", 2),
        ("兑现承诺（约#3）给猫做窝", "兑现承诺给猫做窝", 1),
        ("全角【档＃5】也算", "全角也算", 1),
        ("没有编号的一句话", "没有编号的一句话", 0),
        ("约定好了#1名", "约定好了#1名", 0),  # "约"后面不是紧跟井号，不是编号
    ],
)
def test_strip_memory_numbers_removes_archive_and_commitment_numbers(text: str, expected: str, count: int) -> None:
    assert strip_memory_numbers(text) == (expected, count)
