"""下播整理列出的观众候选的校验：观众定下、她当场接受却没复述的事，人批准了才进记忆。

全部是代码核对，不靠模型自觉：

- 证据：``line`` 必须是本场她播出、并且在回复一条真实观众弹幕（不是模拟观众）的那一行；
  ``viewer_quote`` 是那条弹幕的逐字片段，``quote`` 是她那行的逐字片段（压掉连续空白的写法也认）；
- 名字或梗词：模型另写一个 ``word``（观众定下的那个名字、梗词或设定里的关键词），它必须在标题里原样出现、
  也在那条弹幕原文里原样出现。观众只问了"叫啥"、名字是她自己起的，弹幕里就没有这个词，不算观众定下的；
  对不上的不列，写进整理报告的未登记清单；
- 个人信息：标题、名字或梗词、两段引文里出现手机号、地址、学校、年龄、联系方式这类模式的，整条丢掉，
  只计数，不把内容写进整理报告；
- 去重：和本场新写或上一份档案里的行同一句作证据、或归一化后标题相同的不列；和驳回过的候选同一条
  弹幕、或归一化后标题相同的不列；同一行台词只列一条；
- 标题里模型写的观众代号（"观众3"）换成"观众"：代号只在这一场有意义。

"她说欠着、账上却找不到的"（``claimed_missing``）走同一个待审区，口径见 ``validate_claimed_missing``。

候选批准前对她完全不可见：这里只产出待写进待审区的条目，简报、线索卡、回想都不读候选表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Sequence, Set

from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.memory import PendingViewerCandidate, UnregisteredItem

from .saves import SaveTimeline

__all__ = [
    "MAX_CLAIMED_MISSING",
    "MAX_VIEWER_CANDIDATES",
    "CandidateCheck",
    "normalize_title",
    "taken_from",
    "validate_claimed_missing",
    "validate_viewer_candidates",
]

# 一场至多列几条观众候选（只是候选，宁缺毋滥）
MAX_VIEWER_CANDIDATES = 5
# 一场至多列几条"她说欠着的"
MAX_CLAIMED_MISSING = 3
_TITLE_CHARS = 30
_QUOTE_CHARS = 80
# 观众定下的那个名字或梗词：两个字以上（单字太容易碰巧出现在弹幕里）、不超过 16 字
_WORD_MIN_CHARS = 2
_WORD_CHARS = 16
_VIEWER_CODE = re.compile(r"观众\s*\d+")
# 观众个人信息的模式：手机号或长串数字、年龄、住址、学校、联系方式
_PERSONAL = re.compile(
    r"\d{7,}|\d+\s*岁|住在|住址|地址|小区|门牌|学校|大学|中学|小学|高中|初中|年级|手机|电话|微信号|[Qq]{2}号|身份证"
)
_NOT_WORD = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_title(text: str) -> str:
    """标题去重用的归一化：去掉空白与标点、字母转小写。"""
    return _NOT_WORD.sub("", text or "").lower()


@dataclass
class CandidateCheck:
    """校验的结果：要写进待审区的候选、没通过的条目、因个人信息丢掉的条数。"""

    candidates: List[PendingViewerCandidate] = field(default_factory=list)
    missed: List[UnregisteredItem] = field(default_factory=list)
    personal_dropped: int = 0


def _verbatim(quote: Any, content: Optional[str], limit: int) -> Optional[str]:
    """逐字片段校验：原文与压掉连续空白的写法都认；空、超长或对不上返回 None。"""
    text = str(quote or "").strip()
    raw = content or ""
    if not text or len(text) > limit:
        return None
    return text if text in raw or text in " ".join(raw.split()) else None


def _word_from_viewer(word: str, title: str, viewer_text: Optional[str]) -> bool:
    """观众定下的名字或梗词核得上吗：长度合规、标题里原样出现、那条弹幕原文里也原样出现（压掉空白的写法也认）。

    观众只问"那只猫叫啥"、她自己说"叫奶糖"，模型容易把它当成观众定下的；名字不在弹幕里，就是她自己起的。
    """
    if not (_WORD_MIN_CHARS <= len(word) <= _WORD_CHARS) or word not in title:
        return False
    raw = viewer_text or ""
    return word in raw or word in " ".join(raw.split())


def validate_viewer_candidates(
    rows: Any,
    *,
    line_of: Callable[[Any], Optional[SettleSpeechLine]],
    game_of: Callable[[Any], Optional[str]],
    saves: SaveTimeline,
    taken_evidence: Iterable[int],
    taken_titles: Iterable[str],
    rejected_viewer_chats: Iterable[int],
    rejected_titles: Iterable[str],
) -> CandidateCheck:
    """把模型列的 ``viewer_candidates`` 逐条核成待审候选。

    ``taken_evidence`` / ``taken_titles`` 是本场新写与上一份档案里的行（作证据的台词、标题）；
    ``rejected_*`` 是驳回过的候选挂的弹幕与标题。挂了游戏的按那行台词的时刻认存档，与档案行同一口径。
    """
    check = CandidateCheck()
    if not isinstance(rows, list):
        return check
    evidence: Set[int] = {int(item) for item in taken_evidence}
    titles: Set[str] = {normalize_title(item) for item in taken_titles} | {
        normalize_title(item) for item in rejected_titles
    }
    titles.discard("")
    rejected_chats: Set[int] = {int(item) for item in rejected_viewer_chats}
    for raw in rows:
        if not isinstance(raw, dict) or len(check.candidates) >= MAX_VIEWER_CANDIDATES:
            continue
        title = _VIEWER_CODE.sub("观众", str(raw.get("title") or "").strip())[:_TITLE_CHARS]
        quote = str(raw.get("quote") or "").strip()
        line = line_of(raw.get("line"))
        if line is None or line.reply_chat_id is None or line.reply_simulated:
            # 只认她在回复一条真实观众弹幕的那几行：模拟观众说的不算观众定下的
            check.missed.append(
                UnregisteredItem(kind="candidate", what=title, quote=quote, reason="引用的不是她回复观众弹幕的那一行")
            )
            continue
        viewer_quote = _verbatim(raw.get("viewer_quote"), line.reply_content, _QUOTE_CHARS)
        reply_quote = _verbatim(quote, line.content, _QUOTE_CHARS)
        word = str(raw.get("word") or "").strip()
        if _PERSONAL.search(" ".join((title, word, str(raw.get("viewer_quote") or ""), quote))):
            # 观众的个人信息不进待审区，也不把内容写进报告
            check.personal_dropped += 1
            continue
        if not title or viewer_quote is None or reply_quote is None:
            check.missed.append(
                UnregisteredItem(
                    kind="candidate",
                    what=title,
                    quote=quote,
                    reason=f"没有标题，或弹幕与她那句的原话不是第 {raw.get('line')} 行的逐字片段",
                )
            )
            continue
        if not _word_from_viewer(word, title, line.reply_content):
            # 观众只问了名字、名字是她自己起的，或标题里没写那个词：不是观众定下的，不列，进报告给人看
            check.missed.append(
                UnregisteredItem(
                    kind="candidate",
                    what=title,
                    quote=quote,
                    reason=(
                        f"名字或梗词「{word}」不在标题里，或不在第 {raw.get('line')} 行回复的那条弹幕原文里"
                        if word
                        else "没写观众定下的名字或梗词"
                    ),
                )
            )
            continue
        key = normalize_title(title)
        if int(line.id) in evidence or key in titles or int(line.reply_chat_id) in rejected_chats:
            # 已经写成档案、驳回过、或同一行已经列过：不重复列
            continue
        evidence.add(int(line.id))
        titles.add(key)
        game_name = game_of(raw.get("game"))
        save = saves.save_at(game_name, int(line.timestamp_ms))
        check.candidates.append(
            PendingViewerCandidate(
                kind="viewer_defined",
                title=title,
                viewer_chat_id=int(line.reply_chat_id),
                viewer_quote=viewer_quote,
                # 核对过的名字或梗词跟着候选存下：批准后渲染原话时这一段不遮成"那位观众"
                word=word,
                reply_chat_id=int(line.id),
                reply_quote=reply_quote,
                game=game_name,
                save_id=save.id,
                save_name=save.name,
            )
        )
    return check


def validate_claimed_missing(
    rows: List[Any],
    *,
    line_of: Callable[[Any], Optional[SettleSpeechLine]],
    game_of: Callable[[Any], Optional[str]],
    saves: SaveTimeline,
    ledger_titles: Iterable[str],
    taken_lines: Iterable[int],
    rejected_reply_chats: Iterable[int],
    rejected_titles: Iterable[str],
) -> CandidateCheck:
    """把模型列的"她说欠着、账上却找不到的"逐条核成待审候选，人批准才登记成承诺，代码从不自动登记。

    她说"上次答应过你们……"，可能是账漏登记了，也可能是她顺着观众的话编的，代码分不出来，交给人判：

    - 引文必须是本场她播出那一行的逐字片段；
    - 账上对得上的不列：归一化后与 ``ledger_titles``（未了结、已了结、本场新登记）任一条相同，或者那一行
      已经作了新承诺、了结的证据（``taken_lines``）；
    - 驳回过的同一句台词、同样的标题不再列；一行台词至多一条，一场至多三条；
    - 她当时在回一条真实观众弹幕的，候选挂上那条弹幕（删观众、忘了我时一起删）；弹幕里有个人信息时
      只挂不抄原文；标题或她那句里有个人信息的整条丢掉，只计数。
    """
    check = CandidateCheck()
    lines_taken: Set[int] = {int(item) for item in taken_lines} | {int(item) for item in rejected_reply_chats}
    titles: Set[str] = {normalize_title(item) for item in ledger_titles} | {
        normalize_title(item) for item in rejected_titles
    }
    titles.discard("")
    for raw in rows:
        if not isinstance(raw, dict) or len(check.candidates) >= MAX_CLAIMED_MISSING:
            continue
        title = _VIEWER_CODE.sub("观众", str(raw.get("what") or raw.get("title") or "").strip())[:_TITLE_CHARS]
        quote = str(raw.get("quote") or "").strip()
        line = line_of(raw.get("line"))
        if line is None:
            check.missed.append(
                UnregisteredItem(kind="candidate", what=title, quote=quote, reason="她说欠着的：引用的不是本场她的台词")
            )
            continue
        if _PERSONAL.search(" ".join((title, quote))):
            check.personal_dropped += 1
            continue
        reply_quote = _verbatim(quote, line.content, _QUOTE_CHARS)
        if not title or reply_quote is None:
            check.missed.append(
                UnregisteredItem(
                    kind="candidate",
                    what=title,
                    quote=quote,
                    reason=f"她说欠着的：没有标题，或原话不是第 {raw.get('line')} 行的逐字片段",
                )
            )
            continue
        key = normalize_title(title)
        if int(line.id) in lines_taken or key in titles:
            # 账上有、驳回过、或同一行已经列过：不是"找不到"，不列
            continue
        lines_taken.add(int(line.id))
        titles.add(key)
        viewer_chat_id: Optional[int] = None
        viewer_quote = ""
        if line.reply_chat_id is not None and not line.reply_simulated:
            # 她在回一条真实观众弹幕：挂上那条弹幕，观众被删或说"忘了我"时候选一起删
            viewer_chat_id = int(line.reply_chat_id)
            viewer_text = " ".join((line.reply_content or "").split())[:_QUOTE_CHARS]
            viewer_quote = "" if _PERSONAL.search(viewer_text) else viewer_text
        game_name = game_of(raw.get("game"))
        save = saves.save_at(game_name, int(line.timestamp_ms))
        check.candidates.append(
            PendingViewerCandidate(
                kind="claimed_missing",
                title=title,
                viewer_chat_id=viewer_chat_id,
                viewer_quote=viewer_quote,
                reply_chat_id=int(line.id),
                reply_quote=reply_quote,
                game=game_name,
                save_id=save.id,
                save_name=save.name,
            )
        )
    return check


def taken_from(lines: Sequence[Any]) -> tuple[List[int], List[str]]:
    """档案行（新写的待写行或快照里的行）作证据的台词与标题，给去重用。"""
    evidence: List[int] = []
    titles: List[str] = []
    for item in lines:
        line = getattr(item, "line", item)
        evidence.append(int(line.evidence_chat_id))
        titles.append(str(getattr(line, "text", "") or ""))
    return evidence, titles
