"""MaiCraft 任务事件的分类表：事件流里哪些是值得向观众叙述的身体遭遇。

## 为什么需要这一层

MaiCraft v1 的事件流里大部分是目标运行的处境变化（开始、提问、暂停、结束），那是游戏 Agent
的任务通道，主播的叙事不需要。和身体有关的有这几种：挨了一阵打（谁打的、几下、掉了多少血，
一段只来一条、不分开始结束）、换了维度、生存需求插进来的临时任务开始与结束（自卫、夜里封顶自保、
退离边沿）、角色自己处理不了的需求（饿了没吃上、封顶没封上）、角色死亡；装备用坏、探索途中的发现、
跟随中的处境变化与天色变了（天黑、天亮、下雨、雨停）也是主播能讲的经历。`maicraft_attention` 采集器只把这些转成 `game.body.*` 事件，任务通道的事件留在上游。

事件流里剩余种类的去向三分（判据）：
- 步进完成（`step_finished`）：主播可讲的进展——画面在动、Mod 自带结论句（如"采集完成：8 个
  橡木原木进了背包"）——转成 `game.milestone`，走主播侧现成的订阅与落库；
- 目标开始/提问/暂停/继续：游戏 Agent 的任务通道，留在上游；
- 目标完成/失败/卡住（`stalled`）：不经这里——完成与失败走 `game.report`（自带主播唤醒），
  卡住由游戏 Agent 记账本中间态并转发"有目标停着没动静"。

提醒（快溺水、装备快坏、黑暗里反复挨打这类）是写给游戏 Agent 的维护提示，证据在 observe(self) 里，
不是遭遇本身；转给主播只会变成一串重复的"身体事件"，把真正的挨打和死亡挤掉，所以不进叙事通道。

计时到点（`timer_due`）同理：那是游戏 Agent 用 timer 给自己定的"过 N 秒回头看一眼"，备注是它写给自己的
（"看炉子烧好没"），画面里什么都没发生；到点后它去看、去做，取出东西、交付这些后续才是主播能讲的，
会照常走步进完成与 `game.report`。提前把一句自备忘念给观众，只会和随后的真进展说两遍，所以也不进叙事通道。

## 契约

- `classify()` 返回 `None` = 这条事件不进入叙事通道（目标运行的处境变化、提醒、计时到点）；
- 未知事件种类归 `unknown` 且保留 `source_event_type`，所以**上游加新事件种类不会让本系统的事件面漂移**；
- `summarize()` 只陈述事件里有的事实，并写成观众听得懂的白话：Mod 那句话是写给游戏 Agent 的
  （"插入自卫""回到打点""位移 1 格"），主播照念观众听不懂，所以内部说法换成白话或去掉，
  坐标、血量、格数这些数字一律不进叙事通道（数量限定换成"附近"这类说法，范围限定不丢）。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from src.modules.events.names import CoreEvents

#: 叙事种类 → 事件名（事件名一律引用 CoreEvents 常量，不写字面量）
KIND_TO_EVENT: Dict[str, str] = {
    "attacked": CoreEvents.GAME_BODY_ATTACKED,
    "attack_ended": CoreEvents.GAME_BODY_ATTACK_ENDED,
    "died": CoreEvents.GAME_BODY_DIED,
    "respawned": CoreEvents.GAME_BODY_RESPAWNED,
    "reflex_started": CoreEvents.GAME_BODY_REFLEX_STARTED,
    "reflex_finished": CoreEvents.GAME_BODY_REFLEX_FINISHED,
    "dimension_changed": CoreEvents.GAME_BODY_DIMENSION_CHANGED,
    "need_unhandled": CoreEvents.GAME_BODY_NEED_UNHANDLED,
    "unknown": CoreEvents.GAME_BODY_UNKNOWN,
    # 步进完成不进身体事件通道，转 game.milestone（主播可讲的进展）
    "milestone": CoreEvents.GAME_MILESTONE,
}

#: 已经是"结局面"的种类（主播据此把措辞从"正在"改成"刚才"）
RESOLVED_KINDS = frozenset({"attack_ended", "respawned", "reflex_finished"})

#: 事件种类 → 叙事种类。生存需求的临时任务就是"身体先停下手上的活处理急事"，对应本能接管。
_SOURCE_KINDS: Dict[str, str] = {
    # 一阵挨打 Mod 只发一条（按来源归并，安静 5 秒或每 10 秒发一次），没有"打完了"那一条，所以只用 attacked。
    "hurt": "attacked",
    "dimension_changed": "dimension_changed",
    "temporary_task_started": "reflex_started",
    "temporary_task_finished": "reflex_finished",
    "need_unhandled": "need_unhandled",
    # 角色血量见底进了死亡流程：Mod 只发这一条，重生后接着做原来的目标，不另发重生事件。
    "character_died": "died",
    # 步进完成是主播可讲的进展（画面在动、自带结论句），转 milestone 通道
    "step_finished": "milestone",
}

#: 目标运行的处境变化与死亡恢复决策的执行结果：游戏 Agent 的任务通道，不进叙事通道
# （步进完成除外——它转 milestone）。目标完成/失败不经这里，交付与升级走 game.report；
# 卡住（stalled，Mod 侧"长时间没有新事件仍未结束"）同样归任务通道：账本中间态与
# "有目标停着没动静"的叙事转发由游戏 Agent 处理，不走身体事件分类。
_GOAL_KINDS = frozenset({"started", "asked", "paused", "resumed", "finished", "stalled", "death_recovery_applied"})

#: 写给游戏 Agent 自己的：维护提示（证据在 observe(self) 里）与计时到点（它给自己定的回头看一眼），
#: 都不是遭遇，不进叙事通道；计时到点后游戏 Agent 去做的事另走步进完成与交付，主播从那里讲。
_SILENT_KINDS = frozenset({"reminder", "timer_due"})

#: 每种叙事的开头：说清这是什么性质的事，用观众听得懂的白话（不说"身体""角色"这类幕后说法）。
#: 挨打的原话本身就是"被苦力怕炸到一下"，不另加开头。
_LEADS: Dict[str, str] = {
    "dimension_changed": "换了个维度",
    "reflex_started": "先放下手上的事",
    "reflex_finished": "急事处理完了",
    "need_unhandled": "有件事没办成",
    "died": "死了",
}

#: 没有专门叙事种类、走 unknown 的事件里，主播也能讲的几种：给一个说清性质的开头，不让它读成"身体事件：xxx"
_SOURCE_LEADS: Dict[str, str] = {
    "gear_broken": "装备用坏了",
    "discovered": "发现了新东西",
    "follow_status": "跟着人走时",
    # 天黑、天亮、下雨、雨停是观众在画面里也看得见的变化，主播能顺着接"天黑了该找地方睡了"这类话；
    # Mod 只在变的那一刻发一条，一个游戏日不过几条，不会像提醒那样刷屏，所以进叙事通道而不是静默。
    "sky_changed": "天色变了",
    "stalled": "手上的事好一阵没进展了",
}

#: 原话是写给游戏 Agent 的操作提示（"用 goal 按编号看它在做什么，不会动了就取消"），观众听不懂：只说开头
_LEAD_ONLY_SOURCES = frozenset({"stalled"})

#: 什么都对不上时的说法：上游加了新事件种类、又没有原话
_FALLBACK = "游戏里有了新动静"

# ---------------------------------------------------------------------------
# 白话化：把 Mod 写给游戏 Agent 的那句话改成观众听得懂的说法
# ---------------------------------------------------------------------------

#: 括号里的补充说明；先单独清洗，整段都说得出口才保留
_ASIDE = re.compile(r"[（(]([^（）()]*)[）)]")
#: 括号清洗后在原句里留下的占位记号（\x00 不会出现在 Mod 的中文原话里）
_ASIDE_MARK = re.compile(r"\x00(\d+)\x00")
#: 句首的玩家名（"Dev被蜘蛛杀死了" 的 Dev）：观众只需要知道被什么杀死
_PLAYER_NAME = re.compile(r"^[A-Za-z0-9_]{2,16}(?=被)")
#: 只给游戏 Agent 用的说法：打点、位移、目标编号、步号、能力 ID、物品标签、操作提示
_AGENT_ONLY = re.compile(r"打点|位移|游标|第\s*\d+\s*步|目标\s*\d+|按编号|用 goal|它下的目标|看现场决定|maicraft:|#c:")
#: 剩下的任何数字都是遥测（血量、格数、秒数），不念给观众
_DIGITS = re.compile(r"\d")
#: 还没换成中文名的方块、物品、群系 ID
_RAW_ID = re.compile(r"[a-z_]+:[a-z0-9_/]+")

#: 整句换掉的说法：Mod 的术语 → 观众听得懂的话；空串 = 这句是重复或幕后信息，去掉
_PHRASES: Dict[str, str] = {
    "被威胁": "被怪盯上了",
    "插入自卫": "先自卫",
    "已撤离": "跑开了",
    "自卫结束": "打完了",
    "没见打倒哪只": "没打倒哪只",
    "角色死了": "",
    "睡觉没有完成": "",
    "回到打点": "",
    "装备用坏了": "",
}

#: 常见工具与护甲：材质 + 种类拼中文名（模组物品不在表里，原样去掉命名空间交给主播念）
_MATERIALS: Dict[str, str] = {
    "wooden": "木",
    "stone": "石",
    "iron": "铁",
    "golden": "金",
    "diamond": "钻石",
    "netherite": "下界合金",
    "leather": "皮革",
    "chainmail": "锁链",
}
_GEAR: Dict[str, str] = {
    "pickaxe": "镐",
    "axe": "斧",
    "shovel": "锹",
    "hoe": "锄",
    "sword": "剑",
    "helmet": "头盔",
    "chestplate": "胸甲",
    "leggings": "护腿",
    "boots": "靴子",
}

#: 常见群系的中文名（不在表里的去掉命名空间原样给）
_BIOMES: Dict[str, str] = {
    "plains": "平原",
    "forest": "森林",
    "birch_forest": "白桦森林",
    "dark_forest": "黑森林",
    "taiga": "针叶林",
    "old_growth_spruce_taiga": "原始云杉针叶林",
    "snowy_plains": "雪原",
    "snowy_taiga": "积雪针叶林",
    "desert": "沙漠",
    "savanna": "热带草原",
    "jungle": "丛林",
    "swamp": "沼泽",
    "mangrove_swamp": "红树林沼泽",
    "river": "河流",
    "beach": "沙滩",
    "ocean": "海洋",
    "deep_ocean": "深海",
    "warm_ocean": "暖水海洋",
    "cherry_grove": "樱花树林",
    "meadow": "草甸",
    "badlands": "恶地",
    "mushroom_fields": "蘑菇岛",
    "lush_caves": "繁茂洞穴",
    "dripstone_caves": "溶洞",
    "deep_dark": "深暗之域",
    "nether_wastes": "下界荒地",
    "crimson_forest": "绯红森林",
    "warped_forest": "诡异森林",
    "soul_sand_valley": "灵魂沙峡谷",
    "basalt_deltas": "玄武岩三角洲",
    "the_end": "末地",
}


def _plain_id(raw_id: str) -> str:
    """去掉 ``minecraft:`` 命名空间；模组物品保留命名空间，主播能看出是哪个模组的东西。"""
    return raw_id.split(":", 1)[1] if raw_id.startswith("minecraft:") else raw_id


def item_name(raw_id: str) -> str:
    """物品 ID → 念得出口的名字：常见工具护甲拼中文（``minecraft:iron_pickaxe`` → "铁镐"），其余去掉原版命名空间。"""
    material, _, gear = _plain_id(raw_id).partition("_")
    if material in _MATERIALS and gear in _GEAR:
        return f"{_MATERIALS[material]}{_GEAR[gear]}"
    return _plain_id(raw_id)


def biome_name(raw_id: str) -> str:
    """群系 ID → 中文名；表里没有的去掉原版命名空间原样给。"""
    return _BIOMES.get(_plain_id(raw_id), _plain_id(raw_id))


def _health_words(remaining: float) -> str:
    """还剩多少血 → 处境白话（满血 20 点）；血还多时不值一提，返回空串。"""
    if remaining <= 4:
        return "血快没了"
    if remaining <= 10:
        return "血掉了不少"
    return ""


def _hearts_words(hearts: int) -> str:
    """一次遭遇大约掉了几颗心 → 白话。"""
    if hearts <= 0:
        return "没掉血"
    return "掉了点血" if hearts <= 3 else "掉了一大截血"


def _rewrite_clause(clause: str) -> str:
    """一个分句 → 观众听得懂的说法；返回空串表示这句不念（幕后说法、遥测或重复）。"""
    text = _PLAYER_NAME.sub("", clause.strip())
    if text in _PHRASES:
        return _PHRASES[text]
    # 数量限定换成"附近"，范围限定本身要保留："32 格内没有能睡的床" → "附近没有能睡的床"
    text = re.sub(r"\d+\s*格内", "附近", text)
    gear = re.fullmatch(r"(?:mainhand|offhand|head|chest|legs|feet)\s*的\s*(\S+?)\s*耐久打空消灭了", text)
    if gear:
        return f"{item_name(gear.group(1))}坏了"
    biome = re.fullmatch(r"发现群系\s*(\S+)", text)
    if biome:
        return biome_name(biome.group(1))
    hearts = re.fullmatch(r"掉了约\s*(\d+)\s*颗心", text)
    if hearts:
        return _hearts_words(int(hearts.group(1)))
    remaining = re.fullmatch(r"还剩\s*(\d+(?:\.\d+)?)\s*点", text)
    if remaining:
        return _health_words(float(remaining.group(1)))
    if re.fullmatch(r"掉了\s*\d+(?:\.\d+)?\s*点血", text):
        return ""
    # 次数只分"一下"和"好几下"："被骷髅射中 3 下" → "被骷髅射中好几下"
    text = re.sub(r"(?<![\d.])\s*1\s*下", "一下", text)
    text = re.sub(r"\s*\d+\s*下", "好几下", text)
    if _AGENT_ONLY.search(text) or _DIGITS.search(text) or _RAW_ID.search(text):
        return ""
    return text


def _clean_aside(inner: str) -> Optional[str]:
    """括号里的补充说明整段都说得出口才保留；只要有一句被去掉就整段不要，免得留下没头没尾的半截话。"""
    parts = [part.strip() for part in _CLAUSE_BREAK.split(inner) if part.strip()]
    rewritten = [_rewrite_clause(part) for part in parts]
    if not rewritten or any(not part for part in rewritten):
        return None
    return "，".join(rewritten)


def plain_detail(message: str) -> str:
    """Mod 写给游戏 Agent 的那句话 → 观众听得懂的白话（去掉观察编号、坐标、数字与幕后说法）。"""
    asides: List[Optional[str]] = []

    def stash(match: "re.Match[str]") -> str:
        asides.append(_clean_aside(match.group(1)))
        return f"\x00{len(asides) - 1}\x00"

    text = _ASIDE.sub(stash, _OBSERVATION_IDS.sub("", str(message or "")))
    kept: List[str] = []
    for clause in _CLAUSE_BREAK.split(text):
        if not clause.strip():
            continue
        own = _ASIDE_MARK.sub("", clause)
        attached = [asides[int(index)] for index in _ASIDE_MARK.findall(clause)]
        notes = [note for note in attached if note]
        rewritten = _rewrite_clause(own) if own.strip() else ""
        if rewritten:
            kept.append(rewritten + "".join(f"（{note}）" for note in notes))
        elif notes:
            # 正句被去掉、括号里的说明还在（"角色死了（被蜘蛛杀死了）"）：说明本身就是要讲的事
            kept.extend(notes)
    return "，".join(dict.fromkeys(kept))


# 一个分句里出现"x, y, z"三个整数就是坐标：整句去掉，不在半句话里留下"停在了"这种残片。
_COORDINATES = re.compile(r"-?\d+\s*,\s*-?\d+\s*,\s*-?\d+")
# 括号里的观察编号（e12、b5 这类）是给游戏 Agent 指东西用的，念给观众没有意义，去掉括号连同编号。
_OBSERVATION_IDS = re.compile(r"\s*[（(][a-z]\d+[）)]")
# 只按中文标点与分号、冒号断句；英文逗号不断，否则坐标本身会被拆开。
# 英文冒号后面紧跟字母数字时是物品、方块、能力的 ID（minecraft:oak_log、"第 3 步（maicraft:build）"），
# 不断开，否则重组后变成"minecraft，oak_log"念给观众。
_CLAUSE_BREAK = re.compile(r"[，；：;]|:(?![\w./-])")


def classify(source_event_type: str) -> Optional[str]:
    """事件种类 → 叙事种类；不进入叙事通道返回 ``None``。"""
    source = str(source_event_type or "")
    if not source or source in _GOAL_KINDS or source in _SILENT_KINDS:
        return None
    return _SOURCE_KINDS.get(source, "unknown")


def without_coordinates(text: str) -> str:
    """去掉带坐标的分句与括号里的观察编号，其余原样保留（"被威胁，插入自卫：打点在 3, 64, 9" → "被威胁，插入自卫"）。"""
    plain = _OBSERVATION_IDS.sub("", str(text or ""))
    clauses = [clause.strip() for clause in _CLAUSE_BREAK.split(plain)]
    return "，".join(clause for clause in clauses if clause and not _COORDINATES.search(clause))


def summarize(kind: str, source_event_type: str, message: str) -> str:
    """一句观众听得懂的中文：说清性质的开头，加上白话化后的原话；原话说不出口时只说开头。"""
    lead = _LEADS.get(kind) or _SOURCE_LEADS.get(source_event_type) or ""
    detail = "" if source_event_type in _LEAD_ONLY_SOURCES else plain_detail(message)
    if lead and detail.startswith(lead):
        # 原话开头和叙事开头重复（"装备用坏了，铁镐坏了"）时不说两遍
        detail = detail[len(lead) :].lstrip("，")
    if lead and detail:
        return f"{lead}：{detail}"
    return lead or detail or _FALLBACK


__all__ = [
    "KIND_TO_EVENT",
    "RESOLVED_KINDS",
    "biome_name",
    "classify",
    "item_name",
    "plain_detail",
    "summarize",
    "without_coordinates",
]
