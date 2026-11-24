"""无玩家引导期自驱节奏控制与长搜寻止损提示（Minecraft 游戏侧纯逻辑模块）。

职责边界：
1. **原地内耗逃逸阀（``SoloPacingTracker``）**：
   - 在游戏自主主控期间持续记录最近一次外部互动时刻（游戏公屏、直播间建议、运营提醒）、
     角色水平位置样本以及近期目标终态（正常推进 vs 失败/取消/清包修补类维护）。
   - 当连续较长时间无外部引导，且活动半径局限在小范围或近期失败/维护占比偏高时，
     按冷却周期产出一条开放式换口味软提醒，引导结合当前存档的 FTB 未完成任务、
     周边未探索群系或建筑结构、或可用联动机器自选新鲜小项目。
   - 一旦有新的外部互动或角色位置大幅拓展（跨维度或超出水平半径阈值），自动重置计数。
2. **长搜寻止损提示（``build_search_stop_loss_hint``）**：
   - 追踪在跑搜寻/获取类长目标（如 ``obtain``/``find``/``gather``）的开始时刻；
   - 在挂起无进展看门狗唤醒时，若目标总耗时或连续无进展超过阈值，附加一段开放式止损建议，
     鼓励评估是否取消并改换生态群系、切换途径或转做其他事项，避免单个目标空耗过久。

时刻统一使用注入的单调时钟（秒）；纯内存无外部依赖，非线程安全，仅在 Agent 事件循环内调用。
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional, Sequence, Tuple

from src.agents.minecraft.wait_progress import Coords

__all__ = [
    "DEFAULT_CHURN_MIN_SAMPLES",
    "DEFAULT_CHURN_RATIO",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_GOAL_WINDOW_SIZE",
    "DEFAULT_SEARCH_STOP_LOSS_S",
    "DEFAULT_SMALL_RADIUS_BLOCKS",
    "DEFAULT_SOLO_IDLE_WINDOW_S",
    "SEARCH_GOAL_ABILITIES",
    "SoloPacingTracker",
    "TrackedGoalStart",
    "build_search_stop_loss_hint",
    "is_churn_or_maintenance_goal",
    "is_search_like_ability",
]

#: 连续多久无外部玩家/观众/运营引导才考虑触发换口味软提醒（秒，默认 15 分钟）
DEFAULT_SOLO_IDLE_WINDOW_S = 900.0
#: 换口味软提醒的最小冷却间隔（秒，默认 15 分钟至多一次）
DEFAULT_COOLDOWN_S = 900.0
#: 判定“活动半径局限在小范围”的水平位移阈值（格）
DEFAULT_SMALL_RADIUS_BLOCKS = 32.0
#: 判定“近期失败/取消/维护占比偏高”的比例下限与最少样本数
DEFAULT_CHURN_RATIO = 0.4
DEFAULT_CHURN_MIN_SAMPLES = 6
#: 近期目标终态滑动窗口大小
DEFAULT_GOAL_WINDOW_SIZE = 12
#: 搜寻/获取类长目标触发止损建议的总耗时或连续无进展阈值（秒，默认 120 秒）
DEFAULT_SEARCH_STOP_LOSS_S = 120.0

#: 视作搜寻/获取类长目标的能力短名集合（剥离命名空间前缀后比对）
SEARCH_GOAL_ABILITIES = frozenset({"obtain", "find", "gather", "search", "explore", "locate", "collect"})

# 失败/取消/未完成终态集合
_NON_SUCCESS_STATUSES = frozenset({"failed", "cancelled", "partial", "gone"})
# 清包、存箱、修补与日常维持类能力短名（即使成功完成，反复集中在这些动作上也属于内耗维护）
_MAINTENANCE_ABILITIES = frozenset(
    {
        "sort_inventory",
        "deposit",
        "store",
        "stash",
        "organize",
        "repair",
        "equip",
        "eat",
        "heal",
        "drop",
        "toss",
        "clean",
    }
)
# 清包/整理/修补类目标目的关键词（命中时归入维护样本）
_MAINTENANCE_PURPOSE_KEYWORDS = (
    "清包",
    "整理背包",
    "整理箱子",
    "存箱",
    "放箱",
    "归置",
    "修补",
    "修理",
    "修复",
    "补血",
    "吃东西",
    "换装备",
    "丢垃圾",
)
# 已记录目标终态的去重容量：避免同一 goal_id 从 execute 回执与事件流重复记账
_SEEN_OUTCOME_GOAL_LIMIT = 64
# 位置样本环形缓冲上限
_MAX_POSITION_SAMPLES = 64


def _ability_short_name(ability: str) -> str:
    """提取去掉命名空间前缀的能力短名（如 ``maicraft:obtain`` → ``obtain``）。"""
    return str(ability or "").strip().rsplit(":", 1)[-1].lower()


def is_search_like_ability(ability: str) -> bool:
    """判断一个能力是否属于容易长时间空耗的搜寻/获取类目标。"""
    return _ability_short_name(ability) in SEARCH_GOAL_ABILITIES


def is_churn_or_maintenance_goal(ability: str, purpose: str, status: str) -> bool:
    """判断一次已结束的目标是否属于失败/取消或清包修补类维护操作。

    - 失败、取消、半途而废或目标失踪直接记为内耗样本；
    - 虽返回 ``done``，但属于整理背包、存箱、修补或基础补给类重复维护的，也记为维护样本；
    - 正常推进探索、建造、加工、合成、交互等成功目标记为有效推进（``False``）。
    """
    normalized_status = str(status or "").strip().lower()
    if normalized_status in _NON_SUCCESS_STATUSES:
        return True
    short = _ability_short_name(ability)
    if short in _MAINTENANCE_ABILITIES:
        return True
    purpose_text = " ".join(str(purpose or "").split())
    return any(keyword in purpose_text for keyword in _MAINTENANCE_PURPOSE_KEYWORDS)


def build_search_stop_loss_hint(
    *,
    search_labels: Sequence[str],
    max_elapsed_s: float,
    stalled_s: float,
    threshold_s: float = DEFAULT_SEARCH_STOP_LOSS_S,
) -> str:
    """当挂起唤醒涉及搜寻/获取类长目标且耗时或停滞超阈值时，生成开放式止损建议；未命中返回空串。"""
    if not search_labels:
        return ""
    if max(max_elapsed_s, stalled_s) < threshold_s:
        return ""
    duration_s = int(max(max_elapsed_s, stalled_s))
    targets = "、".join(search_labels[:2])
    return (
        f" 提示：搜寻/获取类目标（{targets}）已累计约 {duration_s} 秒未见完成；"
        "若现场仍缺目标资源或地形受限，可主动评估是否取消该目标，"
        "改换生态群系、切换获取途径或先转做其他新鲜事项，避免在单个目标上长时间空耗。"
    )


@dataclass(frozen=True, slots=True)
class TrackedGoalStart:
    """在跟踪目标的开始元数据：用于计算总耗时与识别搜寻/获取类长目标。"""

    goal_id: int
    ability: str
    purpose: str
    started_at: float


class SoloPacingTracker:
    """游戏自主主控期的无引导节奏监视器与在跑目标耗时台账。"""

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        solo_idle_window_s: float = DEFAULT_SOLO_IDLE_WINDOW_S,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        small_radius_blocks: float = DEFAULT_SMALL_RADIUS_BLOCKS,
        churn_ratio: float = DEFAULT_CHURN_RATIO,
        churn_min_samples: int = DEFAULT_CHURN_MIN_SAMPLES,
        goal_window_size: int = DEFAULT_GOAL_WINDOW_SIZE,
        search_stop_loss_s: float = DEFAULT_SEARCH_STOP_LOSS_S,
    ) -> None:
        self._clock = clock
        self._solo_idle_window_s = max(1.0, float(solo_idle_window_s))
        self._cooldown_s = max(1.0, float(cooldown_s))
        self._small_radius_blocks = max(1.0, float(small_radius_blocks))
        self._churn_ratio = max(0.0, min(1.0, float(churn_ratio)))
        self._churn_min_samples = max(1, int(churn_min_samples))
        self._goal_window_size = max(self._churn_min_samples, int(goal_window_size))
        self._search_stop_loss_s = max(1.0, float(search_stop_loss_s))

        self._active: bool = False
        self._last_external_interaction_at: Optional[float] = None
        self._last_nudge_at: Optional[float] = None
        # 水平位置样本：(单调时刻秒, 维度, x, z)；首条即为当前统计窗口的锚点
        self._positions: Deque[Tuple[float, str, float, float]] = deque(maxlen=_MAX_POSITION_SAMPLES)
        # 近期目标终态样本（True=失败/取消/维护，False=正常推进）
        self._goal_outcomes: Deque[bool] = deque(maxlen=self._goal_window_size)
        self._seen_outcome_goals: Deque[int] = deque(maxlen=_SEEN_OUTCOME_GOAL_LIMIT)
        # 在跑目标开始时刻台账（不限主控态，委派态同样可为长搜寻止损提供准确总耗时）
        self._tracked_goals: Dict[int, TrackedGoalStart] = {}

    @property
    def active(self) -> bool:
        """当前是否处于游戏自主主控期的节奏监控态。"""
        return self._active

    def enter_lead(self) -> None:
        """进入游戏自主主控态：以当前时刻作为无引导计时起点，清空旧窗口样本。"""
        now = float(self._clock())
        self._active = True
        self._last_external_interaction_at = now
        self._last_nudge_at = None
        self._positions.clear()
        self._goal_outcomes.clear()
        self._seen_outcome_goals.clear()

    def reset(self, *, clear_tracked_goals: bool = True) -> None:
        """退出主控、下播或换存档时复位节奏监控状态。"""
        self._active = False
        self._last_external_interaction_at = None
        self._last_nudge_at = None
        self._positions.clear()
        self._goal_outcomes.clear()
        self._seen_outcome_goals.clear()
        if clear_tracked_goals:
            self._tracked_goals.clear()

    def note_external_interaction(self) -> None:
        """收到游戏公屏、直播间建议或运营/主播提醒：刷新互动时刻并重置内耗样本窗口。"""
        if not self._active:
            return
        self._last_external_interaction_at = float(self._clock())
        self._positions.clear()
        self._goal_outcomes.clear()

    def note_position(self, coords: Optional[Coords]) -> None:
        """记录角色最新坐标样本：若跨维度或水平移动超过半径阈值，视为位置大幅拓展并重置内耗计数。"""
        if not self._active or coords is None:
            return
        now = float(self._clock())
        dim, x, _y, z = coords
        self._prune_expired_positions(now)
        if self._positions:
            _t0, anchor_dim, anchor_x, anchor_z = self._positions[0]
            # 跨维度或离开当前锚点超过小范围半径 -> 角色已主动拓展活动区域，重置无引导内耗窗口
            if dim != anchor_dim or math.hypot(x - anchor_x, z - anchor_z) > self._small_radius_blocks:
                self._last_external_interaction_at = now
                self._positions.clear()
                self._goal_outcomes.clear()
        self._positions.append((now, dim, x, z))

    def note_goal_started(self, goal_id: int, ability: str, purpose: str = "") -> None:
        """登记后台目标开始时刻，供无进展唤醒时计算搜寻/获取类长目标总耗时。"""
        if goal_id in self._tracked_goals:
            return
        self._tracked_goals[goal_id] = TrackedGoalStart(
            goal_id=goal_id,
            ability=str(ability or "").strip(),
            purpose=str(purpose or "").strip(),
            started_at=float(self._clock()),
        )

    def forget_goal(self, goal_id: int) -> None:
        """移除已取消或不再跟踪的目标开始记录。"""
        self._tracked_goals.pop(goal_id, None)

    def note_goal_finished(
        self,
        goal_id: Optional[int],
        ability: str,
        purpose: str = "",
        status: str = "",
    ) -> None:
        """记录一次目标终态：清掉在跑台账，并在主控期按 goal_id 去重写入内耗/推进样本窗口。"""
        if goal_id is not None:
            self._tracked_goals.pop(goal_id, None)
        if not self._active:
            return
        if goal_id is not None:
            if goal_id in self._seen_outcome_goals:
                return
            self._seen_outcome_goals.append(goal_id)
        is_churn = is_churn_or_maintenance_goal(ability, purpose, status)
        self._goal_outcomes.append(is_churn)

    def maybe_build_nudge(self) -> Optional[str]:
        """评估是否命中“无引导原地内耗逃逸阀”：满足条件且过冷却时返回开放式换口味软提醒，否则返回 None。"""
        if not self._active or self._last_external_interaction_at is None:
            return None
        now = float(self._clock())
        idle_s = now - self._last_external_interaction_at
        if idle_s < self._solo_idle_window_s:
            return None
        if self._last_nudge_at is not None and (now - self._last_nudge_at) < self._cooldown_s:
            return None

        self._prune_expired_positions(now)
        small_radius = self._is_confined_to_small_radius()
        high_churn = self._has_high_churn_ratio()
        if not (small_radius or high_churn):
            return None

        self._last_nudge_at = now
        idle_minutes = max(1, int(idle_s // 60))
        reasons: list[str] = []
        if small_radius:
            reasons.append("近期活动范围一直局限在附近小片区域内")
        if high_churn:
            churn_count = sum(1 for flag in self._goal_outcomes if flag)
            reasons.append(f"最近 {len(self._goal_outcomes)} 个目标中有 {churn_count} 个属于失败、取消或维护类杂活")
        reason_text = "，且".join(reasons)
        return (
            f"[节奏提醒] 已经连续约 {idle_minutes} 分钟没有收到外部玩家或直播间引导，且{reason_text}。"
            "不必把整段时间都耗在原地反复收拾或重复杂活上；可以结合当前存档里的 FTB 未完成任务、"
            "周边尚未探索的群系或建筑结构，或是可用的联动机器，自主挑选一件新鲜有趣的小项目换换口味"
            "（若眼下有正在收尾的关键工序，做完再换即可）。"
        )

    def search_stop_loss_hint(self, goal_ids: Sequence[int], stalled_s: float) -> str:
        """根据在跑目标开始时刻与当前停滞时长，仅为自身已达止损阈值的搜寻/获取类长目标生成开放式止损建议。"""
        now = float(self._clock())
        overdue_items: list[Tuple[float, str]] = []
        max_elapsed_s = 0.0
        for gid in goal_ids:
            meta = self._tracked_goals.get(gid)
            if meta is None or not is_search_like_ability(meta.ability):
                continue
            elapsed_s = max(0.0, now - meta.started_at)
            # 多搜寻目标并跑时，只把自身总耗时已达止损阈值的目标列入提示，不把刚启动的新目标误标为超长空耗
            if elapsed_s < self._search_stop_loss_s:
                continue
            max_elapsed_s = max(max_elapsed_s, elapsed_s)
            label = f"{meta.ability}（{meta.purpose}）" if meta.purpose else meta.ability
            overdue_items.append((elapsed_s, label))
        overdue_items.sort(key=lambda item: item[0], reverse=True)
        search_labels = [label for _elapsed, label in overdue_items]
        return build_search_stop_loss_hint(
            search_labels=search_labels,
            max_elapsed_s=max_elapsed_s,
            stalled_s=max(0.0, float(stalled_s)),
            threshold_s=self._search_stop_loss_s,
        )

    def _prune_expired_positions(self, now: float) -> None:
        """裁掉早于统计窗口的历史坐标样本（至少保留最近 1 条以便识别原地不动）。"""
        cutoff = now - self._solo_idle_window_s
        while len(self._positions) > 1 and self._positions[0][0] < cutoff:
            self._positions.popleft()

    def _is_confined_to_small_radius(self) -> bool:
        """当前窗口内存在坐标样本且全部落在锚点水平半径阈值内。"""
        if not self._positions:
            return False
        _t0, anchor_dim, anchor_x, anchor_z = self._positions[0]
        for _ts, dim, x, z in self._positions:
            if dim != anchor_dim:
                return False
            if math.hypot(x - anchor_x, z - anchor_z) > self._small_radius_blocks:
                return False
        return True

    def _has_high_churn_ratio(self) -> bool:
        """近期目标终态样本数达到下限且失败/取消/维护占比达到或超过阈值。"""
        total = len(self._goal_outcomes)
        if total < self._churn_min_samples:
            return False
        churn = sum(1 for flag in self._goal_outcomes if flag)
        return (churn / total) >= self._churn_ratio
