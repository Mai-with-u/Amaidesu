"""memory.refs 各解析口径的行为边界：每个消费方认哪些写法、互相拒什么，逐口径钉死。"""

from src.agents.streamer.memory.refs import (
    ARCHIVE,
    COMMITMENT,
    format_refs,
    parse_commitment_ref,
    parse_line_number,
    parse_loose,
    parse_refs_column,
    parse_strict,
    render_archive_number,
    render_commitment_number,
    strip_numbers,
)


def test_render_produces_documented_forms() -> None:
    assert render_commitment_number(7) == "约#7"
    assert render_archive_number(41) == "档#41"
    assert format_refs([7, 8], COMMITMENT) == '["约#7", "约#8"]'
    assert format_refs([], COMMITMENT) == ""


def test_refs_column_splits_by_prefix() -> None:
    raw = '["约#7", "档#41", "档#7", "乱写#3"]'
    assert parse_refs_column(raw, COMMITMENT) == {7}
    assert parse_refs_column(raw, ARCHIVE) == {41, 7}
    assert parse_refs_column("不是 JSON", ARCHIVE) == set()
    assert parse_refs_column(None, ARCHIVE) == set()
    assert parse_refs_column('["档#41"]', COMMITMENT) == set(), "两个编号空间互不串号"


def test_parse_strict_only_accepts_prefixed_forms() -> None:
    assert parse_strict("档#42") == (ARCHIVE, 42)
    assert parse_strict("约#15") == (COMMITMENT, 15)
    assert parse_strict("[约#15]") == (COMMITMENT, 15), "更正工具允许一层方括号"
    assert parse_strict("  档 # 42 ") == (ARCHIVE, 42)
    assert parse_strict("7") is None, "严格式不带前缀不认"
    assert parse_strict("#15") is None
    assert parse_strict("档#0") is None, "非正整数不认"
    assert parse_strict(None) is None
    assert parse_strict(42) is None


def test_parse_loose_commitment_accepts_three_forms_archive_one() -> None:
    assert parse_loose("约#7") == (COMMITMENT, 7)
    assert parse_loose("#7") == (COMMITMENT, 7)
    assert parse_loose("7") == (COMMITMENT, 7)
    assert parse_loose("档#41") == (ARCHIVE, 41)
    assert parse_loose("#41") == (COMMITMENT, 41), "无前缀的 #N 一律算承诺编号，不是档案行"
    assert parse_loose("41") == (COMMITMENT, 41), "裸数字只算承诺编号，不是档案行"
    assert parse_loose("乱#7") is None, "别的前缀两头都不认"
    assert parse_loose("") is None
    assert parse_loose(7) is None


def test_parse_line_number_rejects_prefixed_forms() -> None:
    assert parse_line_number(18) == 18
    assert parse_line_number("#18") == 18
    assert parse_line_number(" 18 ") == 18
    assert parse_line_number("约#18") is None, "台词编号不带约#前缀"
    assert parse_line_number("档#18") is None
    assert parse_line_number(True) is None, "bool 是 int 的子类，按没给处理"
    assert parse_line_number("") is None


def test_parse_commitment_ref_rejects_archive_space() -> None:
    assert parse_commitment_ref(12) == 12
    assert parse_commitment_ref("#12") == 12
    assert parse_commitment_ref("12") == 12
    assert parse_commitment_ref("约#12") == 12
    assert parse_commitment_ref("档#12") is None, "档# 是档案行的编号空间"
    assert parse_commitment_ref(True) is None
    assert parse_commitment_ref("") is None


def test_strip_numbers_removes_bracketed_and_bare_forms() -> None:
    assert strip_numbers("[档#41] 水车搭好了") == ("水车搭好了", 1)
    assert strip_numbers("说好下次一起挖矿（约#7·上一场）") == ("说好下次一起挖矿（·上一场）", 1), (
        "只洗编号本身，括号里的注记留在原话里"
    )
    assert strip_numbers("档#3和约#4都念不出来") == ("和都念不出来", 2)
    assert strip_numbers(
        "没有编号的话",
    ) == ("没有编号的话", 0)
    assert strip_numbers("【档#3】带全角括号") == ("带全角括号", 1)
