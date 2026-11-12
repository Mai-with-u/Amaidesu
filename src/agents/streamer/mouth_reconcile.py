"""嘴与决策环节的对账件（Agent 内部件，代码直连，不注册为工具）。

解决的问题：
决策环节从开窗读快照到真正张嘴出声通常隔十多秒，这期间"嘴"可能已经对刚发生的游戏事件
当场接了一句。两条出口互不知情时会出现三类穿帮：
1. 重复欢迎：同一个人进直播间或上线进游戏，嘴刚欢迎完，决策环节二十秒后又欢迎一遍；
2. 重复反应：同一场遭遇或送礼，嘴刚接完话，决策环节又把同一件事讲一遍；
3. 相邻矛盾：决策开窗时还在旧现场（如掉线卡在门外、东西还没吃），出声前游戏已切入新世界
   且嘴已说出"换到新世界站岸边了"，决策台词却接着说"还卡在门口进不去"。

本模块维护本场口头流水（区分嘴与决策），并在决策台词出声前做对账：
- 本场已欢迎过的人再次被欢迎时，确定性剥掉重复欢迎句（只剩欢迎则整条不说，后面还有新进展则只留新进展）；
- 本轮开窗后嘴没说过话时，不做任何模型调用，零额外时延放行；
- 本轮开窗后嘴说过话时，用轻量表达档核对一次：内容已被嘴说过的直接不说或删掉重复句，
  被嘴与期间新游戏事件推翻的整句作废（交给决策层按新现场重想一步，或静默撤回）。
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

# 断句：按句末标点切开，同时保留每句末尾标点以便删掉重复欢迎半句后自然拼接剩余句子
_CLAUSE_WITH_PUNCT = re.compile(r"[^。！？!?…~～；;\n]+[。！？!?…~～；;\n]*")

# 欢迎招呼特征词：只有点到名字且同时出现招呼语的句子才算"欢迎句"，普通对话喊对方名字不算欢迎
_GREETING_WORDS: Tuple[str, ...] = (
    "欢迎",
    "来啦",
    "来了",
    "进来了",
    "上线",
    "进来坐",
    "进来别客气",
    "随便坐",
    "新来的",
    "新面孔",
    "好呀",
    "你好",
    "哈喽",
    "嗨",
    "早安",
    "午安",
    "晚上好",
)

# 游戏公屏系统消息里的玩家上线提示（中英文原版提示）：提取玩家名供本场欢迎去重跟踪
_PLAYER_JOIN_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?:系统消息[：:]\s*)?([A-Za-z0-9_\-一-鿿]{2,32})\s*加入了游戏\s*$"),
    re.compile(r"^(?:系统消息[：:]\s*)?([A-Za-z0-9_\-一-鿿]{2,32})\s+joined the game\.?\s*$", re.IGNORECASE),
)

# 保留句忠实度核查用的词元提取：防止轻量模型在"保留："分支夹带原台词没有的新词
_WORD_CHAR = re.compile(r"[a-z0-9]+|[一-鿿]")

_RECONCILE_SYSTEM = (
    "你是直播台词出声前的核对器。主播在十几秒前开始构思一句台词，构思期间她的快反应通道（嘴）"
    "已经先对刚发生的事说出了一两句话。请对照嘴刚说的话与这几秒刚发生的游戏记录，判断准备说出的这句台词该怎么处理。\n"
    "只输出下面四种格式之一，不要解释：\n"
    "1. 放行 —— 两边说的是不同的事，且这句台词没有被刚发生的新情况推翻。\n"
    "2. 重复 —— 这句台词的主要意思嘴刚才已经说过了（包括同一声欢迎、对同一遭遇或同一份礼物的同义反应），不必再说一遍。\n"
    "3. 保留：<删掉重复那一句后剩下的原话> —— 这句台词里有一句嘴刚说过，但其余句子是嘴没提过的新内容且没过时；"
    "只从原话里删掉重复的那一句，其余字句原样保留，不添新词。\n"
    "4. 作废：<一句话说明哪点过时或矛盾> —— 这句台词依赖的处境已被嘴刚说的话或刚发生的游戏记录推翻"
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
    """决策台词出声前与嘴对账的裁决结果。"""

    action: ReconcileAction
    speech: str = ""
    reason: str = ""
    request_id: Optional[str] = None


class SpokenLog:
    """本场两条出口共用的已说台词流水（场次边界清空）。

    嘴单独用它保底读取决策环节最近说的台词（不被高频身体事件挤掉），
    决策环节用它查询本轮开窗之后嘴插过哪些话。
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
        # 短时间内同一出口同文不重复记（发声分发器回调与嘴内部多行跟读记账可能先后触达同一句）
        for idx in range(len(self._entries) - 1, max(-1, len(self._entries) - 5), -1):
            existing = self._entries[idx]
            if existing.source == source and existing.text == cleaned and abs(at_ms - existing.at_ms) <= 2_000:
                if trigger and not existing.trigger:
                    self._entries[idx] = SpokenEntry(at_ms=existing.at_ms, source=source, text=cleaned, trigger=trigger)
                return
        self._entries.append(SpokenEntry(at_ms=at_ms, source=source, text=cleaned, trigger=trigger))

    def mouth_since(self, since_ms: int) -> List[SpokenEntry]:
        """返回 ``since_ms``（含）之后嘴说出口的全部台词，按时间正序排列。"""
        return [entry for entry in self._entries if entry.source == "mouth" and entry.at_ms >= since_ms]

    def recent_texts(self, limit: int = 20) -> List[str]:
        """返回本场最近 ``limit`` 句已说台词（含决策与嘴，旧→新）。"""
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


