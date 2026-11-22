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

游戏玩家在公屏说的关于他自己的事（玩家自述）进另一张待审表，口径见 ``validate_player_candidates``：
个人信息、名字或梗词、去重与观众候选同一套，证据换成他那句公屏和她回他的那句。

候选批准前对她完全不可见：这里只产出待写进待审区的条目，简报、线索卡、回想都不读候选表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.game_players import PendingPlayerCandidate
from src.modules.storage.models.memory import PendingViewerCandidate, UnregisteredItem

from .saves import SaveTimeline
from .settle_players import PlayerChat
from .textmatch import verbatim_quote

__all__ = [
    "MAX_CLAIMED_MISSING",
    "MAX_PLAYER_CANDIDATES",
    "MAX_VIEWER_CANDIDATES",
    "MAX_VIEWER_SELF_CANDIDATES",
    "CandidateCheck",
    "PlayerCandidateCheck",
    "normalize_title",
    "taken_from",
    "validate_claimed_missing",
    "validate_player_candidates",
    "validate_viewer_candidates",
]

# 一场至多列几条观众定下类候选（只是候选，宁缺毋滥）
MAX_VIEWER_CANDIDATES = 5
# 一场至多列几条老观众近况候选
MAX_VIEWER_SELF_CANDIDATES = 3
# 一场至多列几条"她说欠着的"
MAX_CLAIMED_MISSING = 3
# 一场至多列几条玩家自述（只是候选，宁缺毋滥）
MAX_PLAYER_CANDIDATES = 3
_TITLE_CHARS = 30
_QUOTE_CHARS = 80
# 观众定下的那个名字或梗词：两个字以上（单字太容易碰巧出现在弹幕里）、不超过 16 字
_WORD_MIN_CHARS = 2
_WORD_CHARS = 16
_VIEWER_CODE = re.compile(r"(?:老观众|观众)\s*\d+")
_PLAYER_CODE = re.compile(r"玩家\s*\d+")
# 观众个人信息的模式：手机号或长串数字、年龄、住址、学校、工作单位、就医病历、联系方式
_PERSONAL = re.compile(
    r"\d{7,}|\d+\s*岁|住在|住址|地址|小区|门牌|学校|大学|中学|小学|高中|初中|年级"
    r"|单位|公司|工位|医院|就诊|住院|病历|确诊|手术|手机号|手机|电话|微信号|[Qq]{2}号|身份证|邮箱"
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


@dataclass
class PlayerCandidateCheck:
    """玩家自述的校验结果：要写进待审表的、没通过的条目、因个人信息丢掉的条数。"""

    candidates: List[PendingPlayerCandidate] = field(default_factory=list)
    missed: List[UnregisteredItem] = field(default_factory=list)
    personal_dropped: int = 0


def _word_from_speaker(word: str, title: str, source_text: Optional[str]) -> bool:
    """说话人定下的名字或梗词核得上吗：长度合规、标题里原样出现、他那句原文里也原样出现（压掉空白的写法也认）。

    观众只问"那只猫叫啥"、她自己说"叫奶糖"，模型容易把它当成观众定下的；名字不在弹幕里，就是她自己起的。
    玩家自述同理：关键词不在他那句公屏里，就是她猜的或模型补的。
    """
    if not (_WORD_MIN_CHARS <= len(word) <= _WORD_CHARS) or word not in title:
        return False
    raw = source_text or ""
    return word in raw or word in " ".join(raw.split())


@dataclass(frozen=True)
class _RowResult:
    """一行模型候选的核对结论：建成条目、打回原因、个人信息丢弃、静默跳过（超额/去重命中），四者取其一。"""

    candidate: Any = None
    missed: Optional[UnregisteredItem] = None
    personal_dropped: bool = False
    silent: bool = False


def _run_candidate_rows(
    rows: Any,
    check: Any,
    *,
    cap_reached: Callable[[], bool],
    row_result: Callable[[Mapping[str, Any]], _RowResult],
) -> None:
    """三类候选校验共用的逐行运行器：跳过非 dict 行与超额行，把每行的核对结论分派到结果集。

    行内各道门禁的先后顺序属各校验器自己的口径（打回原因取先撞上的那道），不在这里统一。
    """
    if not isinstance(rows, list):
        return
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if cap_reached():
            continue
        result = row_result(raw)
        if result.personal_dropped:
            check.personal_dropped += 1
            continue
        if result.missed is not None:
            check.missed.append(result.missed)
            continue
        if result.candidate is not None:
            check.candidates.append(result.candidate)


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
    familiar_viewer_ids: Iterable[int] = (),
    decided_self_titles: Optional[Mapping[int, Iterable[str]]] = None,
    rejected_self_chats: Iterable[int] = (),
) -> CandidateCheck:
    """把模型列的 ``viewer_candidates``（观众定下的 ``viewer_defined`` 与老观众近况 ``viewer_self``）逐条核成待审候选。

    ``taken_evidence`` / ``taken_titles`` 是本场新写与上一份档案里的行（作证据的台词、标题）；
    ``rejected_*`` 是驳回过的观众定下类候选挂的弹幕与标题；``familiar_viewer_ids`` 是代码认定的本场老观众主键集合，
    非老观众行上报的 ``viewer_self`` 一律硬门禁拦截记入 ``missed``。
    """
    check = CandidateCheck()
    seen_by_kind: Set[Tuple[str, int]] = {("viewer_defined", int(item)) for item in taken_evidence}
    titles: Set[str] = {normalize_title(item) for item in taken_titles} | {
        normalize_title(item) for item in rejected_titles
    }
    titles.discard("")
    rejected_chats: Set[int] = {int(item) for item in rejected_viewer_chats}
    familiar_set: Set[int] = {int(item) for item in familiar_viewer_ids}
    self_titles_by_viewer: Dict[int, Set[str]] = {
        int(vid): {normalize_title(t) for t in items if normalize_title(t)}
        for vid, items in (decided_self_titles or {}).items()
    }
    rejected_self_chat_set: Set[int] = {int(item) for item in rejected_self_chats}
    counts = {"viewer_defined": 0, "viewer_self": 0}

    def _result(raw: Mapping[str, Any]) -> _RowResult:
        kind = str(raw.get("kind") or "viewer_defined").strip() or "viewer_defined"
        raw_title = str(raw.get("title") or "").strip()
        quote = str(raw.get("quote") or "").strip()
        word = str(raw.get("word") or "").strip()
        raw_viewer_quote = str(raw.get("viewer_quote") or "")
        if kind not in ("viewer_defined", "viewer_self"):
            if not _PERSONAL.search(" ".join((raw_title, word, raw_viewer_quote, quote))):
                return _RowResult(
                    missed=UnregisteredItem(
                        kind="candidate",
                        what=_VIEWER_CODE.sub("观众", raw_title)[:_TITLE_CHARS],
                        quote=quote,
                        reason=f"观众候选类型认不出（{kind[:20]}），未列入",
                    )
                )
            return _RowResult(personal_dropped=True)

        if kind == "viewer_self":
            if counts["viewer_self"] >= MAX_VIEWER_SELF_CANDIDATES:
                return _RowResult(silent=True)
            title = re.sub(r"\s+", " ", _VIEWER_CODE.sub("ta ", raw_title)).strip()[:_TITLE_CHARS]
            line = line_of(raw.get("line"))
            if _PERSONAL.search(" ".join((title, word, raw_viewer_quote, quote))):
                return _RowResult(personal_dropped=True)
            if line is None or line.reply_chat_id is None or line.reply_simulated:
                return _RowResult(
                    missed=UnregisteredItem(
                        kind="candidate",
                        what=title,
                        quote=quote,
                        reason="老观众近况：引用的不是她回复真实观众弹幕的那一行",
                    )
                )
            if line.reply_viewer_id is None or int(line.reply_viewer_id) not in familiar_set:
                return _RowResult(
                    missed=UnregisteredItem(
                        kind="candidate",
                        what=title,
                        quote=quote,
                        reason="老观众近况只认标注了老观众的那一行，普通观众弹幕不列入",
                    )
                )
            viewer_quote = verbatim_quote(raw.get("viewer_quote"), line.reply_content, _QUOTE_CHARS)
            reply_quote = verbatim_quote(quote, line.content, _QUOTE_CHARS)
            if not title or viewer_quote is None or reply_quote is None:
                return _RowResult(
                    missed=UnregisteredItem(
                        kind="candidate",
                        what=title,
                        quote=quote,
                        reason=f"老观众近况：没有标题，或弹幕与她那句的原话不是第 {raw.get('line')} 行的逐字片段",
                    )
                )
            if not _word_from_speaker(word, title, line.reply_content):
                return _RowResult(
                    missed=UnregisteredItem(
                        kind="candidate",
                        what=title,
                        quote=quote,
                        reason=(
                            f"老观众近况：关键词「{word}」不在标题里，或不在第 {raw.get('line')} 行回复的那条弹幕原文里"
                            if word
                            else "老观众近况：没写弹幕里的近况或喜好关键词"
                        ),
                    )
                )
            vid = int(line.reply_viewer_id)
            key = normalize_title(title)
            viewer_seen = self_titles_by_viewer.setdefault(vid, set())
            if (
                ("viewer_self", int(line.id)) in seen_by_kind
                or key in viewer_seen
                or int(line.reply_chat_id) in rejected_self_chat_set
            ):
                return _RowResult(silent=True)
            seen_by_kind.add(("viewer_self", int(line.id)))
            viewer_seen.add(key)
            game_name = game_of(raw.get("game"))
            save = saves.save_at(game_name, int(line.timestamp_ms))
            counts["viewer_self"] += 1
            return _RowResult(
                candidate=PendingViewerCandidate(
                    kind="viewer_self",
                    title=title,
                    viewer_chat_id=int(line.reply_chat_id),
                    viewer_quote=viewer_quote,
                    word=word,
                    reply_chat_id=int(line.id),
                    reply_quote=reply_quote,
                    game=game_name,
                    save_id=save.id,
                    save_name=save.name,
                    viewer_id=vid,
                )
            )

        if counts["viewer_defined"] >= MAX_VIEWER_CANDIDATES:
            return _RowResult(silent=True)
        title = _VIEWER_CODE.sub("观众", raw_title)[:_TITLE_CHARS]
        line = line_of(raw.get("line"))
        if line is None or line.reply_chat_id is None or line.reply_simulated:
            # 只认她在回复一条真实观众弹幕的那几行：模拟观众说的不算观众定下的
            return _RowResult(
                missed=UnregisteredItem(
                    kind="candidate", what=title, quote=quote, reason="引用的不是她回复观众弹幕的那一行"
                )
            )
        viewer_quote = verbatim_quote(raw.get("viewer_quote"), line.reply_content, _QUOTE_CHARS)
        reply_quote = verbatim_quote(quote, line.content, _QUOTE_CHARS)
        if _PERSONAL.search(" ".join((title, word, raw_viewer_quote, quote))):
            # 观众的个人信息不进待审区，也不把内容写进报告
            return _RowResult(personal_dropped=True)
        if not title or viewer_quote is None or reply_quote is None:
            return _RowResult(
                missed=UnregisteredItem(
                    kind="candidate",
                    what=title,
                    quote=quote,
                    reason=f"没有标题，或弹幕与她那句的原话不是第 {raw.get('line')} 行的逐字片段",
                )
            )
        if not _word_from_speaker(word, title, line.reply_content):
            # 观众只问了名字、名字是她自己起的，或标题里没写那个词：不是观众定下的，不列，进报告给人看
            return _RowResult(
                missed=UnregisteredItem(
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
        key = normalize_title(title)
        if (
            ("viewer_defined", int(line.id)) in seen_by_kind
            or key in titles
            or int(line.reply_chat_id) in rejected_chats
        ):
            # 已经写成档案、驳回过、或同一行已经列过同类型候选：不重复列
            return _RowResult(silent=True)
        seen_by_kind.add(("viewer_defined", int(line.id)))
        titles.add(key)
        game_name = game_of(raw.get("game"))
        save = saves.save_at(game_name, int(line.timestamp_ms))
        counts["viewer_defined"] += 1
        return _RowResult(
            candidate=PendingViewerCandidate(
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
                viewer_id=int(line.reply_viewer_id) if line.reply_viewer_id is not None else None,
            )
        )

    _run_candidate_rows(rows, check, cap_reached=lambda: False, row_result=_result)
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

    def _result(raw: Mapping[str, Any]) -> _RowResult:
        title = _VIEWER_CODE.sub("观众", str(raw.get("what") or raw.get("title") or "").strip())[:_TITLE_CHARS]
        quote = str(raw.get("quote") or "").strip()
        line = line_of(raw.get("line"))
        if line is None:
            return _RowResult(
                missed=UnregisteredItem(
                    kind="candidate", what=title, quote=quote, reason="她说欠着的：引用的不是本场她的台词"
                )
            )
        if _PERSONAL.search(" ".join((title, quote))):
            return _RowResult(personal_dropped=True)
        reply_quote = verbatim_quote(quote, line.content, _QUOTE_CHARS)
        if not title or reply_quote is None:
            return _RowResult(
                missed=UnregisteredItem(
                    kind="candidate",
                    what=title,
                    quote=quote,
                    reason=f"她说欠着的：没有标题，或原话不是第 {raw.get('line')} 行的逐字片段",
                )
            )
        key = normalize_title(title)
        if int(line.id) in lines_taken or key in titles:
            # 账上有、驳回过、或同一行已经列过：不是"找不到"，不列
            return _RowResult(silent=True)
        lines_taken.add(int(line.id))
        titles.add(key)
        viewer_chat_id: Optional[int] = None
        viewer_id: Optional[int] = None
        viewer_quote = ""
        if line.reply_chat_id is not None and not line.reply_simulated:
            # 她在回一条真实观众弹幕：挂上那条弹幕，观众被删或说"忘了我"时候选一起删
            viewer_chat_id = int(line.reply_chat_id)
            viewer_id = int(line.reply_viewer_id) if line.reply_viewer_id is not None else None
            viewer_text = " ".join((line.reply_content or "").split())[:_QUOTE_CHARS]
            viewer_quote = "" if _PERSONAL.search(viewer_text) else viewer_text
        game_name = game_of(raw.get("game"))
        save = saves.save_at(game_name, int(line.timestamp_ms))
        return _RowResult(
            candidate=PendingViewerCandidate(
                kind="claimed_missing",
                title=title,
                viewer_chat_id=viewer_chat_id,
                viewer_quote=viewer_quote,
                reply_chat_id=int(line.id),
                reply_quote=reply_quote,
                game=game_name,
                save_id=save.id,
                save_name=save.name,
                viewer_id=viewer_id,
            )
        )

    _run_candidate_rows(
        rows, check, cap_reached=lambda: len(check.candidates) >= MAX_CLAIMED_MISSING, row_result=_result
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


def _player_reply(
    raw: Mapping[str, Any],
    player_id: int,
    *,
    players: PlayerChat,
    line_of: Callable[[Any], Optional[SettleSpeechLine]],
) -> Optional[Tuple[Optional[int], Optional[int], int, str]]:
    """她回他的那句：返回（公屏行, 台词行, 时刻, 原文），对不上这位玩家返回 None。

    公屏那句（``chat``）要点了他的名、或紧接着他的话说；台词（``line``）要落库时记下在回应他、
    而且不是在回一条弹幕（回弹幕的那一句对象是观众）。
    """
    if raw.get("chat") not in (None, ""):
        chat = players.chat_line(raw.get("chat"))
        if chat is None or player_id not in players.targets.get(int(chat.id), frozenset()):
            return None
        return int(chat.id), None, int(chat.at_ms), chat.content or ""
    line = line_of(raw.get("line"))
    if line is None or line.reply_player_id is None or line.reply_chat_id is not None:
        return None
    if int(line.reply_player_id) != player_id:
        return None
    return None, int(line.id), int(line.timestamp_ms), line.content or ""


def validate_player_candidates(
    rows: Any,
    *,
    players: PlayerChat,
    line_of: Callable[[Any], Optional[SettleSpeechLine]],
    decided_titles: Mapping[int, Iterable[str]],
    rejected_player_chats: Iterable[int],
) -> PlayerCandidateCheck:
    """把模型列的 ``player_candidates``（玩家在公屏说的关于他自己的事、她当场接住了）逐条核成待审候选。

    - 对谁：玩家代号认得出；她回他的那句按 ``_player_reply`` 核对（公屏点名或紧接着、台词记下的回应对象）；
    - 原话：``player_quote`` 是他在她那句之前说的、给模型看过的一句公屏的逐字片段，``quote`` 是她那句的逐字片段；
    - 关键词：``word`` 两个字以上，在标题里、也在他那句原文里原样出现——不在他原话里的，是她猜的或模型补的；
    - 个人信息：标题、关键词、两段引文里有手机号、年龄、住址、学校、联系方式这类模式的整条丢掉，只计数；
    - 去重：``decided_titles`` 是每位玩家已批、已驳回的标题，``rejected_player_chats`` 是驳回过的他那句公屏，
      都不再列；同一句他的话、同一句她的回应只列一条；一场至多三条；
    - 标题里的玩家代号换成"他"：代号只在这一场有意义。
    """
    check = PlayerCandidateCheck()
    titles: Dict[int, Set[str]] = {
        int(player_id): {normalize_title(title) for title in items} for player_id, items in decided_titles.items()
    }
    used_chats: Set[int] = {int(item) for item in rejected_player_chats}
    used_replies: Set[Tuple[Optional[int], Optional[int]]] = set()

    def _result(raw: Mapping[str, Any]) -> _RowResult:
        title = _PLAYER_CODE.sub("他", str(raw.get("title") or "").strip())[:_TITLE_CHARS]
        quote = str(raw.get("quote") or "").strip()
        word = str(raw.get("word") or "").strip()
        if _PERSONAL.search(" ".join((title, word, str(raw.get("player_quote") or ""), quote))):
            # 玩家的个人信息不进待审区，也不把内容写进报告
            return _RowResult(personal_dropped=True)
        player_id = players.player_of(raw.get("player"))
        reply = _player_reply(raw, player_id, players=players, line_of=line_of) if player_id is not None else None
        if player_id is None or reply is None:
            return _RowResult(
                missed=UnregisteredItem(
                    kind="candidate", what=title, quote=quote, reason="玩家自述：引用的不是她回应这位玩家的那一句"
                )
            )
        game_chat_id, live_chat_id, reply_at_ms, reply_text = reply
        # 他那句只在给模型看过、早于她这句回应的公屏里找：她接住的只能是他先说的话
        his_line = players.player_line(player_id, str(raw.get("player_quote") or ""), before_ms=reply_at_ms)
        player_quote = verbatim_quote(raw.get("player_quote"), his_line.content if his_line else None, _QUOTE_CHARS)
        reply_quote = verbatim_quote(quote, reply_text, _QUOTE_CHARS)
        if not title or his_line is None or player_quote is None or reply_quote is None:
            return _RowResult(
                missed=UnregisteredItem(
                    kind="candidate",
                    what=title,
                    quote=quote,
                    reason="玩家自述：没有标题，或他那句、她那句不是给出的公屏与台词的逐字片段",
                )
            )
        if not _word_from_speaker(word, title, his_line.content):
            # 关键词不在他原话里：是她猜的或模型补的，不算他说的关于自己的事，进报告给人看
            return _RowResult(
                missed=UnregisteredItem(
                    kind="candidate",
                    what=title,
                    quote=quote,
                    reason=(
                        f"玩家自述：关键词「{word}」不在标题里，或不在他那句公屏原文里"
                        if word
                        else "玩家自述：没写关键词"
                    ),
                )
            )
        key = normalize_title(title)
        seen = titles.setdefault(player_id, set())
        if key in seen or int(his_line.id) in used_chats or (game_chat_id, live_chat_id) in used_replies:
            # 批准过、驳回过，或这一场同一句已经列过：不重复列
            return _RowResult(silent=True)
        seen.add(key)
        used_chats.add(int(his_line.id))
        used_replies.add((game_chat_id, live_chat_id))
        return _RowResult(
            candidate=PendingPlayerCandidate(
                player_id=player_id,
                title=title,
                player_chat_id=int(his_line.id),
                player_quote=player_quote,
                reply_game_chat_id=game_chat_id,
                reply_live_chat_id=live_chat_id,
                reply_quote=reply_quote,
            )
        )

    _run_candidate_rows(
        rows, check, cap_reached=lambda: len(check.candidates) >= MAX_PLAYER_CANDIDATES, row_result=_result
    )
    return check
