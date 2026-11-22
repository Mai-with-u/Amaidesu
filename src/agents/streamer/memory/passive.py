"""被动想起的规则与地点两路：从档案行、承诺、老梗里找出这一批该想起来的条目。

只找候选，不管限频、用上即停、每批上限与卡面——那些归线索推送（``MemoryCueProvider``）。

- **规则一路**：触发词匹配（子串加拼音层，见 ``textmatch``），看本批观众弹幕与她最近 3 句播出的
  台词。候选是写了触发词的档案行和老梗栏里的梗；承诺还没有触发词（找承诺的提示词还没改），只走
  向量一路。命中它的来源全记下：观众弹幕、她的台词；只有她的台词命中的叫"自触发"，推送侧按更严的
  条件处理。老梗只认观众弹幕——老梗栏写明了"观众提起时才接，别自己翻出来"。
- **向量一路**：观众弹幕与游戏情境各自成一条查询（都不放她自己的台词——她的话触发语义联想会越说
  越多），在档案行与承诺里找意思相近的：余弦够高、且融合后排进前 3 才算。游戏情境查询是"存档、走到
  跟前的记住的地点、手上在做的委派"，三样变了才出一条新查询，查过一次就清掉。
- **地点一路**：角色从 48 格外（或这场还没读到过）走进某个记住的地点 24 格内，且这份距离是
  最近读到的，就算"到了"。到了以后推挂在这个地点、这个存档里的档案行；每个地点每场只算一次到达
  （她大部分时间待在营地附近，按半径持续判断会让营地的事一场推好几遍）。

候选池（写了触发词或挂了地点的档案行、老梗）每场第一次用时读库并建好匹配器，
之后每 10 分钟重读一次：整理只在场与场之间写新行，运营者在审核里改了触发词，十分钟内跟上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from src.modules.logging import get_logger
from src.modules.storage.models.memory import ArchiveLine
from src.modules.storage.privacy import mask_names_in_text
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation

from .saves import SavePlace, place_of
from .search import SearchResult
from .textmatch import KeywordMatcher, bigram_tokens, is_stopword_token, normalize

if TYPE_CHECKING:
    from src.modules.storage.repos import ArchiveRepo

__all__ = [
    "ARRIVE_WITHIN",
    "LEAVE_BEYOND",
    "CueText",
    "ItemKey",
    "PassiveHit",
    "PlaceArrivals",
    "RulePool",
    "SOURCE_DANMAKU",
    "SOURCE_GAME",
    "SOURCE_PLACE",
    "SOURCE_PLAYER",
    "SOURCE_SELF",
    "SOURCE_SITUATION",
    "SituationQuery",
    "VECTOR_MAX_QUERIES",
    "VECTOR_MIN_COSINE",
    "VECTOR_TOP",
    "intent_content_tokens",
    "merge_matched_phrases",
    "parse_inside_jokes",
    "query_worthy",
    "score_intent_keyword_match",
    "vector_hits",
    "vector_top3_summary",
]

logger = get_logger("MemoryCueProvider")

# 命中来源：观众弹幕 / 她的台词 / 走到记住的地点 / 游戏情境查询 / 游戏开着 / 游戏玩家开口或冲她做动作
SOURCE_DANMAKU = "danmaku"
SOURCE_PLAYER = "player"
SOURCE_SELF = "self"
SOURCE_PLACE = "place"
SOURCE_SITUATION = "situation"
SOURCE_GAME = "game"

# 条目键：("archive", 档案行编号) / ("commitment", 承诺编号) / ("joke", 老梗主词)
ItemKey = Tuple[str, Union[int, str]]

# 到达判定的两道距离（格）：进到 24 格内算到了；要先离开到 48 格外，下次进来才再算
ARRIVE_WITHIN = 24
LEAVE_BEYOND = 48
# 角色位置多久前读到的还算数：位置只在游戏 Agent 速览时更新，太旧的距离不判到达
SITUATION_STALE_MS = 3 * 60 * 1000
# 一个地点到达时最多推几行（新的在前）
LINES_PER_PLACE = 2
# 候选池多久重读一次、最多取多少行档案
POOL_REFRESH_MS = 10 * 60 * 1000
POOL_MAX_LINES = 500

# 向量一路：余弦至少这么高、且融合后排进前几名才推。阈值是先定的，离线评测集做出来后按
# "无关文本逐条误推不超过千分之二"校准
VECTOR_MIN_COSINE = 0.65
VECTOR_TOP = 3
# 归一化后不到 4 个字的弹幕不拿去查（"哈哈哈""666"和什么都沾一点边）
VECTOR_MIN_QUERY_CHARS = 4
# 一批最多拿几条弹幕去查（取最新的几条）
VECTOR_MAX_QUERIES = 5
# 游戏情境查询算不出向量时，最多再等几批就放弃这一条
SITUATION_QUERY_TRIES = 3
# 委派原话截多长放进游戏情境查询
_TASK_CHARS = 60
# 情境查询里玩家名换成的叫法
_PLAYER_LABEL = "玩家"

# 老梗栏一行："倒立洗头/洗头挑战：她说过输了就倒立洗头"——冒号前是梗与别名，冒号后是说明
_JOKE_HEAD = re.compile(r"[：:]")
_JOKE_ALIASES = re.compile(r"[/／、|]")

# 意图关键词联想：多字通用泛词在切分前替换为空格，避免计划外壳/寒暄/量词拼出伪重合触发无关记忆
_INTENT_GENERIC_PHRASES: Tuple[str, ...] = tuple(
    sorted(
        {
            "准备",
            "打算",
            "计划",
            "想要",
            "想去",
            "需要",
            "开始",
            "继续",
            "完成",
            "进行",
            "处理",
            "整理",
            "查看",
            "检查",
            "看看",
            "看下",
            "看一眼",
            "找找",
            "找下",
            "逛逛",
            "转转",
            "溜达",
            "休息",
            "聊聊",
            "聊天",
            "说话",
            "问问",
            "告诉",
            "帮忙",
            "帮我",
            "麻烦",
            "谢谢",
            "感谢",
            "辛苦",
            "没事",
            "没关系",
            "好的",
            "可以",
            "马上",
            "立刻",
            "待会",
            "等会",
            "等下",
            "稍等",
            "尽量",
            "争取",
            "先去",
            "回去",
            "过去",
            "过来",
            "出来",
            "进去",
            "回来",
            "拿点",
            "拿些",
            "搞点",
            "弄点",
            "做个",
            "做一个",
            "建个",
            "建一个",
            "盖个",
            "盖一个",
            "造个",
            "造一个",
            "修个",
            "修一下",
            "去找",
            "去看",
            "去拿",
            "去做",
            "凑点",
            "凑些",
            "弄一下",
            "搞一下",
            "看一下",
            "找一下",
            "问一下",
            "说一下",
            "今天",
            "明天",
            "昨天",
            "今晚",
            "昨晚",
            "上午",
            "下午",
            "晚上",
            "刚才",
            "刚刚",
            "以前",
            "之前",
            "以后",
            "之后",
            "目前",
            "现在",
            "本来",
            "原来",
            "平时",
            "经常",
            "总是",
            "一直",
            "已经",
            "正在",
            "还没",
            "还没有",
            "顺便",
            "顺路",
            "刚好",
            "恰好",
            "正好",
            "终于",
            "果然",
            "确实",
            "真的",
            "实在",
            "非常",
            "特别",
            "比较",
            "稍微",
            "差不多",
            "大概",
            "可能",
            "也许",
            "应该",
            "必须",
            "一定",
            "千万",
            "好像",
            "其实",
            "就是",
            "还是",
            "或者",
            "而且",
            "因为",
            "所以",
            "如果",
            "虽然",
            "但是",
            "不过",
            "然后",
            "接着",
            "随便",
            "顺手",
            "挺好",
            "真好",
            "不错",
            "怎么样",
            "怎样",
            "什么",
            "为什么",
            "哪儿",
            "哪里",
            "哪个",
            "哪些",
            "怎么",
            "是不是",
            "有没有",
            "能不能",
            "一个",
            "一座",
            "一条",
            "一块",
            "一堆",
            "一些",
            "一点",
            "一点点",
            "一下",
            "一会",
            "一会儿",
            "一次",
            "一趟",
            "一批",
            "一起",
            "一共",
            "全部",
            "所有",
            "很多",
            "不少",
            "几个",
            "几块",
            "几组",
            "东西",
            "事情",
            "地方",
            "位置",
            "附近",
            "旁边",
            "周围",
            "前面",
            "后面",
            "左边",
            "右边",
            "上面",
            "下面",
            "里边",
            "外边",
            "里面",
            "外面",
            "这边",
            "那边",
            "这里",
            "那里",
            "别人",
            "大家",
            "咱们",
            "自己",
            "我们",
            "你们",
            "他们",
            "她们",
            "那个",
            "这个",
            "这些",
            "那些",
            "同伴",
            "朋友",
            "玩家",
            "队友",
            "主播",
            "观众",
            "直播",
            "直播间",
            "游戏",
            "存档",
            "世界",
            "服务器",
            "现成",
            "已有",
            "天气",
            # 日常口头禅、基础采集动作与高频背景泛词（防无向量兜底时误触发无关记录）
            "知道",
            "没问题",
            "问题",
            "明白",
            "收到",
            "等等",
            "不管",
            "小心点",
            "小心",
            "算了",
            "行吧",
            "好吧",
            "走吧",
            "出发",
            "不用",
            "不要",
            "别急",
            "挖点",
            "挖些",
            "挖矿",
            "砍点",
            "砍些",
            "砍树",
            "采点",
            "采些",
            "找点",
            "找些",
            "带点",
            "带些",
            "放点",
            "留点",
            "石头",
            "木头",
            "泥土",
            "材料",
            "资源",
            "工具",
            "装备",
            "背包",
            "身上",
            "营地",
            "基地",
            "家里",
            "老家",
            "门口",
            "路边",
            "山脚",
            "山上",
            "山下",
            "河边",
            "水边",
            "边上",
        },
        key=len,
        reverse=True,
    )
)
_INTENT_GENERIC_PHRASES_RE = re.compile("|".join(re.escape(phrase) for phrase in _INTENT_GENERIC_PHRASES))
# 句法助词、语气词与人称代词边界：切分前作分隔符，防止跨虚字粘出伪二元组
_INTENT_SPLIT_CHARS_RE = re.compile(r"[的了吗呢吧啊呀啦喔哦噢哈嘿诶唉嗯哦我你他她它们把被给让和跟与着过]")
_INTENT_GENERIC_EN_WORDS: frozenset[str] = frozenset(
    (
        "going get got make making build building fix fixing check checking look looking find finding "
        "need want plan ready try trying help please thanks thank okay good nice great just some more "
        "back here there near around stuff thing things item items player players game world server hello"
    ).split()
)
# 仅在两段文本只命中单个两字汉字二元组时拦截的边缘虚/泛动词，防"修好""放到""去拿"之类误触发
_WEAK_EDGE_CJK_CHARS: frozenset[str] = frozenset(
    "在是有就都也还又去来做弄搞拿放到往向从看找说想要能会好完掉着过不没再先刚太很挺真这那一二三两几"
)
_CJK_TWO_CHAR_RE = re.compile(r"^[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]{2}$")


def intent_content_tokens(text: str) -> List[str]:
    """提取用于意图关键词联想的高纯度实词（去重保序，剥离泛词、单字与纯数字）。"""
    raw = str(text or "").strip()
    if not raw:
        return []
    stripped = _INTENT_SPLIT_CHARS_RE.sub(" ", _INTENT_GENERIC_PHRASES_RE.sub(" ", raw))
    tokens: List[str] = []
    seen: Set[str] = set()
    for token in bigram_tokens(stripped):
        if token in seen or is_stopword_token(token):
            continue
        if _CJK_TWO_CHAR_RE.match(token):
            seen.add(token)
            tokens.append(token)
            continue
        # 英文与混合实词要求至少 3 字符且非纯数字，过滤单字母与泛英文寒暄词
        if len(token) < 3 or token.isdigit() or token in _INTENT_GENERIC_EN_WORDS:
            continue
        if token.isascii():
            seen.add(token)
            tokens.append(token)
    return tokens


def _is_weak_cjk_bigram(token: str) -> bool:
    """两字汉字二元组若首字或尾字为高频泛动词/虚字，单独命中时不足以作为触发依据。"""
    return bool(_CJK_TWO_CHAR_RE.match(token)) and (
        token[0] in _WEAK_EDGE_CJK_CHARS or token[1] in _WEAK_EDGE_CJK_CHARS
    )


def merge_matched_phrases(tokens: Sequence[str]) -> Tuple[str, ...]:
    """把相连重叠的汉字二元组还原为自然词组（如 ('下界', '界门') -> ('下界门',)），供卡面展示。"""
    phrases: List[str] = []
    for token in tokens:
        if (
            phrases
            and _CJK_TWO_CHAR_RE.match(token)
            and len(phrases[-1]) >= 2
            and not phrases[-1].isascii()
            and phrases[-1][-1] == token[0]
        ):
            phrases[-1] = phrases[-1] + token[1]
        elif token not in phrases:
            phrases.append(token)
    return tuple(phrases)


def score_intent_keyword_match(query: str, document: str) -> Tuple[float, Tuple[str, ...]]:
    """计算意图文本与候选记忆正文的实质关键词重合得分与命中词组；未达门槛返回 (0.0, ())。

    防误触发门禁：
    - 单字与通用计划外壳/寒暄词不参与匹配；
    - 仅命中单个 2 字汉字词时，首尾均不得为高频泛动词/虚字；
    - 命中连续重叠二元组（对应 3+ 字实体词）或多项强实词给予显著高分。
    """
    q_tokens = intent_content_tokens(query)
    if not q_tokens:
        return 0.0, ()
    d_tokens = intent_content_tokens(document)
    if not d_tokens:
        return 0.0, ()
    d_set = set(d_tokens)
    matched = [token for token in q_tokens if token in d_set]
    if not matched:
        return 0.0, ()

    if len(matched) == 1:
        only = matched[0]
        if _is_weak_cjk_bigram(only):
            return 0.0, ()
        base = 1.2 if only.isascii() and len(only) >= 4 else 1.0
        return base, (only,)

    q_norm = normalize(query)
    d_norm = normalize(document)
    strong_count = sum(1 for token in matched if not _is_weak_cjk_bigram(token))
    chained_pairs = 0
    for left, right in zip(matched, matched[1:], strict=False):
        if _CJK_TWO_CHAR_RE.match(left) and _CJK_TWO_CHAR_RE.match(right) and left[1] == right[0]:
            trigram = left + right[1]
            if trigram in q_norm and trigram in d_norm:
                chained_pairs += 1

    if strong_count == 0 and chained_pairs == 0:
        return 0.0, ()

    score = float(len(matched)) + chained_pairs * 1.5 + strong_count * 0.5
    return score, merge_matched_phrases(matched)


def parse_inside_jokes(text: str) -> Dict[str, List[str]]:
    """老梗栏按行拆成触发词：冒号前用 / 分开的是梗与别名（第一个是主词），没有冒号的整行当主词。"""
    jokes: Dict[str, List[str]] = {}
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•*").strip()
        if not line:
            continue
        head = _JOKE_HEAD.split(line, maxsplit=1)[0]
        words = [word.strip() for word in _JOKE_ALIASES.split(head) if word.strip()]
        if words and words[0] not in jokes:
            jokes[words[0]] = words
    return jokes


def query_worthy(text: str) -> bool:
    """这条弹幕值得拿去做语义查询：归一化后至少 4 个字。"""
    return len(normalize(text)) >= VECTOR_MIN_QUERY_CHARS


@dataclass(frozen=True)
class CueText:
    """规则一路看的一段文本：观众弹幕（带弹幕编号）或她最近播出的一句台词。"""

    source: str
    text: str
    message_id: str = ""


@dataclass
class PassiveHit:
    """一条被想起的候选：条目、命中它的全部来源与依据。

    ``message_id`` 是第一条命中它的观众弹幕，只给卡面点名用，不进线索卡记录（记录不带观众身份）。
    """

    key: ItemKey
    sources: Set[str] = field(default_factory=set)
    keywords: List[str] = field(default_factory=list)
    # 命中的触发词在条目触发词表里排第几（从 0 起）：线索卡记录只存这个，不存原词
    keyword_indexes: List[int] = field(default_factory=list)
    hows: List[str] = field(default_factory=list)
    message_id: str = ""
    place: str = ""
    distance: Optional[int] = None
    cosine: Optional[float] = None
    vector_rank: Optional[int] = None
    fused: Optional[float] = None
    top3: List[Dict[str, Any]] = field(default_factory=list)
    best_query_index: Optional[int] = None

    @property
    def self_only(self) -> bool:
        """只由她自己的台词命中（防打转：推送侧对它更严）。"""
        return self.sources == {SOURCE_SELF}


class RulePool:
    """规则与地点两路的候选池：档案行、老梗与它们的触发词匹配器。"""

    def __init__(self, *, archive_repo: Optional["ArchiveRepo"], inside_jokes: str, clock: Callable[[], int]) -> None:
        self._archive = archive_repo
        self._jokes = parse_inside_jokes(inside_jokes)
        self._clock = clock
        self._loaded_at: Optional[int] = None
        self._lines: Dict[int, ArchiveLine] = {}
        self._session_started_ms: Dict[int, int] = {}
        self._entries: Dict[ItemKey, List[str]] = {}
        self._matcher: KeywordMatcher[ItemKey] = KeywordMatcher({})

    def reset(self) -> None:
        """换场时作废，下一次用时重读。"""
        self._loaded_at = None

    async def refresh_if_stale(self) -> None:
        """这场还没读过、或上次读已过十分钟，就重读候选池；读失败沿用上一份。"""
        now = int(self._clock())
        if self._loaded_at is not None and now - self._loaded_at < POOL_REFRESH_MS:
            return
        self._loaded_at = now
        try:
            lines = await self._archive.list_cue_lines(limit=POOL_MAX_LINES) if self._archive is not None else []
            bounds_fn = (
                getattr(self._archive, "list_archived_session_bounds", None) if self._archive is not None else None
            )
            session_bounds = await bounds_fn(limit=POOL_MAX_LINES) if callable(bounds_fn) else []
        except Exception as exc:  # noqa: BLE001 - 读不到候选池就沿用上一份，不挡决策
            logger.warning(f"读被动想起的候选池失败（沿用上一份）: {exc}", exc=exc)
            return
        self._lines = {int(line.id): line for line in lines}
        self._session_started_ms = {int(sid): int(started_ms) for sid, started_ms in session_bounds}
        entries: Dict[ItemKey, List[str]] = {}
        for line in self._lines.values():
            if line.keywords:
                entries[("archive", int(line.id))] = list(line.keywords)
        for main, words in self._jokes.items():
            entries[("joke", main)] = words
        self._entries = entries
        self._matcher = KeywordMatcher(entries)

    def archive_line(self, line_id: int) -> Optional[ArchiveLine]:
        return self._lines.get(int(line_id))

    def session_started_at_ms(self, session_id: int) -> Optional[int]:
        """某档案行所属场次的开播时刻（判自触发是否来自两场以前用）；未知返回 None。"""
        return self._session_started_ms.get(int(session_id))

    def joke_index(self, main: str) -> Optional[int]:
        """老梗在老梗栏里排第几条（从 0 起）；不在栏里返回 None。"""
        for index, name in enumerate(self._jokes):
            if name == main:
                return index
        return None

    def lines_at(self, game: str, place: str, current_saves: Mapping[str, GameSave]) -> List[ArchiveLine]:
        """挂在这个游戏、这个地点上的档案行（新的在前）：只取眼下这个存档里的，或这个游戏不分存档。"""
        return [
            line
            for line in self._lines.values()
            if line.place == place
            and line.game == game
            and place_of(line.game, line.save_id, current_saves) in (SavePlace.SAME, SavePlace.UNSPLIT)
        ][:LINES_PER_PLACE]

    def match(self, texts: Sequence[CueText]) -> List[PassiveHit]:
        """逐段文本匹配触发词，同一条目合并成一条候选；老梗只收观众弹幕命中的。"""
        hits: Dict[ItemKey, PassiveHit] = {}
        for item in texts:
            for found in self._matcher.match(item.text):
                if found.key[0] == "joke" and item.source != SOURCE_DANMAKU:
                    continue
                hit = hits.setdefault(found.key, PassiveHit(key=found.key))
                hit.sources.add(item.source)
                if found.keyword not in hit.keywords:
                    hit.keywords.append(found.keyword)
                    words = self._entries.get(found.key, [])
                    if found.keyword in words:
                        hit.keyword_indexes.append(words.index(found.keyword))
                if found.how not in hit.hows:
                    hit.hows.append(found.how)
                if item.source == SOURCE_DANMAKU and not hit.message_id:
                    hit.message_id = item.message_id
        # 观众弹幕命中的排在只由她自己台词命中的前面
        return sorted(hits.values(), key=lambda hit: hit.self_only)


def vector_top3_summary(result: SearchResult) -> List[Dict[str, Any]]:
    """提取一次向量查询前 3 名的条目编号与分数（不带原词和观众身份），供线索记录留痕校准阈值。"""
    top: List[Dict[str, Any]] = []
    for found in result.hits[:VECTOR_TOP]:
        if found.source not in ("archive", "commitment"):
            continue
        top.append(
            {
                "kind": found.source,
                "id": int(found.source_id),
                "cosine": round(found.vector_score, 4) if found.vector_score is not None else None,
                "rank": found.vector_rank,
                "fused": round(found.score, 5),
            }
        )
    return top


def vector_hits(
    results: Sequence[Tuple[str, str, SearchResult]],
    *,
    allowed_sources: Sequence[str] = ("archive", "commitment"),
) -> List[PassiveHit]:
    """各条查询（来源, 弹幕编号, 检索结果）合成候选：前几名里余弦够高的指定来源条目。

    同一条目被几条查询找到时合并来源、留最高的余弦及对应查询的前三名分数；按余弦从高到低排。
    """
    allowed = set(allowed_sources)
    hits: Dict[ItemKey, PassiveHit] = {}
    for query_idx, (source, message_id, result) in enumerate(results):
        top3 = vector_top3_summary(result)
        for found in result.hits[:VECTOR_TOP]:
            if found.source not in allowed:
                continue
            if found.vector_score is None or found.vector_score < VECTOR_MIN_COSINE:
                continue
            key: ItemKey = (found.source, int(found.source_id))
            hit = hits.setdefault(key, PassiveHit(key=key))
            hit.sources.add(source)
            if source == SOURCE_DANMAKU and not hit.message_id:
                hit.message_id = message_id
            if hit.cosine is None or found.vector_score > hit.cosine:
                hit.cosine, hit.vector_rank, hit.fused = found.vector_score, found.vector_rank, found.score
                hit.top3 = top3
                hit.best_query_index = query_idx
    return sorted(hits.values(), key=lambda hit: -(hit.cosine or 0.0))


class SituationQuery:
    """游戏情境查询：眼下的存档、走到跟前的记住的地点、手上在做的委派；三样变了才出一条新查询。

    查询文本在变的那一刻定下，算出向量、查过一次就清掉：同一个情境不反复推，压住"玩着就一直
    想起"。算不出向量的，最多再等几批。查询要发给嵌入服务：委派原话里已知的玩家名（身边的玩家、
    发过弹幕的观众、游戏里说过话的人）换成"玩家"；地点名是她起的，照发。
    """

    def __init__(self) -> None:
        self._key: Optional[Tuple[Tuple[str, str, str, str], ...]] = None
        self._pending: Optional[str] = None
        self._tries = 0

    def reset(self) -> None:
        self._key = None
        self._pending = None
        self._tries = 0

    @property
    def pending(self) -> Optional[str]:
        """等着去查的那条情境查询；没有为 None。"""
        return self._pending

    def update(
        self,
        situations: Mapping[str, GameSituation],
        current_saves: Mapping[str, GameSave],
        tasks: Mapping[str, str],
        names: Iterable[str] = (),
    ) -> None:
        """读这一批的情境；和上一批比变了，就定下一条新查询（没有地点也没有委派时不查）。

        ``names`` 是已知的玩家名：先遮名再截短，免得名字被截成半截漏出去。
        """
        known = list(names)
        key: List[Tuple[str, str, str, str]] = []
        parts: List[str] = []
        for game in sorted(set(situations) | set(tasks)):
            save = current_saves.get(game)
            save_known = save is not None and save.known
            situation = situations.get(game)
            near = ""
            if situation is not None and situation.age_ms <= SITUATION_STALE_MS:
                near = next((place.name for place in situation.places if place.distance <= ARRIVE_WITHIN), "")
            task = mask_names_in_text((tasks.get(game) or "").strip(), known, _PLAYER_LABEL)[:_TASK_CHARS]
            key.append((game, save.id if save_known and save is not None else "", near, task))
            if near or task:
                where = f" 存档「{save.label}」" if save_known and save is not None else ""
                at = f" 在「{near}」附近" if near else ""
                doing = f" 正在做：{task}" if task else ""
                parts.append(f"{game}{where}{at}{doing}")
        frozen = tuple(key)
        if frozen == self._key:
            return
        self._key = frozen
        self._pending = "；".join(parts) or None
        self._tries = 0

    def done(self) -> None:
        """查过了，清掉。"""
        self._pending = None

    def missed(self) -> None:
        """这一批向量还没算好；等满几批还没好就放弃这一条。"""
        self._tries += 1
        if self._tries >= SITUATION_QUERY_TRIES:
            self._pending = None


class PlaceArrivals:
    """地点到达判定：每个（游戏, 存档, 地点）记上一次读到的距离，本场到过的地点不再算。

    只认"真的从 48 格外走进 24 格内"。之前在哪不清楚的——开播后、进程重启后、换存档后第一次读到——
    只记下眼下的距离，不算到达：她几乎每场都从营地开播，第一次读到就算到达等于每场开头固定念一遍营地。
    """

    def __init__(self) -> None:
        self._last: Dict[Tuple[str, str, str], int] = {}
        self._arrived: Set[Tuple[str, str, str]] = set()
        # 每个游戏上一次有效读数：（存档编号, 那次列出的最远地点的距离）；没有就是之前在哪不清楚
        self._previous: Dict[str, Tuple[str, int]] = {}

    def reset(self) -> None:
        self._last.clear()
        self._arrived.clear()
        self._previous.clear()

    def update(
        self, situations: Mapping[str, GameSituation], current_saves: Mapping[str, GameSave]
    ) -> List[Tuple[str, str, int]]:
        """读一次各游戏的情境，返回这一批刚到达的（游戏, 地点, 距离）。

        距离太旧的游戏这一批不判，之前记下的距离作废。读数只列最近的几个地点：上一次没列出的地点，
        只有那次列到的最远一个也在 48 格外时才能断定它当时在 48 格外；否则当"之前在哪不清楚"。
        """
        arrivals: List[Tuple[str, str, int]] = []
        for game, situation in situations.items():
            if situation.age_ms > SITUATION_STALE_MS:
                # 位置太旧：这段时间她可能走到了任何地方，之前记下的作废，下一次新读数只记下来
                self._forget_game(game)
                continue
            save = current_saves.get(game)
            save_id = save.id if save is not None and save.known else ""
            previous = self._previous.get(game)
            # 同一存档上一次读到过，且那次列出的最远地点都在 48 格外：没列出的地点当时一定更远
            unlisted_was_far = previous is not None and previous[0] == save_id and previous[1] > LEAVE_BEYOND
            seen: Dict[Tuple[str, str, str], int] = {}
            for place in situation.places:
                key = (game, save_id, place.name)
                seen[key] = int(place.distance)
                before = self._last.get(key)
                was_far = before > LEAVE_BEYOND if before is not None else unlisted_was_far
                if place.distance <= ARRIVE_WITHIN and was_far and key not in self._arrived:
                    self._arrived.add(key)
                    arrivals.append((game, place.name, int(place.distance)))
            for key in [key for key in self._last if key[0] == game and key not in seen]:
                del self._last[key]
            self._last.update(seen)
            self._previous[game] = (save_id, max((int(place.distance) for place in situation.places), default=0))
        return arrivals

    def claim_arrived(
        self,
        game: str,
        place: str,
        current_saves: Mapping[str, GameSave],
    ) -> bool:
        """登记游戏主控侧已本地确认到达的记住地标（同场同存档同地点仅放行一次，与距离滞回共享去重集）。"""
        normalized_game = (game or "").strip()
        normalized_place = (place or "").strip()
        if not normalized_game or not normalized_place:
            return False
        save = current_saves.get(normalized_game)
        save_id = save.id if save is not None and save.known else ""
        key = (normalized_game, save_id, normalized_place)
        if key in self._arrived:
            return False
        self._arrived.add(key)
        return True

    def _forget_game(self, game: str) -> None:
        """丢掉一个游戏记下的距离：之后第一次新读数当"之前在哪不清楚"。"""
        self._previous.pop(game, None)
        for key in [key for key in self._last if key[0] == game]:
            del self._last[key]