def is_greeting_sentence(sentence: str, name: str) -> bool:
    """判断单句 ``sentence`` 是不是在欢迎 ``name`` 进房或上线（普通互动叫名字不算欢迎）。"""
    if not mentions_name(name, sentence):
        return False
    return any(word in sentence for word in _GREETING_WORDS)


def strip_duplicate_greetings(speech: str, welcomed_names: Iterable[str]) -> Tuple[str, bool]:
    """从 ``speech`` 里剥掉对本场已欢迎名单 ``welcomed_names`` 的重复欢迎句。

    返回 ``(剩余台词, 是否剥除过欢迎句)``：
    - 整段只有重复欢迎时，剩余台词为空串，调用方直接按重复处理不再发声；
    - 前半句顺口重复欢迎、后半句在讲新的游戏进展时，只剥掉前半句欢迎，保留后半句出声。
    """
    names = [name.strip() for name in welcomed_names if name and name.strip()]
    if not names or not speech.strip():
        return speech.strip(), False
    clauses = _CLAUSE_WITH_PUNCT.findall(speech)
    if not clauses:
        return speech.strip(), False
    kept: List[str] = []
    removed = False
    for clause in clauses:
        if any(is_greeting_sentence(clause, name) for name in names):
            removed = True
            continue
        kept.append(clause.strip())
    if not removed:
        return speech.strip(), False
    return "".join(kept).strip(), True


def _is_faithful_substring(kept: str, original: str) -> bool:
    """核查轻量模型返回的"保留："文本确实来自原台词，防止模型借裁剪顺手编造新词。"""
    kept_clean = kept.strip()
    if not kept_clean or len(kept_clean) > len(original.strip()):
        return False
    if kept_clean in original:
        return True
    orig_tokens = set(_WORD_CHAR.findall(original.lower()))
    kept_tokens = _WORD_CHAR.findall(kept_clean.lower())
    if not kept_tokens:
        return False
    covered = sum(1 for token in kept_tokens if token in orig_tokens)
    return covered / len(kept_tokens) >= 0.85


def _parse_reconcile_output(raw: str, working_speech: str) -> Tuple[ReconcileAction, str, str]:
    """解析轻量对账模型的单行裁决输出，返回 ``(动作, 保留台词, 作废理由)``。"""
    first_line = next((line.strip() for line in (raw or "").splitlines() if line.strip()), "")
    if not first_line or first_line.startswith("放行"):
        return "pass", working_speech, ""
    if first_line.startswith("重复"):
        return "duplicate", "", "内容已被嘴刚才说过"
    if first_line.startswith(("作废：", "作废:")):
        _, _, reason = first_line.partition("：" if "：" in first_line[:4] else ":")
        return "contradicted", "", reason.strip() or "构思期间现场已变，原话与新事实矛盾"
    if first_line.startswith("作废"):
        return "contradicted", "", "构思期间现场已变，原话与新事实矛盾"
    if first_line.startswith(("保留：", "保留:")):
        _, _, rest = first_line.partition("：" if "：" in first_line[:4] else ":")
        kept = rest.strip().strip("「」\"'")
        if not kept:
            return "duplicate", "", "删掉重复句后没有剩余内容"
        if _is_faithful_substring(kept, working_speech):
            return "trim", kept, ""
        _LOGGER.debug(f"对账保留句超出原台词字面范围，忽略改写按原句放行: kept={kept!r}")
    return "pass", working_speech, ""


