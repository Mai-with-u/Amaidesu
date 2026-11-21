"""实例已接入的模组能力、带电装备进度条识别、连锁挖矿统计与取材留坑回填事实。

解析 ``observe(what=self)`` 可选的 ``mod_features``、``last_chain_mining``，以及 ``obtain`` / ``gather``
目标结果 ``details`` 里的 ``chain_mining`` 与 ``pits``，并提供带电装备低电量提醒判定：
- Mod 还没报对应字段时一律返回 ``None``，调用方不输出空键、不报错；
- 格式化结果控制在单行短句，只放进结构化速览字段、工作文档快照与任务事实，不进一句话现场（``brief``），
  避免挤占血量、敌情与身边同伴的核心空间。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "WorldMiningFacts",
    "chain_mining_text",
    "goal_mining_and_pits_notes",
    "is_powered_gear",
    "low_gear_bar_note",
    "mod_features_text",
    "pits_text",
]

#: Mod 报的实例能力代号 → 中文短名（未知新代号原样保留，向后兼容）
_MOD_FEATURE_NAMES: Dict[str, str] = {
    "chain_mining": "连锁破坏",
    "lootr": "独立战利品箱",
    "create": "机械动力",
    "ae2": "应用能源2",
    "appmek": "应用机械",
    "mekanism": "通用机械",
    "sophisticated_backpacks": "精妙背包",
    "touhou_little_maid": "车万女仆",
    "ftb_quests": "FTB任务书",
    "sable": "Sable载具",
}

#: AE2 里用进度条显示剩余电量而非耐久的固定物品路径（不含消耗耐久的石英切割刀与石英工具）
_AE2_POWERED_ITEM_PATHS: frozenset[str] = frozenset(
    {
        "matter_cannon",
        "entropy_manipulator",
        "color_applicator",
        "charged_staff",
        "energy_cell",
        "dense_energy_cell",
    }
)
#: AE2 带电便携存储元件与无线终端路径前缀
_AE2_POWERED_ITEM_PREFIXES: tuple[str, ...] = ("portable_", "wireless_")
#: 应用机械（Applied Mekanistics）带电便携化学品元件路径前缀
_APPMEK_POWERED_ITEM_PREFIXES: tuple[str, ...] = ("portable_chemical_cell_",)

#: 备注里可能带的括号坐标，进速览与委派摘要前剥掉以免主播照念坐标数字
_PARENTHESIZED_COORDINATES = re.compile(
    r"\s*[（(]\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*[)）]"
)
_NOTE_MAX_CHARS = 28


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _clean_note(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    stripped = _PARENTHESIZED_COORDINATES.sub("", raw).strip().rstrip("。；;")
    if len(stripped) <= _NOTE_MAX_CHARS:
        return stripped
    return f"{stripped[:_NOTE_MAX_CHARS]}…"


def is_powered_gear(raw_item: str) -> bool:
    """判断物品的进度条是否表示模组电量（而非原版物品耐久）。

    AE2 便携元件、无线终端与带电工具及应用机械便携化学品元件都借物品进度条显示剩余能量，
    而同属 AE2 的石英切割刀（``*_quartz_cutting_knife``）与石英工具仍消耗普通耐久，不归入此类。
    """
    text = raw_item.strip().lower()
    namespace, sep, path = text.partition(":")
    if not sep or not path:
        return False
    # 命中已知带电模组命名空间与物品族时按电量处理，其余（含石英切割刀与原版工具）默认按耐久处理
    if namespace == "ae2":
        if path.endswith("_quartz_cutting_knife"):
            return False
        return path in _AE2_POWERED_ITEM_PATHS or path.startswith(_AE2_POWERED_ITEM_PREFIXES)
    if namespace == "appmek":
        return path.startswith(_APPMEK_POWERED_ITEM_PREFIXES)
    return False


def low_gear_bar_note(raw_item: str, percent: int) -> str:
    """将手持或护甲栏位低比例进度条格式化为中文提醒（带电装备报低电量，其余报低耐久）。"""
    label = "低电量" if is_powered_gear(raw_item) else "低耐久"
    return f"（{label}，剩 {percent}%）"


def mod_features_text(data: Dict[str, Any]) -> Optional[str]:
    """``observe(what=self)`` 顶层 ``mod_features`` → 一行中文能力清单；Mod 没报该字段时返回 ``None``。"""
    if not isinstance(data, dict) or "mod_features" not in data:
        return None
    raw = data.get("mod_features")
    if not isinstance(raw, list):
        return None
    # 角色进世界后 Mod 报空数组表示当前世界没有任何额外联动就绪，明写"无额外联动"供被问起时如实答
    if not raw:
        return "无额外联动"
    labels: List[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        key = item.strip()
        label = _MOD_FEATURE_NAMES.get(key, key)
        if label not in labels:
            labels.append(label)
    return "、".join(labels) if labels else "无额外联动"


def chain_mining_text(raw: Any) -> Optional[str]:
    """``last_chain_mining`` 或 ``details.chain_mining`` → 一行连锁统计短句；未报或格式不符时返回 ``None``。"""
    if not isinstance(raw, dict):
        return None
    batches = _int_or_none(raw.get("chained_batches"))
    blocks = _int_or_none(raw.get("chained_blocks"))
    single = _int_or_none(raw.get("single_blocks"))
    reason = _clean_note(raw.get("not_chained_reason"))
    if batches is None and blocks is None and single is None and not reason:
        return None
    batches_n = max(0, batches or 0)
    blocks_n = max(0, blocks or 0)
    single_n = max(0, single or 0)
    # 连上了至少一批 -> 先报批数与格数，再附单挖与部分未连原因；一批都没连上 -> 直报单挖格数与未触发原因
    if batches_n > 0:
        parts = [f"连锁 {batches_n} 批共 {blocks_n} 格"]
        if single_n > 0:
            parts.append(f"单挖 {single_n} 格")
        head = "、".join(parts)
        return f"{head}（部分未连：{reason}）" if reason else head
    head = f"单挖 {single_n} 格，未触发连锁" if single_n > 0 else "未触发连锁"
    return f"{head}（{reason}）" if reason else head


def pits_text(raw: Any) -> Optional[str]:
    """``details.pits`` → 一行取材留坑与回填情况；没挖出深坑或字段缺省时返回 ``None``。"""
    if not isinstance(raw, list) or not raw:
        return None
    pits = [item for item in raw if isinstance(item, dict)]
    if not pits:
        return None
    total = len(pits)
    filled_count = sum(1 for item in pits if item.get("filled") is True)
    unfilled_count = total - filled_count
    depths = [d for d in (_int_or_none(item.get("depth")) for item in pits) if d is not None and d > 0]
    max_depth = max(depths) if depths else None
    depth_part = f"深 {max_depth} 格" if total == 1 and max_depth else (f"最深 {max_depth} 格" if max_depth else "")
    # 优先摘未填平那一处的说明，全部填平时摘第一处回填说明，让主播和游戏 Agent 知道填了什么或为什么没填
    preferred = next((item for item in pits if item.get("filled") is not True), pits[0])
    note = _clean_note(preferred.get("note"))
    detail = "：".join(part for part in (depth_part, note) if part)
    suffix = f"（{detail}）" if detail else ""
    if unfilled_count == 0:
        return f"顺手回填留坑 {total} 处{suffix}"
    if filled_count == 0:
        return f"留坑 {unfilled_count} 处未填平{suffix}"
    return f"留坑 {total} 处（已填 {filled_count} 处、未填 {unfilled_count} 处{f'，{detail}' if detail else ''}）"


def goal_mining_and_pits_notes(result: Any) -> Dict[str, str]:
    """从目标终态 ``result`` 里提取可选的 ``chain_mining`` 与 ``pits`` 短句字典（无则空 dict）。"""
    if not isinstance(result, dict):
        return {}
    details = result.get("details")
    if not isinstance(details, dict):
        return {}
    out: Dict[str, str] = {}
    chain = chain_mining_text(details.get("chain_mining"))
    if chain:
        out["chain_mining"] = chain
    pits = pits_text(details.get("pits"))
    if pits:
        out["pits"] = pits
    return out


@dataclass
class _WorldEntry:
    mod_features: Optional[str] = None
    last_chain_mining: Optional[str] = None
    recent_pits: Optional[str] = None


@dataclass
class WorldMiningFacts:
    """按存档记最近观测到的实例能力、连锁统计与取材留坑情况；换存档天然隔离。"""

    _by_world: Dict[str, _WorldEntry] = field(default_factory=dict)

    def _slot(self, world_key: Optional[str]) -> _WorldEntry:
        key = world_key or ""
        entry = self._by_world.get(key)
        if entry is None:
            entry = _WorldEntry()
            self._by_world[key] = entry
        return entry

    def absorb_self(self, world_key: Optional[str], data: Any) -> None:
        """读到 ``observe(what=self)`` -> 同步本存档的模组能力与最近一次连锁挖矿统计。"""
        if not isinstance(data, dict):
            return
        slot = self._slot(world_key)
        features = mod_features_text(data)
        if features is not None:
            slot.mod_features = features
        chain = chain_mining_text(data.get("last_chain_mining"))
        if chain is not None:
            slot.last_chain_mining = chain

    def absorb_goal_result(self, world_key: Optional[str], result: Any) -> None:
        """采掘目标结束 -> 用 ``details.chain_mining`` 与 ``details.pits`` 刷新本存档最近挖掘与留坑记录。"""
        notes = goal_mining_and_pits_notes(result)
        if not notes:
            return
        slot = self._slot(world_key)
        if "chain_mining" in notes:
            slot.last_chain_mining = notes["chain_mining"]
        if "pits" in notes:
            slot.recent_pits = notes["pits"]

    def view(self, world_key: Optional[str]) -> Dict[str, str]:
        """当前存档已记下的非空事实（未报过的键一律缺省，不占上下文）。"""
        entry = self._by_world.get(world_key or "")
        if entry is None:
            return {}
        out: Dict[str, str] = {}
        if entry.mod_features is not None:
            out["mod_features"] = entry.mod_features
        if entry.last_chain_mining is not None:
            out["last_chain_mining"] = entry.last_chain_mining
        if entry.recent_pits is not None:
            out["recent_pits"] = entry.recent_pits
        return out
