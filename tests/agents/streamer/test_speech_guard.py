"""台词出口守卫模块：两检整体取用与敏感词净化单取用的组合语义。"""

from __future__ import annotations

import pytest

from src.agents.streamer.speech_guard import (
    WordFilter,
    apply_word_filter,
    find_verbatim_repeat,
    fit_to_budget,
    guard,
    strip_memory_numbers,
)


def test_apply_word_filter_replaces_and_drops() -> None:
    flt = WordFilter(words=["脏话"], replacement="***")
    assert apply_word_filter("这是脏话测试", flt) == "这是***测试"
    strict = WordFilter(words=["脏话"], drop_on_match=True)
    assert apply_word_filter("这是脏话测试", strict) is None
    assert apply_word_filter("干净台词", strict) == "干净台词"
    assert apply_word_filter("原样放行", None) == "原样放行"


def test_guard_passes_clean_text_through_all_checks() -> None:
    verdict = guard("今天天气不错呀", speech_max_chars=40, word_filter=None)
    assert verdict.text == "今天天气不错呀" and verdict.reason == "passed"


def test_guard_drops_on_sensitive_word_with_drop_on_match() -> None:
    flt = WordFilter(words=["脏话"], drop_on_match=True)
    verdict = guard("说了脏话的一句", speech_max_chars=40, word_filter=flt)
    assert verdict.text is None and verdict.reason == "word_filter"


def test_guard_truncates_to_budget_before_other_checks() -> None:
    long_text = "第一句完整的话。第二句完整的话。第三句完整的话。"
    assert len(fit_to_budget(long_text, 12)) <= 12
    verdict = guard(long_text, speech_max_chars=12, word_filter=None)
    assert verdict.text == fit_to_budget(long_text, 12)
    # 预算为 0 = 不截短
    assert guard(long_text, speech_max_chars=0, word_filter=None).text == long_text


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


@pytest.mark.parametrize(
    ("speech", "earlier", "should_hit"),
    [
        # 第 63 场 23:44:08 真实逐字复读：隔 157 秒原样重讲 30 字
        (
            "嘴上说不坐船，手倒是很诚实，橡木船都快拼一半了，我这不是打自己脸嘛。",
            ["嘴上说不坐船，手倒是很诚实，橡木船都快拼一半了，我这不是打自己脸嘛。"],
            True,
        ),
        # 连着讲几句成一段时，其中夹了一句已说过的长句：按句抓出，不被新句稀释放跑
        (
            "先把工作台收起来。嘴上说不坐船，手倒是很诚实，橡木船都快拼一半了！",
            ["嘴上说不坐船，手倒是很诚实，橡木船都快拼一半了，我这不是打自己脸嘛。"],
            True,
        ),
        # 更早说的是一整段多句话，新台词把其中一句原样又说了一遍：同样抓出
        (
            "嘴上说不坐船，手倒是很诚实，橡木船都快拼一半了。",
            ["前面先垫块石头。嘴上说不坐船，手倒是很诚实，橡木船都快拼一半了。回头再找羊。"],
            True,
        ),
        # 同一段里的相邻承接句顶真（"水车立好了，水车立好之后再去接传动杆"）：同段内不互判复读
        (
            "水车总算立好了！水车立好之后再去接旁边的传动杆，应该就不会过载了。",
            ["刚才背包塞满了，我先清两个格子出来。"],
            False,
        ),
        # 短句天然重撞（第 61 场"真是哭笑不得"6 个字）：不到最短长度，不拦
        (
            "骨粉撒草地上就能催花嘛，真是哭笑不得。",
            ["找了一整夜的蒲公英，真是哭笑不得。"],
            False,
        ),
        # 长英文玩家名（第 61 场"谢啦玩家名"）：英文词整体算一个字，不会因字母数多而虚高重合度误拦
        (
            "谢啦 PlayerAlphaBravo！我这手滑得也太离谱了，锅一脱手全撒脚边了。",
            ["锅架火上啦，PlayerAlphaBravo 那几颗马铃薯，我先下锅！"],
            False,
        ),
        # 只是共用了短语的正常推进（第 65 场最高非复读 0.67）：远低于 0.9 阈值，放行
        (
            "怪不得我找半天，原来是重新生成过地形。西边那几棵橡树还在吧？",
            ["树苗满世界找，自家西边那几棵橡树叶子不就是现成的嘛！"],
            False,
        ),
    ],
)
def test_find_verbatim_repeat_catches_near_verbatim_without_false_positives(
    speech: str, earlier: list[str], should_hit: bool
) -> None:
    hit = find_verbatim_repeat(speech, earlier)
    assert (hit is not None) is should_hit
    if hit is not None:
        assert hit.ratio >= 0.9