async def reconcile_with_mouth(
    candidate: str,
    *,
    mouth_entries: Sequence[SpokenEntry],
    new_game_lines: Sequence[str] = (),
    welcomed_names: Iterable[str] = (),
    llm_service: Optional[Any] = None,
    profile: str = "replyer",
) -> ReconcileVerdict:
    """决策环节出声前核对：剥除重复欢迎，并对照本轮开窗后嘴说的话判断放行、裁剪、重复或矛盾作废。"""
    text = (candidate or "").strip()
    if not text:
        return ReconcileVerdict(action="duplicate", reason="空台词")

    # 第一道（确定性，不看嘴本轮有没开口）：已在欢迎表里的人不再欢迎第二遍
    working, stripped_greeting = strip_duplicate_greetings(text, welcomed_names)
    if stripped_greeting and not working:
        _LOGGER.info(f"出声前拦下重复欢迎整句: {text!r}")
        return ReconcileVerdict(action="duplicate", reason="同一位观众或玩家本场已欢迎过")

    # 本轮开窗后嘴没插过话：常态直接返回，零模型调用开销
    if not mouth_entries:
        if stripped_greeting:
            _LOGGER.info(f"出声前剥除重复欢迎分句: {text!r} -> {working!r}")
            return ReconcileVerdict(action="trim", speech=working)
        return ReconcileVerdict(action="pass", speech=text)

    # 第二道（确定性）：和本轮嘴刚说出口的话近逐字重合时直接按重复省掉模型调用
    mouth_texts = [entry.text for entry in mouth_entries if entry.text]
    if find_verbatim_repeat(working, mouth_texts) is not None:
        _LOGGER.info(f"出声前命中与嘴近逐字重复，本句不说: {working!r}")
        return ReconcileVerdict(action="duplicate", reason="与本轮嘴刚说的话近逐字重复")

    # 第三道（语义与事实对账）：只在嘴确实插过话的少数轮次调一次轻量表达档核对
    if llm_service is None:
        if stripped_greeting:
            return ReconcileVerdict(action="trim", speech=working)
        return ReconcileVerdict(action="pass", speech=working)

    mouth_block = "\n".join(
        f"- 嘴说：「{entry.text}」" + (f"（触发由头：{entry.trigger}）" if entry.trigger else "")
        for entry in mouth_entries
    )
    events_block = (
        "\n【这几秒游戏里刚发生的事】\n" + "\n".join(f"- {line}" for line in list(new_game_lines)[-6:])
        if new_game_lines
        else ""
    )
    user_prompt = (
        f"【刚才嘴已经替你说出口的话】\n{mouth_block}"
        f"{events_block}\n\n"
        f"【现在准备说出去的这一句】\n{working}\n\n"
        "请给出核对结论（放行 / 重复 / 保留：… / 作废：…）："
    )
    try:
        response = await llm_service.generate(
            [{"role": "user", "content": user_prompt}],
            system=_RECONCILE_SYSTEM,
            profile=profile,
        )
    except Exception as exc:  # noqa: BLE001 - 辅助核对异常不阻断正常说话
        _LOGGER.warning(f"出声前与嘴对账调用异常，保守放行: {exc}", exc=True)
        return ReconcileVerdict(
            action="trim" if stripped_greeting else "pass",
            speech=working,
        )
    if not response.ok:
        _LOGGER.warning(f"出声前与嘴对账返回失败，保守放行: {response.error or 'unknown'}")
        return ReconcileVerdict(
            action="trim" if stripped_greeting else "pass",
            speech=working,
            request_id=response.request_id or None,
        )

    action, final_speech, reason = _parse_reconcile_output(response.content or "", working)
    if action == "pass" and stripped_greeting:
        return ReconcileVerdict(action="trim", speech=working, request_id=response.request_id or None)
    if action != "pass":
        _LOGGER.info(f"出声前与嘴对账裁决 {action}（{reason or '裁剪重复分句'}）: 原={text!r} -> 现={final_speech!r}")
    return ReconcileVerdict(
        action=action,
        speech=final_speech,
        reason=reason,
        request_id=response.request_id or None,
    )
