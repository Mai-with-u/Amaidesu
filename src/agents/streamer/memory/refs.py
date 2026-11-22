"""记忆编号（约#N / 档#N）的唯一产生、解析与清洗入口。

两类编号空间：承诺（约#7）与档案行（档#41），同号不同义。各消费方
（Planner 点名、settle 取料、更正工具、引用列落库、台词洗编号）接受的
写法口径有意不同，这里每种口径一个函数，不合并语义：

- render_number / render_commitment_number / render_archive_number /
  format_refs：产生写法，各处渲染与落库必须经此，保证同一编号只有一种字符串
- parse_refs_column：引用列（JSON 字符串列表，形如 ["约#7"]）里某一类编号
- parse_strict：严格式，只认 ``档#N`` / ``约#N``（可带一层方括号），正整数
- parse_loose：Planner use_memory 的宽口径，承诺另认 ``#7`` / ``7``，档案只认 ``档#N``
- parse_line_number：settle 台词编号（纯数字 / ``#N``），带前缀不认
- parse_commitment_ref：settle 承诺编号（纯数字 / ``#N`` / ``约#N``），``档#`` 不认
- strip_numbers：把台词里的编号洗掉（含全角括号、井号与括号内跟的注记）
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional, Set, Tuple

#: kind 标识会随更正记录等持久化，值只能保持不变
COMMITMENT = "commitment"
ARCHIVE = "archive"

COMMITMENT_PREFIX = "约"
ARCHIVE_PREFIX = "档"


def render_number(kind: str, number: int) -> str:
    """编号的统一写法：承诺“约#7”，档案行“档#41”。"""
    prefix = COMMITMENT_PREFIX if kind == COMMITMENT else ARCHIVE_PREFIX
    return f"{prefix}#{int(number)}"


def render_commitment_number(commitment_id: int) -> str:
    """承诺编号的写法（约#7）；Planner 用它点名，回复器档不用编号。"""
    return render_number(COMMITMENT, commitment_id)


def render_archive_number(archive_line_id: int) -> str:
    """档案行编号的写法（档#41）：记忆摘要与记忆线索里标着，Planner 用它点名。"""
    return render_number(ARCHIVE, archive_line_id)


def format_refs(numbers: Iterable[int], kind: str) -> str:
    """把一类编号写成记忆引用列的文本（形如 ["约#7"]）；空集合写空串。"""
    refs = [render_number(kind, int(item)) for item in numbers]
    return json.dumps(refs, ensure_ascii=False) if refs else ""


def parse_refs_column(memory_refs: Optional[str], kind: str) -> Set[int]:
    """记忆引用文本（JSON 字符串列表）里某一类编号；取不出就空集。"""
    if not memory_refs:
        return set()
    try:
        refs = json.loads(memory_refs)
    except json.JSONDecodeError:
        return set()
    if not isinstance(refs, list):
        return set()
    prefix = COMMITMENT_PREFIX if kind == COMMITMENT else ARCHIVE_PREFIX
    numbers: Set[int] = set()
    for ref in refs:
        if not isinstance(ref, str):
            continue
        head, _sep, tail = ref.partition("#")
        if head.strip().startswith(prefix) and tail.strip().isdigit():
            numbers.add(int(tail.strip()))
    return numbers


def parse_strict(raw: Any) -> Optional[Tuple[str, int]]:
    """更正工具的严格口径：``档#N`` / ``约#N``，可带一层 ASCII 方括号；正整数才认。"""
    match = _STRICT_RE.match(str(raw or ""))
    if not match:
        return None
    prefix, digits = match.group(1), match.group(2)
    number = int(digits)
    if number <= 0:
        return None
    return (ARCHIVE, number) if prefix == ARCHIVE_PREFIX else (COMMITMENT, number)


_STRICT_RE = re.compile(r"^\s*\[?\s*(档|约)\s*#\s*(\d+)\s*\]?\s*$")


def parse_loose(raw: Any) -> Optional[Tuple[str, int]]:
    """Planner 点名的宽口径：承诺接受“约#7”“#7”“7”三种写法，档案行只认“档#41”；其余返回 None。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    kind = COMMITMENT
    if "#" in text:
        head, _sep, tail = text.rpartition("#")
        head = head.strip()
        if head.startswith(ARCHIVE_PREFIX):
            kind = ARCHIVE
        elif head and not head.startswith(COMMITMENT_PREFIX):
            return None
        text = tail.strip()
    return (kind, int(text)) if text.isdigit() else None


def parse_line_number(raw: Any) -> Optional[int]:
    """settle 的台词编号口径：整数、纯数字或 ``#N`` 才认，带别的前缀（约#/档#）不认。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("#"):
        text = text[1:].strip()
    return int(text) if text.isdigit() else None


def parse_commitment_ref(raw: Any) -> Optional[int]:
    """settle 的承诺编号口径：整数、纯数字、``#N``、``约#N`` 都认；``档#`` 与其余写法返回 None。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return int(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if "#" in text:
        head, _sep, tail = text.rpartition("#")
        if head.strip():
            # 带前缀的只认承诺前缀；档# 是档案行的编号空间，不能落进承诺编号
            refs = parse_refs_column(json.dumps([text], ensure_ascii=False), COMMITMENT)
            if len(refs) != 1:
                return None
            return int(next(iter(refs)))
        text = tail.strip()
    return int(text) if text.isdigit() else None


# 台词洗编号：编号（[档#N]、档#N、约#N）是房管在记忆摘要里点名用的记号，观众听不懂。
# 带方括号的连同括号里跟着的注记（如“·上一场”）一起去掉；没括号的只去编号本身
_BRACKETED_NUMBER = re.compile(r"[\[【]\s*[档约]\s*[#＃]\s*\d+[^\]】\n]{0,12}[\]】]")
_BARE_NUMBER = re.compile(r"[档约]\s*[#＃]\s*\d+")
# 去掉编号后剩下的空括号（“（档#3）”→“（）”）
_EMPTY_PARENS = re.compile(r"[（(]\s*[）)]")


def strip_numbers(text: str) -> Tuple[str, int]:
    """去掉文本里的记忆编号，返回（去掉后的文本，去掉了几处）；没有编号时原样返回、计数为 0。

    交给说话侧之前先洗一遍发言要点，说话侧生成台词后再洗一遍：编号念出来是“档井号四十一”，
    观众听不懂，也暴露了后台的记账方式。
    """
    cleaned, bracketed = _BRACKETED_NUMBER.subn("", text)
    cleaned, bare = _BARE_NUMBER.subn("", cleaned)
    count = bracketed + bare
    if not count:
        return text, 0
    cleaned = _EMPTY_PARENS.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, count
