"""注意流事件的分类表：上游事实 → 值得叙述的遭遇。

## 为什么需要这一层

MaiCraft 的注意流内容很杂：里面既有"被僵尸袭击了""死了""紧急反应接管了"
这类**值得向观众叙述**的遭遇，也有掉了 2 点血、当前 18 血、坐标 x/z、
游标编号这类**遥测**。主播的职责是叙述现在在干什么、遭遇了什么——只有前者
对它有价值，后者塞进去只会污染叙事并把有用的信息挤掉。

所以 `maicraft_attention` 采集器不只搬运转发，它还做**分类**：只把遭遇
转成 `game.body.*` 事件，遥测留在上游（需要时用工具直读游戏状态）。

## 契约

- `classify()` 返回 `None` = 这条上游事件不进入叙事通道（世界时间/天气等）；
- 未知上游类型归 `unknown` 且保留 `source_event_type`，所以**上游加新事件类型
  不会让本系统的事件面漂移**；
- `summarize()` 只陈述有证据的事实：攻击者类型、命中次数、阶段。
  不做英文原文翻译，也不推断上游没给的事实——"正在反击"就没有证据，
  本能是否接管只能由 mod 的 `defense` 段回答。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.modules.events.names import CoreEvents

#: 上游时间戳格式（Mod 侧 ISO-8601 带 Z；带/不带毫秒两种）
_UPSTREAM_TIME_FORMATS = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ")

#: 叙事种类 → 事件名（事件名一律引用 CoreEvents 常量，不写字面量）
KIND_TO_EVENT: Dict[str, str] = {
    "attacked": CoreEvents.GAME_BODY_ATTACKED,
    "attack_ended": CoreEvents.GAME_BODY_ATTACK_ENDED,
    "died": CoreEvents.GAME_BODY_DIED,
    "respawned": CoreEvents.GAME_BODY_RESPAWNED,
    "reflex_started": CoreEvents.GAME_BODY_REFLEX_STARTED,
    "reflex_finished": CoreEvents.GAME_BODY_REFLEX_FINISHED,
    "dimension_changed": CoreEvents.GAME_BODY_DIMENSION_CHANGED,
    "unknown": CoreEvents.GAME_BODY_UNKNOWN,
}

#: 已经是"结局面"的种类（主播据此把措辞从"正在"改成"刚才"）
RESOLVED_KINDS = frozenset({"attack_ended", "respawned", "reflex_finished"})

#: 上游类型 → 叙事种类（按阶段细分；未列出的类型归 unknown 保留留痕）
#: 只有"身体侧遭遇"在此表；世界时间/天气与任务事件都不进叙事通道。
_SOURCE_KINDS: Dict[str, Dict[str, str]] = {
    "agent.damaged": {"started": "attacked", "finished": "attack_ended", "": "attacked"},
    "agent.died": {"": "died"},
    "agent.respawned": {"": "respawned"},
    "agent.reflex": {"started": "reflex_started", "finished": "reflex_finished"},
    "agent.dimension_changed": {"": "dimension_changed"},
}

#: 明确不进叙事通道的上游类型（有意的静默；未列出的走 unknown 兜底）
_NON_NARRATIVE_TYPES = frozenset(
    {
        "world.time_phase_changed",
        "world.weather_changed",
        "agent.respawn_requested",
        "agent.respawn_request_failed",
        "agent.death_decision_applied",
    }
)

#: 常见攻击者的中文名（**仅用于生成叙述用词**，不参与任何判定）
#: 查不到就回落到实体 id 的路径段（如 minecraft:zombie → zombie），不猜中文
_ATTACKER_LABELS: Dict[str, str] = {
    "zombie": "僵尸",
    "husk": "尸壳",
    "drowned": "溺尸",
    "skeleton": "骷髅",
    "stray": "流浪者",
    "wither_skeleton": "凋灵骷髅",
    "creeper": "苦力怕",
    "spider": "蜘蛛",
    "cave_spider": "洞穴蜘蛛",
    "enderman": "末影人",
    "witch": "女巫",
    "slime": "史莱姆",
    "magma_cube": "岩浆怪",
    "blaze": "烈焰人",
    "ghast": "恶魂",
    "phantom": "幻翼",
    "pillager": "掠夺者",
    "vindicator": "卫道士",
    "ravager": "劫掠兽",
    "piglin": "猪灵",
    "hoglin": "疣猪兽",
    "wolf": "狼",
    "polar_bear": "北极熊",
    "bee": "蜜蜂",
    "iron_golem": "铁傀儡",
    "player": "玩家",
}


def classify(source_event_type: str, phase: str = "") -> Optional[str]:
    """上游类型（+ 片段阶段）→ 叙事种类；不进入叙事通道返回 ``None``。"""
    source = str(source_event_type or "")
    if not source or source in _NON_NARRATIVE_TYPES:
        return None
    phases = _SOURCE_KINDS.get(source)
    if phases is None:
        return "unknown"  # 上游新增类型：留痕但不丢，事件面保持封闭
    return phases.get(str(phase or ""), phases.get(""))


def attacker_label(entity_type_id: str) -> str:
    """攻击者实体 id → 叙述用词（``minecraft:zombie`` → ``僵尸``）。"""
    path = str(entity_type_id or "").split(":")[-1]
    if not path:
        return "不明来源"
    return _ATTACKER_LABELS.get(path, path)


def summarize(kind: str, source_event_type: str, facts: Dict[str, Any]) -> str:
    """叙事种类 + 上游事实 → 一句可直接讲给观众的话（只陈述有证据的部分）。"""
    cause = facts.get("cause") if isinstance(facts.get("cause"), dict) else {}
    repeat = facts.get("repeat") if isinstance(facts.get("repeat"), dict) else {}
    attacker = str(cause.get("causing_entity_type_id") or "")
    label = attacker_label(attacker) if attacker else ""
    raw_hits = repeat.get("hits")
    hits = int(raw_hits) if isinstance(raw_hits, int) and raw_hits > 1 else 0

    if kind == "attacked":
        if label:
            return f"正在被{label}攻击" + (f"（已命中 {hits} 次）" if hits else "")
        return "受到了伤害" + (f"（已命中 {hits} 次）" if hits else "")
    if kind == "attack_ended":
        if label:
            return f"摆脱了{label}的攻击" + (f"（共命中 {hits} 次）" if hits else "")
        return "不再受到伤害了"
    if kind == "died":
        return "死了"
    if kind == "respawned":
        return "已重生"
    if kind == "reflex_started":
        reflex = str(facts.get("reflex") or "")
        return f"紧急反应接管（{reflex}）" if reflex else "紧急反应接管"
    if kind == "reflex_finished":
        detail = "、".join(part for part in (str(facts.get("reflex") or ""), str(facts.get("outcome") or "")) if part)
        return f"紧急反应结束（{detail}）" if detail else "紧急反应结束"
    if kind == "dimension_changed":
        to_dimension = str(facts.get("to_dimension") or "")
        return f"进入了 {to_dimension}" if to_dimension else "换了个维度"
    return f"身体事件：{source_event_type}" if source_event_type else "身体事件"


def upstream_timestamp_ms(raw: Any) -> int:
    """上游注意流时间戳（ISO-8601 带 Z）→ Unix 毫秒；无法解析返回 0。

    只做搬运：解析不出来宁可写 0（未知），也不拿"现在"冒充上游时刻。
    """
    if not isinstance(raw, str) or not raw:
        return 0
    text = raw.strip()
    for fmt in _UPSTREAM_TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            continue
    return 0


__all__ = [
    "KIND_TO_EVENT",
    "RESOLVED_KINDS",
    "attacker_label",
    "classify",
    "summarize",
    "upstream_timestamp_ms",
]
