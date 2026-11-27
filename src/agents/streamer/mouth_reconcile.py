"""即时反应与规划器的对账件（Agent 内部件，代码直连，不注册为工具）。

解决的问题：
规划器从开窗读快照到真正出声通常隔十多秒，这期间"即时反应"可能已经对刚发生的游戏事件
当场接了一句。两条出口互不知情时会出现三类穿帮：
1. 重复欢迎：同一个人进直播间或上线进游戏，即时反应刚欢迎完，规划器二十秒后又欢迎一遍；
2. 重复反应：同一场遭遇或送礼，即时反应刚接完话，规划器又把同一件事讲一遍；
3. 相邻矛盾：决策开窗时还在旧现场（如掉线卡在门外、东西还没吃），出声前游戏已切入新世界
   且即时反应已说出"换到新世界站岸边了"，决策台词却接着说"还卡在门口进不去"。

本模块维护本场口头流水（区分即时反应与规划器），并在决策台词出声前做对账：
- 本场已欢迎过的人再次被欢迎时，确定性剥掉重复欢迎句（只剩欢迎则整条不说，后面还有新进展则只留新进展）；
- 本轮开窗后即时反应没说过话时，不做任何模型调用，零额外时延放行；
- 本轮开窗后即时反应说过话时，用独立核对档（verifier profile，缺省关思考、可换小模型）核对一次：
  内容已被即时反应说过的直接不说，被即时反应与期间新游戏事件推翻的整句作废（交给规划器按新现场重想一步，
  或静默撤回）；裁决契约三选一（放行 / 重复 / 作废），无改写分支——模型裁决只判不写，
  避免轻量模型借裁剪扭曲原话（ADR-080）。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
from typing import Any, Deque, Iterable, List, Literal, Optional, Sequence, Tuple

from src.modules.logging import get_logger

from .speech_guard import find_verbatim_repeat

__all__ = [
    "ReconcileAction",
    "ReconcileVerdict",
    "SpokenEntry",
    "SpokenLog",
    "extract_player_join_name",
    "is_greeting_sentence",
    "mentions_name",
    "reconcile_with_mouth",
    "strip_duplicate_greetings",
]

ReconcileAction = Literal["pass", "trim", "duplicate", "contradicted"]

_LOGGER = get_logger("StreamerAgent.MouthReconcile")

# 名字切主片段：中文连字或英文数字连串（如 "Player_abc" -> ["Player", "abc"]，"观众甲Abc" -> ["观众甲", "Abc"]）
_NAME_PART = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")

# 断句：按逗号与句末标点切开，同时保留每句末尾标点，避免半句欢迎新人与半句顺口提到已欢迎者粘连误杀
_CLAUSE_WITH_PUNCT = re.compile(r"[^，,。！？!?…~～；;\n]+[，,。！？!?…~～；;\n]*")

# 句末终止标点与孤立语气开头：剥除重复欢迎分句时修整悬空逗号与孤立叹词，保证剩余台词自然连贯
_SENTENCE_END_PUNCT = "。！？!?…~～；;"
_INTERJECTION_ONLY = frozenset({"诶", "哎", "啊", "呀", "哇", "咦", "哈", "嗯", "哦", "呐", "喂"})

# 进房/上线欢迎强特征词：无论是否正在回应观众弹幕，已欢迎过的人再次被用此类措辞欢迎都属于重复欢迎
_ARRIVAL_WELCOME_WORDS: Tuple[str, ...] = (
    "欢迎",
    "进来了",
    "刚进来",
    "上线",
    "进来坐",
    "进来别客气",
    "随便坐",
    "新来的",
    "新面孔",
    "来到直播间",
)

# 进房/上线迎客弱特征词：非回复观众弹幕时属于顺口重复迎客，但观众发弹幕寒暄时常作正常呼应，不视作重复进房欢迎
_ARRIVAL_ECHO_WORDS: Tuple[str, ...] = (
    "来啦",
    "来了",
)

# 进房/上线欢迎词全集（不含"好呀/你好/哈喽/嗨/早安/午安/晚上好"等日常问候回礼词，避免误剥对观众弹幕的正常打招呼）
_GREETING_WORDS: Tuple[str, ...] = _ARRIVAL_WELCOME_WORDS + _ARRIVAL_ECHO_WORDS

# 游戏公屏系统消息里的玩家上线提示（中英文原版提示）：提取玩家名供本场欢迎去重跟踪
_PLAYER_JOIN_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:系统消息[：:]\s*)?([A-Za-z0-9_\-一-鿿]{2,32})\s*加入了游戏\s*$"),
    re.compile(r"^(?:系统消息[：:]\s*)?([A-Za-z0-9_\-一-鿿]{2,32})\s+joined the game\.?\s*$", re.IGNORECASE),
)

_RECONCILE_SYSTEM = (
    "你是直播台词出声前的核对器。主播在十几秒前开始构思一句台词，构思期间她的即时反应通道"
    "已经先对刚发生的事说出了一两句话。请对照她刚说的话与这几秒刚发生的游戏记录，判断准备说出的这句台词该怎么处理。\n"
    "只输出下面三种格式之一，不要解释：\n"
    "1. 放行 —— 两边说的是不同的事，且这句台词没有被刚发生的新情况推翻。\n"
    "2. 重复 —— 这句台词的主要意思她刚才已经说过了（包括同一声欢迎、对同一遭遇或同一份礼物的同义反应），不必再说一遍。\n"
    "3. 作废：<一句话说明哪点过时或矛盾> —— 这句台词依赖的处境已被她刚说的话或刚发生的游戏记录推翻"
    "（例如已经换进新世界或重生上岸，台词却说还卡在门外进不去；东西已经吃掉或做完，台词却说还没吃或没拿到）。"
)


@dataclass(frozen=True, slots=True)
class SpokenEntry:
    """本场已经说出口的一句台词记录。"""

    at_ms: int
    source: Literal["mouth", "planner"]
    text: str
    trigger: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileVerdict:
    """台词出声前与即时反应对账的裁决结果。"""

    action: ReconcileAction
    speech: str = ""
    reason: str = ""
    request_id: Optional[str] = None


class SpokenLog:
    """本场两条出口共用的已说台词流水（场次边界清空）。

    即时反应单独用它保底读取规划器最近说的台词（不被高频身体事件挤掉），
    规划器用它查询本轮开窗之后即时反应插过哪些话。
    """

    def __init__(self, maxlen: int = 60) -> None:
        self._entries: Deque[SpokenEntry] = deque(maxlen=max(10, maxlen))

    def record(
        self,
        text: str,
        at_ms: int,
        *,
        source: Literal["mouth", "planner"] = "planner",
        trigger: str = "",
    ) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        # 短时间内同一出口同文不重复记（发声分发器回调与即时反应内部多行跟读记账可能先后触达同一句）
        for idx in range(len(self._entries) - 1, max(-1, len(self._entries) - 5), -1):
            existing = self._entries[idx]
            if existing.source == source and existing.text == cleaned and abs(at_ms - existing.at_ms) <= 2_000:
                if trigger and not existing.trigger:
                    self._entries[idx] = SpokenEntry(at_ms=existing.at_ms, source=source, text=cleaned, trigger=trigger)
                return
        self._entries.append(SpokenEntry(at_ms=at_ms, source=source, text=cleaned, trigger=trigger))

    def mouth_since(self, since_ms: int) -> List[SpokenEntry]:
        """返回 ``since_ms``（含）之后即时反应说出口的全部台词，按时间正序排列。"""
        return [entry for entry in self._entries if entry.source == "mouth" and entry.at_ms >= since_ms]

    def recent_texts(self, limit: int = 20) -> List[str]:
        """返回本场最近 ``limit`` 句已说台词（含规划器与即时反应，旧→新）。"""
        if limit <= 0:
            return []
        return [entry.text for entry in list(self._entries)[-limit:]]

    def reset(self) -> None:
        """场次边界清空上一场口头流水。"""
        self._entries.clear()


def extract_player_join_name(chat_body: str) -> str:
    """从游戏公屏系统消息（如"系统消息：玩家甲加入了游戏"）提取刚上线的玩家名；不匹配返回空串。"""
    text = (chat_body or "").strip()
    for pattern in _PLAYER_JOIN_PATTERNS:
        match = pattern.match(text)
        if match:
            return match.group(1).strip()
    return ""


def mentions_name(name: str, text: str) -> bool:
    """台词 ``text`` 是否点到了 ``name``（全名大小写不敏感匹配，或够长的名字主片段匹配）。

    主播口头常把带后缀的昵称叫成前半截（如 "Player_abc" 叫成 "Player"、"观众甲Abc" 叫成 "观众甲"），
    因此长度达标的汉字片段（≥2 字）与字母数字片段（≥3 字符）也视为点到名。
    """
    clean_name = (name or "").strip()
    clean_text = (text or "").strip()
    if not clean_name or not clean_text:
        return False
    lowered_text = clean_text.lower()
    if clean_name.lower() in lowered_text:
        return True
    for part in sorted(_NAME_PART.findall(clean_name), key=len, reverse=True):
        min_len = 2 if "一" <= part[0] <= "鿿" else 3
        if len(part) >= min_len and part.lower() in lowered_text:
            return True
    return False


def is_greeting_sentence(sentence: str, name: str, *, replying: bool = False) -> bool:
    """判断单句 ``sentence`` 是不是在重复欢迎 ``name`` 进房或上线（日常问候回礼与普通互动不算）。

    当 ``replying=True``（本轮正在回复该观众的弹幕/消息）时，仅识别强进房/上线欢迎词，
    把「来啦/来了」这类对观众寒暄的正常呼应也放行，避免误削回礼首句。
    """
    if not mentions_name(name, sentence):
        return False
    words = _ARRIVAL_WELCOME_WORDS if replying else _GREETING_WORDS
    return any(word in sentence for word in words)


def _is_replying_to_name(name: str, replying_names: Sequence[str]) -> bool:
    """判定已欢迎名单中的 ``name`` 是否与本轮正在回复的观众名单指向同一位观众。"""
    lowered = name.lower()
    return any(r.lower() == lowered or mentions_name(r, name) or mentions_name(name, r) for r in replying_names)


def strip_duplicate_greetings(
    speech: str,
    welcomed_names: Iterable[str],
    *,
    unwelcomed_names: Iterable[str] = (),
    replying_names: Iterable[str] = (),
) -> Tuple[str, bool]:
    """从 ``speech`` 里剥掉对本场已欢迎名单 ``welcomed_names`` 的重复进房/上线欢迎分句。

    按逗号与句末标点细分分句；若某个分句同时点到了任意一位未欢迎观众（``unwelcomed_names``），
    则绝不剥除该分句，避免同一分句合迎新人与提及已欢迎者时误杀新人欢迎；若本轮正在回应某位观众
    的弹幕（``replying_names``），日常回礼与顺口呼应（如「观众甲好呀」「观众甲来啦」）一律放行，
    仅拦下显式重复的进房/上线欢迎。

    返回 ``(剩余台词, 是否剥除过欢迎句)``：
    - 整段只有重复欢迎时，剩余台词为空串，调用方直接按重复处理不再发声；
    - 前半句顺口重复欢迎、后半句在讲新进展或欢迎新人时，只剥掉重复欢迎分句，保留其余内容出声。
    """
    names = [name.strip() for name in welcomed_names if name and name.strip()]
    unwelcomed = [name.strip() for name in unwelcomed_names if name and name.strip()]
    replying = [name.strip() for name in replying_names if name and name.strip()]
    if not names or not speech.strip():
        return speech.strip(), False
    clauses = _CLAUSE_WITH_PUNCT.findall(speech)
    if not clauses:
        return speech.strip(), False
    kept: List[str] = []
    removed = False
    for clause in clauses:
        stripped = clause.strip()
        if not stripped:
            continue
        is_dup = any(
            is_greeting_sentence(stripped, name, replying=_is_replying_to_name(name, replying)) for name in names
        )
        # 同一分句若同时点到了未欢迎观众，优先保护新人欢迎，绝不剥除该分句
        if is_dup and unwelcomed and any(mentions_name(u, stripped) for u in unwelcomed):
            is_dup = False
        if is_dup:
            removed = True
            # 若紧邻的前导分句仅是孤立单字叹词（如"诶，"），随其后被剥除的重复欢迎句一并清理
            if kept:
                prev = kept[-1]
                prev_body = prev.rstrip("，," + _SENTENCE_END_PUNCT).strip()
                prev_is_sentence_head = len(kept) == 1 or (bool(kept[-2]) and kept[-2][-1] in _SENTENCE_END_PUNCT)
                if prev_is_sentence_head and prev.endswith(("，", ",")) and prev_body in _INTERJECTION_ONLY:
                    kept.pop()
            # 若前一条保留分句以逗号收尾、而被剥除的当前分句带句末标点，把悬空逗号还原为该句末标点
            if kept and kept[-1].endswith(("，", ",")) and stripped[-1] in _SENTENCE_END_PUNCT:
                kept[-1] = kept[-1][:-1] + stripped[-1]
            continue
        kept.append(stripped)
    if not removed:
        return speech.strip(), False
    result = "".join(kept).strip()
    if not result:
        return "", True
    # 若剥除后仅剩纯标点或单字叹词，按整句剥空处理
    body_only = result.rstrip("，," + _SENTENCE_END_PUNCT).strip()
    if not body_only or body_only in _INTERJECTION_ONLY:
        return "", True
    return result, True


def _parse_reconcile_output(raw: str, working_speech: str) -> Tuple[ReconcileAction, str, str]:
    """解析对账模型的单行裁决输出，返回 ``(动作, 台词, 作废理由)``。

    契约三选一（放行 / 重复 / 作废：理由）；裁决只判不写，模型无改写分支。
    无法识别的输出保守放行。
    """
    first_line = next((line.strip() for line in (raw or "").splitlines() if line.strip()), "")
    if not first_line or first_line.startswith("放行"):
        return "pass", working_speech, ""
    if first_line.startswith("重复"):
        return "duplicate", "", "内容已被她刚才说过"
    if first_line.startswith(("作废：", "作废:")):
        _, _, reason = first_line.partition("：" if "：" in first_line[:4] else ":")
        return "contradicted", "", reason.strip() or "构思期间现场已变，原话与新事实矛盾"
    if first_line.startswith("作废"):
        return "contradicted", "", "构思期间现场已变，原话与新事实矛盾"
    _LOGGER.warning(f"对账裁决输出不符合三选一契约，保守放行: raw={first_line!r}")
    return "pass", working_speech, ""


async def reconcile_with_mouth(
    candidate: str,
    *,
    mouth_entries: Sequence[SpokenEntry],
    new_game_lines: Sequence[str] = (),
    welcomed_names: Iterable[str] = (),
    unwelcomed_names: Iterable[str] = (),
    replying_names: Iterable[str] = (),
    llm_service: Optional[Any] = None,
    profile: str = "verifier",
) -> ReconcileVerdict:
    """规划器出声前核对：剥除重复欢迎，并对照本轮开窗后即时反应说的话判断放行、重复或矛盾作废。

    ``profile`` 缺省 ``verifier``——独立核对档，缺省引用与表达档相同的模型但
    关思考（reasoning_effort 不下发），用户可换成更小的模型；换档只改
    model.toml，不动代码。
    """
    text = (candidate or "").strip()
    if not text:
        return ReconcileVerdict(action="duplicate", reason="空台词")

    # 第一道（确定性，不看即时反应本轮有没开口）：已在欢迎表里的人不再欢迎第二遍，同时保护未欢迎观众与本轮弹幕回礼
    working, stripped_greeting = strip_duplicate_greetings(
        text,
        welcomed_names,
        unwelcomed_names=unwelcomed_names,
        replying_names=replying_names,
    )
    if stripped_greeting and not working:
        _LOGGER.info(f"出声前拦下重复欢迎整句: {text!r}")
        return ReconcileVerdict(action="duplicate", reason="同一位观众或玩家本场已欢迎过")

    # 本轮开窗后即时反应没插过话：常态直接返回，零模型调用开销
    if not mouth_entries:
        if stripped_greeting:
            _LOGGER.info(f"出声前剥除重复欢迎分句: {text!r} -> {working!r}")
            return ReconcileVerdict(action="trim", speech=working)
        return ReconcileVerdict(action="pass", speech=text)

    # 第二道（确定性）：和本轮即时反应刚说出口的话近逐字重合时直接按重复省掉模型调用
    mouth_texts = [entry.text for entry in mouth_entries if entry.text]
    if find_verbatim_repeat(working, mouth_texts) is not None:
        _LOGGER.info(f"出声前命中与即时反应近逐字重复，本句不说: {working!r}")
        return ReconcileVerdict(action="duplicate", reason="与本轮即时反应刚说的话近逐字重复")

    # 第三道（语义与事实对账）：只在即时反应确实插过话的少数轮次调一次轻量表达档核对
    if llm_service is None:
        if stripped_greeting:
            return ReconcileVerdict(action="trim", speech=working)
        return ReconcileVerdict(action="pass", speech=working)

    mouth_block = "\n".join(
        f"- 即时反应说：「{entry.text}」" + (f"（触发由头：{entry.trigger}）" if entry.trigger else "")
        for entry in mouth_entries
    )
    events_block = (
        "\n【这几秒游戏里刚发生的事】\n" + "\n".join(f"- {line}" for line in list(new_game_lines)[-6:])
        if new_game_lines
        else ""
    )
    user_prompt = (
        f"【刚才已经替你说出口的话】\n{mouth_block}"
        f"{events_block}\n\n"
        f"【现在准备说出去的这一句】\n{working}\n\n"
        "请给出核对结论（放行 / 重复 / 作废：理由）："
    )
    try:
        # 出声前与即时反应对账仅做四态轻量判别，显式传低思考档位消除长思考带来的发声卡顿
        response = await llm_service.generate(
            [{"role": "user", "content": user_prompt}],
            system=_RECONCILE_SYSTEM,
            profile=profile,
            reasoning_effort="low",
        )
    except Exception as exc:  # noqa: BLE001 - 辅助核对异常不阻断正常说话
        _LOGGER.warning(f"出声前与即时反应对账调用异常，保守放行: {exc}", exc=True)
        return ReconcileVerdict(
            action="trim" if stripped_greeting else "pass",
            speech=working,
        )
    if not response.ok:
        _LOGGER.warning(f"出声前与即时反应对账返回失败，保守放行: {response.error or 'unknown'}")
        return ReconcileVerdict(
            action="trim" if stripped_greeting else "pass",
            speech=working,
            request_id=response.request_id or None,
        )

    action, final_speech, reason = _parse_reconcile_output(response.content or "", working)
    if action == "pass" and stripped_greeting:
        return ReconcileVerdict(action="trim", speech=working, request_id=response.request_id or None)
    if action != "pass":
        _LOGGER.info(
            f"出声前与即时反应对账裁决 {action}（{reason or '裁剪重复分句'}）: 原={text!r} -> 现={final_speech!r}"
        )
    return ReconcileVerdict(
        action=action,
        speech=final_speech,
        reason=reason,
        request_id=response.request_id or None,
    )
