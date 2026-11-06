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
from typing import TYPE_CHECKING, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from src.modules.logging import get_logger
from src.modules.storage.models.memory import ArchiveLine
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation

from .saves import SavePlace, place_of
from .search import SearchResult
from .textmatch import KeywordMatcher, normalize

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
    "SOURCE_SELF",
    "SOURCE_SITUATION",
    "SituationQuery",
    "VECTOR_MAX_QUERIES",
    "VECTOR_MIN_COSINE",
    "VECTOR_TOP",
    "parse_inside_jokes",
    "query_worthy",
    "vector_hits",
]

logger = get_logger("MemoryCueProvider")

# 命中来源：观众弹幕 / 她的台词 / 走到记住的地点 / 游戏情境查询 / 游戏开着
SOURCE_DANMAKU = "danmaku"
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

# 老梗栏一行："倒立洗头/洗头挑战：她说过输了就倒立洗头"——冒号前是梗与别名，冒号后是说明
_JOKE_HEAD = re.compile(r"[：:]")
_JOKE_ALIASES = re.compile(r"[/／、|]")


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
    hows: List[str] = field(default_factory=list)
    message_id: str = ""
    place: str = ""
    distance: Optional[int] = None
    cosine: Optional[float] = None
    vector_rank: Optional[int] = None
    fused: Optional[float] = None

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
        except Exception as exc:  # noqa: BLE001 - 读不到候选池就沿用上一份，不挡决策
            logger.warning(f"读被动想起的候选池失败（沿用上一份）: {exc}", exc=exc)
            return
        self._lines = {int(line.id): line for line in lines}
        entries: Dict[ItemKey, List[str]] = {}
        for line in self._lines.values():
            if line.keywords:
                entries[("archive", int(line.id))] = list(line.keywords)
        for main, words in self._jokes.items():
            entries[("joke", main)] = words
        self._matcher = KeywordMatcher(entries)

    def archive_line(self, line_id: int) -> Optional[ArchiveLine]:
        return self._lines.get(int(line_id))

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
                if found.how not in hit.hows:
                    hit.hows.append(found.how)
                if item.source == SOURCE_DANMAKU and not hit.message_id:
                    hit.message_id = item.message_id
        # 观众弹幕命中的排在只由她自己台词命中的前面
        return sorted(hits.values(), key=lambda hit: hit.self_only)


def vector_hits(results: Sequence[Tuple[str, str, SearchResult]]) -> List[PassiveHit]:
    """各条查询（来源, 弹幕编号, 检索结果）合成候选：前几名里余弦够高的档案行与承诺。

    同一条目被几条查询找到时合并来源、留最高的余弦；按余弦从高到低排。
    """
    hits: Dict[ItemKey, PassiveHit] = {}
    for source, message_id, result in results:
        for found in result.hits[:VECTOR_TOP]:
            if found.source not in ("archive", "commitment"):
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
    return sorted(hits.values(), key=lambda hit: -(hit.cosine or 0.0))


class SituationQuery:
    """游戏情境查询：眼下的存档、走到跟前的记住的地点、手上在做的委派；三样变了才出一条新查询。

    查询文本在变的那一刻定下，算出向量、查过一次就清掉：同一个情境不反复推，压住"玩着就一直
    想起"。算不出向量的，最多再等几批。
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
    ) -> None:
        """读这一批的情境；和上一批比变了，就定下一条新查询（没有地点也没有委派时不查）。"""
        key: List[Tuple[str, str, str, str]] = []
        parts: List[str] = []
        for game in sorted(set(situations) | set(tasks)):
            save = current_saves.get(game)
            known = save is not None and save.known
            situation = situations.get(game)
            near = ""
            if situation is not None and situation.age_ms <= SITUATION_STALE_MS:
                near = next((place.name for place in situation.places if place.distance <= ARRIVE_WITHIN), "")
            task = (tasks.get(game) or "").strip()[:_TASK_CHARS]
            key.append((game, save.id if known and save is not None else "", near, task))
            if near or task:
                where = f" 存档「{save.label}」" if known and save is not None else ""
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

    def _forget_game(self, game: str) -> None:
        """丢掉一个游戏记下的距离：之后第一次新读数当"之前在哪不清楚"。"""
        self._previous.pop(game, None)
        for key in [key for key in self._last if key[0] == game]:
            del self._last[key]
