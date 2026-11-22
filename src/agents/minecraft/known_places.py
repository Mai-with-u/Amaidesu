"""世界设施短目录构造器（供 MinecraftAgent 尾部状态轻量注入）。

以精简目录树形态呈现当前存档里已登记的核心设施，只保留名称、分类与维度分组，
不携带坐标、距离或施工细节，严格控制上下文体积与噪声：
- 防御性过滤：跳过空白名以及 ``建筑`` / ``机器`` / ``建筑@...`` / ``机器@...`` 自动无名档并去重；
- 维度与分类优先级：当前维度优先，按 ``places``（命名地标）> ``stations``（箱式加工站）
  > ``regions``（玩家地盘）> ``machines``（机器）> ``buildings``（建筑）装入，
  跨维度仅保留 ``place`` / ``station`` / ``region`` / ``machine`` 记入 ``other_dimensions``；
- 体积硬上限：总条目不超过 ``MAX_KNOWN_PLACES``（默认 24），超出时附 ``omitted_count``；
  全部为空时返回空字典 ``{}``，零 token 占用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Set, Tuple

__all__ = [
    "KNOWN_PLACES_KEY",
    "MAX_KNOWN_PLACES",
    "build_known_places_catalog",
    "is_anonymous_archive_name",
    "short_dimension_name",
]

# 尾部 [当前任务状态] 中挂载世界设施短目录的键名
KNOWN_PLACES_KEY = "known_places"
# 短目录硬上限：按当前维度核心设施优先填满即停，其余折叠为 omitted_count
MAX_KNOWN_PLACES = 24

# 自动生成的无名建筑/机器档案精确名与前缀（仅过滤明确匿名模式，不靠脆弱正则猜中文名）
_ANONYMOUS_EXACT_NAMES = frozenset({"建筑", "机器"})
_ANONYMOUS_PREFIXES = ("建筑@", "机器@")


def is_anonymous_archive_name(name: str) -> bool:
    """判断名称是否为空白或 Mod 自动生成的匿名建筑/机器档名（如 ``建筑@x,y,z``、``机器@x,y,z``）。"""
    cleaned = str(name or "").strip()
    if not cleaned:
        return True
    if cleaned in _ANONYMOUS_EXACT_NAMES:
        return True
    return cleaned.startswith(_ANONYMOUS_PREFIXES)


def short_dimension_name(dimension: str) -> str:
    """提取可读短维度名：原版维度剥离 ``minecraft:`` 前缀，模组维度保留原名。"""
    cleaned = str(dimension or "").strip()
    if cleaned.startswith("minecraft:"):
        return cleaned[len("minecraft:") :]
    return cleaned


def _item_dimension(item: Mapping[str, Any], *, coord_keys: Tuple[str, ...]) -> str:
    """从设施条目的坐标字段或顶层字段提取维度标识；缺失时返回空串。"""
    for key in coord_keys:
        coord = item.get(key)
        if isinstance(coord, Mapping):
            dim = str(coord.get("dimension") or "").strip()
            if dim:
                return dim
    return str(item.get("dimension") or "").strip()


def _is_same_dimension(item_dim: str, current_dim: str) -> bool:
    """判断条目与角色是否位于同一维度；任一方缺失维度信息时按同维度兼容处理。"""
    if not current_dim or not item_dim:
        return True
    return short_dimension_name(item_dim) == short_dimension_name(current_dim)


def _append_unique(target: List[str], seen: Set[str], value: str) -> None:
    """按首次出现顺序追加去重条目，保持上游给出的由近到远顺序。"""
    if value not in seen:
        seen.add(value)
        target.append(value)


def build_known_places_catalog(
    raw_data: Mapping[str, Any],
    *,
    current_dimension: str = "",
    limit: int = MAX_KNOWN_PLACES,
) -> Dict[str, Any]:
    """从 ``observe(what="places")`` 原始回包构建紧凑世界设施短目录。

    执行顺序：提取并清洗各分类条目 -> 按当前维度与跨维度分桶并保序去重 ->
    按 ``places > stations > regions > machines > buildings > other_dimensions`` 装入至上限 ->
    省略空分类键并在存在截断时附加 ``omitted_count``；无有效条目时返回 ``{}``。
    """
    if not isinstance(raw_data, Mapping) or limit <= 0:
        return {}

    current_dim = str(current_dimension or "").strip()

    cur_places: List[str] = []
    cur_stations: List[str] = []
    cur_regions: List[str] = []
    cur_machines: List[str] = []
    cur_buildings: List[str] = []

    other_places: List[str] = []
    other_stations: List[str] = []
    other_regions: List[str] = []
    other_machines: List[str] = []

    seen_cur_places: Set[str] = set()
    seen_cur_stations: Set[str] = set()
    seen_cur_regions: Set[str] = set()
    seen_cur_machines: Set[str] = set()
    seen_cur_buildings: Set[str] = set()

    # 先拆分 places 数组中的命名地标、机器档案与建筑档案（保留 Mod 原有由近到远顺序）
    raw_places = raw_data.get("places")
    if isinstance(raw_places, list):
        for item in raw_places:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if is_anonymous_archive_name(name):
                continue
            kind = str(item.get("kind") or "place").strip().lower()
            item_dim = _item_dimension(item, coord_keys=("position",))
            if _is_same_dimension(item_dim, current_dim):
                if kind == "machine":
                    _append_unique(cur_machines, seen_cur_machines, name)
                elif kind == "building":
                    _append_unique(cur_buildings, seen_cur_buildings, name)
                else:
                    _append_unique(cur_places, seen_cur_places, name)
            else:
                # 跨维度仅保留 place / machine，过滤建筑档案以控体积
                if kind == "building":
                    continue
                label = f"{name}({short_dimension_name(item_dim)})"
                if kind == "machine":
                    other_machines.append(label)
                else:
                    other_places.append(label)

    # 提取箱式加工站（stations）：投料箱/出料箱坐标仅用于识别维度，绝不写入目录正文
    raw_stations = raw_data.get("stations")
    if isinstance(raw_stations, list):
        for item in raw_stations:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if is_anonymous_archive_name(name):
                continue
            item_dim = _item_dimension(item, coord_keys=("input", "output", "position"))
            if _is_same_dimension(item_dim, current_dim):
                _append_unique(cur_stations, seen_cur_stations, name)
            else:
                other_stations.append(f"{name}({short_dimension_name(item_dim)})")

    # 提取玩家地盘（regions）：中心坐标仅用于识别维度，不向目录输出坐标或半径
    raw_regions = raw_data.get("regions")
    if isinstance(raw_regions, list):
        for item in raw_regions:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if is_anonymous_archive_name(name):
                continue
            item_dim = _item_dimension(item, coord_keys=("center", "position"))
            if _is_same_dimension(item_dim, current_dim):
                _append_unique(cur_regions, seen_cur_regions, name)
            else:
                other_regions.append(f"{name}({short_dimension_name(item_dim)})")

    # 合并跨维度条目并按 place > station > region > machine 保序去重
    other_dimensions: List[str] = []
    seen_other: Set[str] = set()
    for pool in (other_places, other_stations, other_regions, other_machines):
        for label in pool:
            _append_unique(other_dimensions, seen_other, label)

    buckets: List[Tuple[str, List[str]]] = [
        ("places", cur_places),
        ("stations", cur_stations),
        ("regions", cur_regions),
        ("machines", cur_machines),
        ("buildings", cur_buildings),
        ("other_dimensions", other_dimensions),
    ]

    total_eligible = sum(len(items) for _, items in buckets)
    if total_eligible == 0:
        return {}

    # 按严格优先级装入至硬上限即停，空分类键一律省略
    catalog: Dict[str, Any] = {}
    remaining = int(limit)
    taken_total = 0
    for key, items in buckets:
        if not items or remaining <= 0:
            continue
        chosen = items[:remaining]
        catalog[key] = chosen
        taken_total += len(chosen)
        remaining -= len(chosen)

    omitted = total_eligible - taken_total
    if omitted > 0:
        catalog["omitted_count"] = omitted
    return catalog
