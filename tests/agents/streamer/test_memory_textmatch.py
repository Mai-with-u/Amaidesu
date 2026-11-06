"""记忆文本处理：归一化、两字切分与全文查询、触发词的子串与拼音层。

几个坑来自离线调研（合成弹幕集 + B 站弹幕误触发统计）："炸TNT"这类单字汉字紧挨英文的查询
按词查必漏；两个字的触发词做同音会撞上日常词（倒立/道理、联机/连击），所以拼音层只给三个字以上的词。
"""

from __future__ import annotations

import sqlite3

import pytest

from src.agents.streamer.memory.textmatch import (
    KeywordHit,
    KeywordMatcher,
    bigram_tokens,
    fts_or_query,
    index_text,
    needs_like_fallback,
    normalize,
)


def test_normalize_folds_width_case_emotes_spaces_punctuation_and_emoji() -> None:
    assert normalize("倒 立，洗头😂[doge]ＴＮＴ！") == "倒立洗头tnt"
    assert normalize("[笑哭][热词系列_知识增加]") == ""


def test_bigram_split_follows_the_two_char_rule() -> None:
    # 汉字段切成重叠两字；单字汉字段保留一字；非汉字段原样（转小写、全角转半角）
    assert bigram_tokens("倒立洗头") == ["倒立", "立洗", "洗头"]
    assert bigram_tokens("继续炸ＴＮＴ！") == ["继续", "续炸", "tnt"]
    assert bigram_tokens("我 a 好😂") == ["我", "a", "好"]
    # 英文词之间的空格是分隔，不会把两个词粘成一个
    assert bigram_tokens("hello world") == ["hello", "world"]
    assert index_text("芝麻[doge]又来了") == "芝麻 又来 来了"


def _fts_with(rows: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(tokens)")
    for rowid, text in enumerate(rows, start=1):
        conn.execute("INSERT INTO memory_fts(rowid, tokens) VALUES (?, ?)", (rowid, index_text(text)))
    return conn


def test_two_char_words_are_searchable_and_bm25_ranks_more_shared_words_first() -> None:
    """trigram 查不到两个字的词；两字切分后"倒立"这种词能查到，共有词多的排前面。"""
    conn = _fts_with(["今天去山那边找甘蔗", "她倒立洗头那一次", "倒立了一下"])
    query = fts_or_query("倒立洗头")
    assert query is not None
    rows = [
        row[0] for row in conn.execute("SELECT rowid FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank", (query,))
    ]
    assert rows == [2, 3]


def test_or_query_quotes_every_word_so_operators_stay_literal() -> None:
    assert fts_or_query("倒立 OR 倒立") == '"倒立" OR "or"'
    assert fts_or_query("，，😂") is None


def test_lone_cjk_char_next_to_latin_needs_like_fallback() -> None:
    """ "炸TNT"的"炸"在原文里被切进"续炸"，索引里没有单独的"炸"，按词查必漏。"""
    conn = _fts_with(["继续炸tnt"])
    assert conn.execute("SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH ?", ('"炸"',)).fetchone()[0] == 0
    assert needs_like_fallback("炸TNT") is True
    assert needs_like_fallback("倒立洗头") is False
    # 整段只有一个汉字时没有前后可粘，不算这个坑
    assert needs_like_fallback("炸") is False


@pytest.fixture()
def matcher() -> KeywordMatcher[str]:
    return KeywordMatcher(
        {
            "梗-倒立": ["倒立洗头", "倒立"],
            "承诺-炸": ["炸TNT"],
            "名字-猫": ["芝"],
            "游戏-联机": ["联机"],
        }
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("倒 立 洗 头[doge]", [KeywordHit("梗-倒立", "倒立洗头", "exact"), KeywordHit("梗-倒立", "倒立", "exact")]),
        ("到立洗头还记得吗", [KeywordHit("梗-倒立", "倒立洗头", "homophone")]),
        ("dlxt！", [KeywordHit("梗-倒立", "倒立洗头", "initials")]),
        ("继续炸ｔｎｔ", [KeywordHit("承诺-炸", "炸TNT", "exact")]),
    ],
)
def test_keywords_match_by_substring_homophone_and_initials(
    matcher: KeywordMatcher[str], text: str, expected: list[KeywordHit[str]]
) -> None:
    assert matcher.match(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "你说的有道理",  # 两字词"倒立"不做同音
        "这波连击漂亮",  # 两字词"联机"不做同音
        "dlxtx",  # 首字母要整串相等
        "芝麻开门",  # 一个字的触发词不收
        "",
    ],
)
def test_two_char_and_single_char_keywords_do_not_false_trigger(matcher: KeywordMatcher[str], text: str) -> None:
    assert matcher.match(text) == []
