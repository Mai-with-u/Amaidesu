"""按存档筛选与缓存多人世界的公共社交规矩（供 MinecraftAgent 只读注入任务上下文）。

多人联机存档中存在玩家在游戏公屏明说的共享惯例、环境形态、同伴关系与正在推进的工程。
本模块仅消费注入的社交事实仓储只读查询结果，将其转换为紧凑中文单行规矩：
- 只收对游戏内行动边界有指导价值的四类（共享惯例、环境形态、成员关系、进行中的事），
  跳过角色定位归纳与待弄清疑问；
- 拦截未经人工锁定的无引文归纳行与观众弹幕作证行，只放行有游戏公屏逐字引文依据的现行行
  或运营者人工锁定的现行行，防止直播间弹幕投毒篡改游戏内行动规矩；
- 按存档编号隔离缓存，换存档时先清空旧存档规矩再加载新存档，杜绝旧世界规矩串入新世界。
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Tuple

__all__ = [
    "MAX_SOCIAL_RULE_CHARS",
    "MAX_WORLD_SOCIAL_RULES",
    "SOCIAL_RULES_MSG_KEY",
    "WORLD_SOCIAL_RULES_KEY",
    "WorldSocialRulesCache",
    "format_world_social_rule",
    "is_eligible_world_social_fact",
    "select_world_social_rules",
]

# 任务上下文与新任务开局事实中的公共规矩键名
WORLD_SOCIAL_RULES_KEY = "world_social_rules"
# 新任务开局附加公共规矩消息的内部标记：任务中途换存档时据此清退旧存档规矩消息
SOCIAL_RULES_MSG_KEY = "_minecraft_world_social_rules"

# 单次注入条数上限与单条字符上限（不附长引文、不带数据库主键，控制上下文体积）
MAX_WORLD_SOCIAL_RULES = 6
MAX_SOCIAL_RULE_CHARS = 80

# 对游戏内行动边界有指导价值的四类事实及其紧凑中文前缀
_KIND_LABELS: dict[str, str] = {
    "sharing": "共享惯例",
    "setting": "环境形态",
    "members": "成员关系",
    "works": "进行中的事",
}

# 注入优先级：先保共享设施规矩与环境形态，其次成员关系与正在推进的工程；同类保留仓储由新到旧顺序
_KIND_PRIORITY: dict[str, int] = {
    "sharing": 0,
    "setting": 1,
    "members": 2,
    "works": 3,
}


def is_eligible_world_social_fact(row: Any, *, save_id: str) -> bool:
    """判定一条社交事实行能否作为当前存档的公共规矩交给游戏侧执行。"""
    if not save_id:
        return False
    if getattr(row, "status", "active") != "active":
        return False
    row_game = str(getattr(row, "game", "") or "").strip()
    if row_game and row_game != "minecraft":
        return False
    row_save = str(getattr(row, "save_id", "") or "").strip()
    if row_save and row_save != save_id:
        return False
    kind = str(getattr(row, "kind", "") or "")
    if kind not in _KIND_LABELS:
        return False
    clean_text = " ".join(str(getattr(row, "text", "") or "").split())
    if not clean_text:
        return False
    # 运营者人工锁定的现行行直接放行；非人工锁定行必须具备非空逐字引文、绑定游戏公屏出处且非观众弹幕出处
    locked = int(getattr(row, "human_locked", 0) or 0)
    if locked == 1:
        return True
    if locked != 0:
        return False
    has_quote = bool((getattr(row, "quote", "") or "").strip())
    return (
        has_quote
        and getattr(row, "evidence_game_chat_id", None) is not None
        and getattr(row, "evidence_chat_id", None) is None
    )


def format_world_social_rule(row: Any) -> str:
    """将单条合格社交事实格式化为紧凑中文单行（限长 80 字，不附引文与主键）。"""
    label = _KIND_LABELS[str(row.kind)]
    clean_text = " ".join(str(row.text or "").split())
    prefix = f"{label}："
    line = clean_text if clean_text.startswith(prefix) else f"{prefix}{clean_text}"
    if len(line) <= MAX_SOCIAL_RULE_CHARS:
        return line
    return f"{line[: MAX_SOCIAL_RULE_CHARS - 1]}…"


def select_world_social_rules(rows: Iterable[Any], *, save_id: str) -> List[str]:
    """筛选当前存档可执行的社交事实，按类别优先级稳定排序并截取最多 6 条去重单行规矩。"""
    eligible = [row for row in rows if is_eligible_world_social_fact(row, save_id=save_id)]
    eligible.sort(key=lambda row: _KIND_PRIORITY[str(row.kind)])
    lines: List[str] = []
    seen: set[str] = set()
    for row in eligible:
        line = format_world_social_rule(row)
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= MAX_WORLD_SOCIAL_RULES:
            break
    return lines


class WorldSocialRulesCache:
    """当前存档公共规矩的内存只读缓存：认出存档与新开委派任务时异步刷新，推理步内零查库。"""

    def __init__(self) -> None:
        self._save_id: str = ""
        self._rules: Tuple[str, ...] = ()
        self._load_seq: int = 0

    @property
    def load_seq(self) -> int:
        """累计刷新序号：供新任务开局判断是否刚在开局代读认出存档时刷新过，避免重复查库。"""
        return self._load_seq

    def clear(self) -> None:
        """清空已缓存的存档编号与公共规矩（换存档时先行清退，防止旧世界规矩残留）。"""
        self._save_id = ""
        self._rules = ()

    def view(self, current_save_id: str) -> List[str]:
        """按角色当前所在存档编号读取缓存规矩；未认出存档或存档编号不匹配时返回空列表。"""
        if not current_save_id or self._save_id != current_save_id:
            return []
        return list(self._rules)

    async def refresh(self, repo: Optional[Any], *, game: str, save_id: str, logger: Any) -> List[str]:
        """角色已认出具体存档且仓储已注入时异步加载现行社交事实并刷新内存缓存。"""
        if not save_id or repo is None:
            self.clear()
            return []
        if self._save_id != save_id:
            # 切入新存档时先丢弃旧存档缓存 -> 再读新存档，读库异常也不会把旧存档规矩留给新世界
            self.clear()
        self._load_seq += 1
        try:
            rows = await repo.list_active_facts(game=game, save_id=save_id)
        except Exception as exc:  # noqa: BLE001 - 社交事实只读增强失败降级为空列表，不阻断游戏行动循环
            logger.warning(f"读存档公共社交事实失败（{save_id}），按无公共规矩继续", exc=exc)
            self._save_id = save_id
            self._rules = ()
            return []
        selected = select_world_social_rules(rows, save_id=save_id)
        self._save_id = save_id
        self._rules = tuple(selected)
        return selected
