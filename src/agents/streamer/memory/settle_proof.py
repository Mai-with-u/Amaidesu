"""下播整理核对"她说做好了"：本场游戏记录里撑得住"做成了"的那几条，以及引用的核对。

她嘴上说"门装好了"不等于游戏里真装好了：游戏那边没交付就停了、或者根本没去做，她照样会说做好了。
整理把这句写成档案里的事实，下一场她就带着一件没发生的事开播，越说越真。所以整理时把本场的游戏记录
编号列给模型——了结了的委派（只有交付了的能作证）、说明这一步做成了的里程碑与上报——档案里写游戏里
做成了什么、了结承诺时说做完了，都要引撑着它的记录（可以引几条，有一条撑得住就算）。模型引了哪条
说了不算，代码核对：引的那条存在、是做成了的、同一个游戏、不晚于她说那句的时候。没核实到的完成说法
照样写进档案，但写成"她说……（游戏记录里没核实到）"、不当事实：她顺手做成却没留下记录的事不会被
悄悄略掉，人审时也看得见；了结承诺不认没核实到的"做完了"，连同原因进整理报告。

记录给全本场的，不截最近几条：长场前半段的完成说法也引得到。条数多时把同一次执行里连续的几步
里程碑并成一行，免得撑爆输入。

本场一条游戏记录都没有时（记录没接上、旧数据、游戏那边一件事都没报），无从核对，不拦。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Sequence, Set, Tuple

from src.modules.storage.repos.events import TASK_SUCCEEDED_EVENT

__all__ = [
    "PROOF_GAME_EVENT_TYPES",
    "UNVERIFIED_MARK",
    "GameRecord",
    "game_records",
    "infer_proof_for_claim",
    "is_done_claim",
    "mark_unverified",
    "proof_for",
    "render_records",
]

# 下播核验读取 game_events 时只取能作证的进展事件类型，避免高频异常或告警挤掉前半场成功记录
PROOF_GAME_EVENT_TYPES: Tuple[str, ...] = (
    "milestone",
    "report",
    "dimension_changed",
    "item_delivery",
    TASK_SUCCEEDED_EVENT,
)

# 完成说法：游戏里做成了某件事的说法（搭好了、装上了、拿到了、做完了这类）。前面紧挨着"没、未、不、别"的
# 是没做成（"还没烤完""没装好"），或前面紧挨着打算/准备/听说等非已成陈述的，不算
_DONE_CLAIM = re.compile(
    r"(?:做|盖|搭|建|装|修|弄|种|铺|围|造|摆|放|点|挖|砌|封|补|烤|拼|画|配|收拾|整理|布置|备|堆|垒|架|焊|刷|插|挂"
    r"|接|连|炼|熔|煮|炒|绑|圈|驯|立)好"
    r"|(?:做|合成|造|炼|打造)出"
    r"|(?:做|盖|搭|建|装|修|弄|种|铺|围|造|挖|砌|补|烤|炸|拆|砍|采|收|搬|清|填|堆|插|挂|炼|熔|煮|收拾|整理)完"
    r"|(?:凑|攒|配|摆|收|备)齐"
    r"|(?:挖|拿|找|采|收集|买|换|抓|钓|捡|弄)到"
    r"|(?:装|安|点|种|盖|铺|围|挂|插)上了"
    r"|种下|立起来|烤干|搞定|完成|到手|落成|竣工"
)
_NEGATION_CHARS = ("没", "未", "不", "别")
_CLAUSE_BREAKS = "，。！？；,.!?;:：\n"
# 非已成事实的计划、未然或转述前导词：紧邻完成词前出现时不视为“宣称已完成”
_NON_DONE_PREFIXES: Tuple[str, ...] = (
    "打算",
    "准备",
    "计划",
    "想要",
    "快要",
    "即将",
    "等会儿",
    "待会儿",
    "听说",
)
# 连续同文单步完成记录合并窗口（毫秒）：3 分钟内连续多次相同完成摘要折叠为一条“共 N 次”
_CONSECUTIVE_DEDUP_WINDOW_MS = 3 * 60 * 1000
# 兜底语义核对清洗前剥离的宽泛地标、修饰前缀、通用动作与记录模板短语（按长短降序编译，避免靠宽泛前缀穿透匹配）
_STRIP_PHRASES: Tuple[str, ...] = tuple(
    sorted(
        (
            "停了但没交付结果",
            "游戏那边没交付",
            "做到哪不确定",
            "她认定做完了",
            "停了没交付",
            "被重启打断",
            "不能作证",
            "能推能关",
            "能开能关",
            "主世界",
            "全自动",
            "自动化",
            "一整套",
            "下界",
            "末地",
            "营地",
            "基地",
            "家里",
            "房间",
            "屋里",
            "门口",
            "门外",
            "门前",
            "门后",
            "门边",
            "出门",
            "进门",
            "附近",
            "河边",
            "海边",
            "山上",
            "地下",
            "后院",
            "旁边",
            "上面",
            "下面",
            "里面",
            "外面",
            "这边",
            "那边",
            "这里",
            "那里",
            "地方",
            "世界",
            "一楼",
            "二楼",
            "三楼",
            "动力",
            "自动",
            "机械",
            "工作",
            "系统",
            "全套",
            "全部",
            "全都",
            "一堆",
            "一批",
            "一些",
            "几个",
            "几块",
            "几条",
            "一根",
            "一条",
            "一块",
            "一座",
            "一排",
            "一个",
            "一把",
            "一只",
            "一扇",
            "已经",
            "今天",
            "刚才",
            "终于",
            "东西",
            "这个",
            "那个",
            "游戏",
            "玩家",
            "大家",
            "我们",
            "自己",
            "一下",
            "然后",
            "现在",
            "可以",
            "开始",
            "运行",
            "使用",
            "成功",
            "完成",
            "搞定",
            "做完",
            "做好",
            "拿到",
            "找到",
            "弄好",
            "装好",
            "修好",
            "放好",
            "交付",
            "委派",
            "记录",
            "她说",
            "起来",
            "出来",
            "进入",
            "来到",
            "回到",
            "跑通",
            "压出",
            "放入",
            "放进",
            "取出",
            "扔出",
            "存进",
            "塞进",
            "摆在",
            "放在",
            "装上",
            "拿了",
            "给了",
            "还给",
            "好了",
            "完了",
            "到了",
            "上了",
            "去了",
            "回来",
            "直接",
            "就是",
            "也把",
        ),
        key=len,
        reverse=True,
    )
)
_STRIP_PHRASES_RE = re.compile("|".join(re.escape(phrase) for phrase in _STRIP_PHRASES))
# 清洗后用于切分短句的纯语气词与功能助词（不跨词误伤常见物品名）
_PARTICLE_SPLIT_RE = re.compile(r"[的了啦啊呢呀哦嘛也都]")
# 仅在中文词段开头剥离的介词/连词（保留结尾如“火把”中的“把”）
_LEADING_FUNC_RE = re.compile(r"^[把被给在往从对和与跟]+")
# 短完成声明（如“门装好啦”“床摆好了”“树砍完了”）缺失双字实词时允许兜底识别的单字核心物词
_SINGLE_CHAR_OBJECT_NOUNS: frozenset[str] = frozenset(
    "门床树花桥墙路船车炉箱桶剑镐斧锹弓箭鱼牛羊猪鸡猫狗狼马田屋塔井灯矿铁金铜煤石木沙土水书蛋肉药钟梯窗"
)
_CJK_ANY_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ALNUM_RUN_RE = re.compile(r"[a-z0-9_]{2,}", re.IGNORECASE)

# 记录超过这么多条时，同一次执行里连续的几步里程碑并成一行（第 54、58 场各四五十条，不用并）；
# 每步只留这么多字。不截条数：截掉的是长场前半段，那时说的"做好了"就引不到记录、被误判成没核实到
_FOLD_AFTER = 100
_STEP_CHARS = 30
_STEP_HEAD = re.compile(r"^第\s*(\d+)\s*步")
# 并成一行时去掉的工具名（"maicraft:obtain，"）与物品 ID 前缀，只留这一步做了什么
_STEP_NOISE = re.compile(r"maicraft:[a-z_]+，?|minecraft:")
_RECORD_TEXT_CHARS = 50
_INSTRUCTION_CHARS = 24
# 记录时刻允许比她那句晚一点：上报落库与台词落库各走各的，同一刻发生的两件事时刻会差几十秒
_LATE_SLACK_MS = 2 * 60 * 1000
# 里程碑、上报里写着没做成的：撑不了"做成了"，不列
_FAILED_WORDS = (
    "没有完成",
    "没完成",
    "未完成",
    "没做成",
    "没成功",
    "失败",
    "失效",
    "没交付",
    "没有开始",
    "没开始",
    "取消了",
    "停着没动",
    "放弃",
)
# 委派没交付的几种终态怎么写（列出来让模型知道这件事没做成，但不能作证；主播单方结单而游戏那边没交付的也不算证据）
_UNPROVEN_STATUS = {
    "undelivered": "停了没交付",
    "closed_by_initiator": "她认定做完了，游戏那边没交付",
    "interrupted": "被重启打断，做到哪不确定",
    "cancelled": "撤掉了",
    "failed": "失败了",
    "timeout": "超时了",
}
_RECORD_REF = re.compile(r"(?:记录)?#?(\d+)")
# evidence 里几个编号之间的分隔（"记录#3、记录#5""#3 #5"），也可以是 JSON 列表
_REF_SPLIT = re.compile(r"[、,，;；/\s]+|和|与|及")
# 没核实到的完成说法写进档案时的标记：代码写的，整理报告按它数这一场有几条
UNVERIFIED_MARK = "（游戏记录里没核实到）"
# 档案标题里游戏名那一截（"Minecraft：……"）至多这么长，"她说"插在它后面
_GAME_HEAD_CHARS = 12


@dataclass(frozen=True)
class GameRecord:
    """一条本场游戏记录：编号、哪个游戏、什么时候、什么记录、写的什么、能不能撑"做成了"。

    几步并成一行时 ``at_ms`` 是第一步的时刻（核对"不晚于她那句"按它），``until_ms`` 是最后一步的；
    单条记录 ``until_ms`` 为 0。
    """

    number: int
    game: str
    at_ms: int
    kind: str
    text: str
    proves: bool
    until_ms: int = 0


def _clause_prefix(text: str, pos: int, max_chars: int) -> str:
    """取命中位置前同一分句内至多 max_chars 个字符，遇分句标点截断。"""
    window = text[max(0, pos - max_chars) : pos]
    for idx in range(len(window) - 1, -1, -1):
        if window[idx] in _CLAUSE_BREAKS:
            return window[idx + 1 :]
    return window


def is_done_claim(text: str) -> bool:
    """这段话里有没有"游戏里做成了某件事"的说法；同一分句前有否定或未然/计划/转述词的不算。"""
    raw = text or ""
    for match in _DONE_CLAIM.finditer(raw):
        neg_before = _clause_prefix(raw, match.start(), 4)
        if any(char in neg_before for char in _NEGATION_CHARS):
            continue
        # 同分句内的未然/计划/转述前导词可能隔着主语与宾语（如"听说 Alex 把刷铁机修好了"），查至本分句起点
        clause_before = _clause_prefix(raw, match.start(), match.start())
        if any(prefix in clause_before for prefix in _NON_DONE_PREFIXES):
            continue
        return True
    return False


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def mark_unverified(title: str) -> str:
    """没核实到的完成说法写进档案的样子："Minecraft：她说门装好了（游戏记录里没核实到）"。

    标题以游戏名开头时"她说"插在游戏名后面，读起来还是那个游戏里的一件事。
    """
    head, sep, body = title.partition("：")
    if sep and body and len(head) <= _GAME_HEAD_CHARS:
        return f"{head}：她说{body}{UNVERIFIED_MARK}"
    return f"她说{title}{UNVERIFIED_MARK}"


def game_records(events: Sequence[Any], delegations: Sequence[Any]) -> List[GameRecord]:
    """本场的里程碑、上报与了结了的委派 → 按时刻排好、编了号的游戏记录（全本场，不截条数）。

    委派只有交付了的（``succeeded``）能作证，其余终态照列、标明不能作证：模型看得到这件事没做成，
    就不会把她嘴上的"做好了"写成事实。还没了结的委派不列。里程碑与上报写着没做成的不列。
    超过 :data:`_FOLD_AFTER` 条时，同一次执行里连续的几步里程碑并成一行。
    """
    staged: List[_Staged] = []
    for delegation in delegations:
        status = str(getattr(delegation, "status", "") or "")
        if status in ("", "accepted"):
            continue
        what = _clip(getattr(delegation, "instruction", "") or "（指令没记下）", _INSTRUCTION_CHARS)
        if status == "succeeded":
            summary = str(getattr(delegation, "summary", "") or "").strip()
            summary = summary.removeprefix("delivery:").strip()
            text = f"委派「{what}」交付了：{_clip(summary, _RECORD_TEXT_CHARS)}" if summary else f"委派「{what}」交付了"
        else:
            text = f"委派「{what}」{_UNPROVEN_STATUS.get(status, status)}，不能作证"
        at_ms = int(getattr(delegation, "ended_ms", 0) or 0)
        staged.append(_Staged(at_ms, str(getattr(delegation, "game", "") or ""), "委派", text, status == "succeeded"))
    for event in events:
        # 里程碑、阶段上报、跨维度迁移、物品交付与单步任务终态成功均可作为游戏内完成说法的核实证源
        kind = {
            "milestone": "里程碑",
            "report": "上报",
            "dimension_changed": "跨维度",
            "item_delivery": "交付",
            TASK_SUCCEEDED_EVENT: "完成",
        }.get(str(getattr(event, "event_type", "") or ""))
        message = " ".join(str(getattr(event, "message", "") or "").split())
        if kind is None or not message or any(word in message for word in _FAILED_WORDS):
            continue
        at_ms = int(getattr(event, "timestamp_ms", 0) or 0)
        staged.append(_Staged(at_ms, str(getattr(event, "game", "") or ""), kind, message, True))
    staged.sort(key=lambda item: item.at_ms)
    staged = _dedupe_consecutive_completions(staged)
    if len(staged) > _FOLD_AFTER:
        staged = _fold_steps(staged)
    return [
        GameRecord(
            number=index,
            game=item.game,
            at_ms=item.at_ms,
            kind=item.kind,
            text=item.text if item.until_ms else _clip(item.text, _RECORD_TEXT_CHARS),
            proves=item.proves,
            until_ms=item.until_ms,
        )
        for index, item in enumerate(staged, start=1)
    ]


@dataclass(frozen=True)
class _Staged:
    """编号前的一条记录（并步时 ``until_ms`` 记最后一步的时刻）。"""

    at_ms: int
    game: str
    kind: str
    text: str
    proves: bool
    until_ms: int = 0


def _dedupe_consecutive_completions(staged: Sequence[_Staged]) -> List[_Staged]:
    """将短窗内连续重复的同类同文单步“完成”记录折叠为一条“……（共 N 次）”，防止重复动作刷屏占满记录表。"""
    result: List[_Staged] = []
    idx = 0
    total = len(staged)
    while idx < total:
        current = staged[idx]
        if current.kind != "完成":
            result.append(current)
            idx += 1
            continue
        end = idx
        while (
            end + 1 < total
            and staged[end + 1].kind == "完成"
            and staged[end + 1].game == current.game
            and staged[end + 1].text == current.text
            and staged[end + 1].at_ms - staged[end].at_ms <= _CONSECUTIVE_DEDUP_WINDOW_MS
        ):
            end += 1
        count = end - idx + 1
        if count == 1:
            result.append(current)
        else:
            clipped = _clip(current.text, _RECORD_TEXT_CHARS)
            result.append(
                _Staged(
                    at_ms=current.at_ms,
                    game=current.game,
                    kind=current.kind,
                    text=f"{clipped}（共 {count} 次）",
                    proves=current.proves,
                    until_ms=staged[end].at_ms,
                )
            )
        idx = end + 1
    return result


def _fold_steps(staged: Sequence[_Staged]) -> List[_Staged]:
    """同一次执行里连续的几步里程碑（"第 1 步""第 2 步"……中间没夹别的记录）并成一行。

    步号往上走就算同一次执行；又从小号开始、换了游戏、中间夹了上报或委派，就是另一次。
    并成的一行时刻取第一步（核对按它，宽一点：同一次执行几步之间只差几分钟），每步只留做了什么。
    """
    folded: List[_Staged] = []
    run: List[Tuple[int, _Staged]] = []

    def flush() -> None:
        if len(run) == 1:
            folded.append(run[0][1])
        elif run:
            steps = "；".join(_clip(_STEP_NOISE.sub("", item.text), _STEP_CHARS) for _, item in run)
            first, last = run[0][1], run[-1][1]
            folded.append(_Staged(first.at_ms, first.game, "里程碑", f"连续 {len(run)} 步：{steps}", True, last.at_ms))
        run.clear()

    for item in staged:
        step = _STEP_HEAD.match(item.text) if item.kind == "里程碑" else None
        if step is None:
            flush()
            folded.append(item)
            continue
        number = int(step.group(1))
        if run and (number <= run[-1][0] or item.game != run[-1][1].game):
            flush()
        run.append((number, item))
    flush()
    return folded


def _clock(at_ms: int) -> str:
    return datetime.fromtimestamp(max(0, at_ms) / 1000).strftime("%H:%M")


def render_records(records: Sequence[GameRecord]) -> str:
    """给模型看的【游戏记录】一段；没有记录时为空串（整段省略）。"""
    if not records:
        return ""
    lines = []
    for record in records:
        when = _clock(record.at_ms)
        if record.until_ms and _clock(record.until_ms) != when:
            when = f"{when}–{_clock(record.until_ms)}"
        lines.append(f"- 记录#{record.number} {when} {record.game} {record.kind}：{record.text}")
    return "【游戏记录（写游戏里做成了什么、了结时说做完了，都要引这里的编号）】\n" + "\n".join(lines)


def _record_numbers(ref: Any) -> List[int]:
    """evidence 里引的编号：一个（"记录#12"）、几个（"记录#12、记录#15"）或 JSON 列表；认不出的那截不算。"""
    if ref is None:
        return []
    parts = [str(item) for item in ref] if isinstance(ref, (list, tuple)) else _REF_SPLIT.split(str(ref))
    numbers: List[int] = []
    for part in parts:
        match = _RECORD_REF.fullmatch(part.strip())
        if match is not None:
            numbers.append(int(match.group(1)))
    return numbers


def proof_for(ref: Any, records: Sequence[GameRecord], *, game: Optional[str], at_ms: int) -> Optional[GameRecord]:
    """模型引的记录里有没有一条撑得住这句完成说法：存在、能作证、同一个游戏、不晚于她那句（容两分钟）。

    引了几条时有一条撑得住就算；都撑不住为 None。
    """
    by_number = {record.number: record for record in records}
    for number in _record_numbers(ref):
        record = by_number.get(number)
        if record is None or not record.proves:
            continue
        if game and record.game and record.game.lower() != game.lower():
            continue
        if record.at_ms > at_ms + _LATE_SLACK_MS:
            continue
        return record
    return None


def _clean_for_proof(text: str, *, game: Optional[str] = None) -> str:
    """剥离游戏名、完成动词、宽泛地标/修饰前缀与语气助词，保留可用于事实指向比对的实词骨架。"""
    cleaned = _DONE_CLAIM.sub(" ", str(text or ""))
    if game:
        cleaned = re.sub(re.escape(game), " ", cleaned, flags=re.IGNORECASE)
    cleaned = _STRIP_PHRASES_RE.sub(" ", cleaned)
    cleaned = _PARTICLE_SPLIT_RE.sub(" ", cleaned)
    return cleaned


def _extract_proof_features(
    text: str, *, game: Optional[str] = None
) -> Tuple[List[frozenset[str]], Set[str], List[str], Set[str]]:
    """从文本抽取实质比对特征：(去重实词单元列表, 全部双字/字母词元, 中文词段列表, 候选单字核心物词)。

    先清洗宽泛前缀与助词，再对每个中文词段剥离句首介词并切片为双字词元；
    当声明不含多字实词时（如“门装好啦”），回退到受控单字物词集合，并与记录词段末尾中心语比对，
    避免被“门口”“鱼竿”等非中心语复合词误匹配。
    """
    cleaned = _clean_for_proof(text, game=game)
    raw_units: List[frozenset[str]] = []
    all_tokens: Set[str] = set()
    cjk_runs: List[str] = []
    single_nouns: Set[str] = set()

    for match in _CJK_ANY_RUN_RE.finditer(cleaned):
        run = match.group(0)
        if len(run) >= 2:
            stripped = _LEADING_FUNC_RE.sub("", run)
            if stripped:
                run = stripped
        if not run:
            continue
        cjk_runs.append(run)
        if len(run) == 1:
            if run in _SINGLE_CHAR_OBJECT_NOUNS:
                single_nouns.add(run)
            continue
        unit_tokens = frozenset(run[pos : pos + 2] for pos in range(len(run) - 1))
        if unit_tokens:
            raw_units.append(unit_tokens)
            all_tokens.update(unit_tokens)

    for match in _ALNUM_RUN_RE.finditer(cleaned):
        word = match.group(0).lower()
        unit_tokens = frozenset({word})
        raw_units.append(unit_tokens)
        all_tokens.add(word)

    # 合并重复或被更完整实词覆盖的子单元（如标题写“木桥”、原话写“北岸木桥”时不重复计两份）
    unique_units: List[frozenset[str]] = []
    for unit in sorted(set(raw_units), key=len, reverse=True):
        if any(unit.issubset(existing) for existing in unique_units):
            continue
        unique_units.append(unit)
    return unique_units, all_tokens, cjk_runs, single_nouns


def _match_record_score(
    claim_units: Sequence[frozenset[str]],
    claim_tokens: Set[str],
    claim_single_nouns: Set[str],
    rec_tokens: Set[str],
    rec_cjk_runs: Sequence[str],
) -> Tuple[int, int]:
    """评估单条记录与声明的实质重合度；不满足门槛返回 (0, 0)。

    判定规则：
    - 多字实词声明：
      - 仅 1 个实词单元时，若该单元含 >= 3 个双字词元（4 字及以上长名词如“要塞刷怪笼”），至少命中 2 个双字词元；
        否则至少命中 1 个双字词元（如“鳕鱼”“正门”）。
      - 含 >= 2 个实词单元时，至少命中 2 个双字/字母词元，且至少覆盖声明半数实词单元，防止单点重合放行吹牛。
    - 无多字实词的短声明（如“门装好啦”“床摆好了”）：
      - 仅当声明中的全部单字核心物词均命中记录的独立单字或词段中心语尾字（如“正门”“木床”“橡树”）时才算命中。
    """
    if claim_units:
        overlap = len(claim_tokens & rec_tokens)
        if overlap <= 0:
            return (0, 0)
        hit_units = sum(1 for unit in claim_units if unit & rec_tokens)
        if len(claim_units) == 1:
            min_overlap = 2 if len(claim_tokens) >= 3 else 1
            if overlap < min_overlap:
                return (0, 0)
            return (hit_units, overlap)
        if overlap < 2 or hit_units * 2 < len(claim_units):
            return (0, 0)
        return (hit_units, overlap)

    if not claim_single_nouns:
        return (0, 0)
    matched = sum(1 for noun in claim_single_nouns if any(run.endswith(noun) for run in rec_cjk_runs))
    if matched < len(claim_single_nouns):
        return (0, 0)
    return (matched, matched)


def infer_proof_for_claim(
    text: str,
    records: Sequence[GameRecord],
    *,
    game: Optional[str],
    at_ms: int,
) -> Optional[GameRecord]:
    """当模型漏填 evidence 时，按时间与实质重合度从可作证游戏记录里兜底匹配对应证据。

    触发条件：仅在场次档案校验且模型未显式填写 ``evidence`` 时调用（绝不绕过显式指向失败/超时记录的错误引用，
    也不用于旧承诺结案）。
    执行顺序：提取声明实词单元与兜底单字核心物词 -> 筛同游戏且不晚于发言时刻（容两分钟）的记录
    -> 对可作证记录与失败记录采用同一实词门槛评分 -> 挑覆盖最高者并确认同期或之后无指向同一事项的失败矛盾记录。
    """
    claim_units, claim_tokens, _, claim_single_nouns = _extract_proof_features(text, game=game)
    if not claim_units and not claim_single_nouns:
        return None
    deadline = at_ms + _LATE_SLACK_MS
    best: Optional[Tuple[int, int, int, GameRecord]] = None
    contradiction_times: List[int] = []
    for record in records:
        if game and record.game and record.game.lower() != game.lower():
            continue
        if record.at_ms > deadline:
            continue
        _, rec_tokens, rec_cjk_runs, _ = _extract_proof_features(record.text, game=game)
        hit_units, overlap = _match_record_score(
            claim_units, claim_tokens, claim_single_nouns, rec_tokens, rec_cjk_runs
        )
        if overlap <= 0:
            continue
        if record.proves:
            score_tuple = (hit_units, overlap, record.at_ms, record)
            if best is None or score_tuple[:3] > best[:3]:
                best = score_tuple
        else:
            contradiction_times.append(record.at_ms)
    if best is None:
        return None
    best_record = best[3]
    # 若在命中的成功记录同一时刻或之后存在指向同一事项的未交付/失败记录，保守不自动放行
    if any(fail_at_ms >= best_record.at_ms for fail_at_ms in contradiction_times):
        return None
    return best_record
