"""AgendaState - 节目单运行时状态机 + Storage 适配（Wave 6 / §1.7）

职责边界（重要）
----------------
本模块**只**回答"当前节目单跑到哪了、整场进度几分之几、
能不能暂停/跳转/回退"——返回状态变更/快照 dict，**不**实现调度循环
（``agenda_idle.py``）、**不**调用 LLM（``agenda_loader.py``）、
**不**订阅 EventBus。

设计要点（沿用 ``OutlineState`` / ``RoomState`` 范式）
---------------------------------------------------------
- **可注入时钟**：所有方法接受可选 ``now_ms`` 参数（无参调用退回真实时钟），
  测试中传入确定性时间戳即可重现任意暂停/跳转场景，**禁止**依赖 ``time.sleep``。
- **Storage 适配（Wave 6 新增）**：
  - 原始大纲 → ``agenda_plan`` 表（只读基准）
  - 运行进度 → ``agenda_runtime`` 表（Agent 改）
  - 适配方法：``load_agenda_plan(agenda_id)`` / ``dump_agenda_runtime()`` /
    ``restore_agenda_runtime(runtime_rows)`` —— 读 SQLite 拿到数据，写 SQLite
    持久化运行时。
  - 框架默认接口是 ``agenda_store``（duck-typed，提供上述方法）；可注入。
- **状态机**::

      INACTIVE ──start()──▶ RUNNING ──skip()到末段──▶ COMPLETED ──unload()──▶ UNLOADED
                          │  ◀──rewind()──┘
                          ├─pause()──▶ PAUSED（status 仍为 RUNNING）──resume()──▶ RUNNING
                          │
                          └─unload()──▶ UNLOADED

  - ``LOADING`` 为加载过程保留位；本模块不主动设置（由调用方在 ``start()`` 前置位）。
  - ``PAUSED`` 是 ``RUNNING`` 的子状态（``is_paused=True`` 标志位），status 值仍为 ``RUNNING``。

- **整场时间轴锚点** ``agenda_started_at_ms`` 在 ``start()`` 时记录，``unload()`` 时清除。
  ``get_elapsed_live_ms()`` = ``now - agenda_started_at_ms``，**不**扣除暂停时长
  （暂停期间整场时间按 wall clock 推进，进度百分比被夹取到 100% 兜底）。

- **暂停语义**：``pause()`` 记录 ``_paused_at_ms`` 冻结 segment 时间，``resume()`` 时把
  暂停时长累加到 ``paused_elapsed_ms``。**segment 维度的"剩余时长"** 在暂停期间
  保持不变（不依赖 wall clock 推进），由 ``get_current_segment_remaining_ms()`` 暴露。

- **跳过语义**：``skip()`` 时清除当前环节 ``expanded_cache`` 条目（新段需重新生成），
  并把当前环节 id 加入 ``completed_segment_ids``。

- **跳转语义**：``jump_to(seg)`` 跳到任意环节（不限方向），**不**把当前环节标记为完成；
  若目标不在 ``expanded_cache`` 中 → 加入 ``_needs_expansion`` 集合（idle 据此触发
  即时扩展，扩展完成后 ``cache_expanded()`` 会自动从集合移除）。

- **手动覆盖**：``skip()`` / ``rewind()`` / ``jump_to()`` 隐式设置 ``_manually_overridden=True``，
  调度器（idle）据此跳过下一次自动推进；``advance_to()``（自动路径）**不**设置该标志。

使用方契约
----------
- 调度器（``agenda_idle.py``）：每个 tick 调用 ``advance_to(seg, now_ms=...)`` 推进 + ``get_snapshot()`` 渲染。
- Dashboard：调 ``get_snapshot()`` 展示整场进度条 + 环节卡片；调 ``skip()`` / ``pause()`` /
  ``resume()`` / ``rewind()`` / ``jump_to()`` 实现手动控制按钮。
- Planner/Replyer：通过 ``expanded_cache[seg_id]`` 取扩展内容；通过
  ``get_elapsed_live_ms()`` / ``get_total_planned_ms()`` / ``get_progress_percent()``
  拼装整场进度信息注入 prompt。

不在此处
--------
- 不实现调度循环（agenda_idle）
- 不调用 LLM 生成扩展（agenda_loader）
- 不持久化（delegated to agenda_store；本模块通过 store 接口读写）
- 不订阅 EventBus
- 不实现 Dashboard HTTP API
"""

from __future__ import annotations

from collections import deque
from enum import Enum
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    List,
    Optional,
    Protocol,
    Set,
    runtime_checkable,
)

from src.modules.time_utils import now_ms as _real_now_ms


__all__ = ["AgendaStatus", "AgendaState", "AgendaStore", "AgendaRuntimeRow"]


# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------


class AgendaStatus(Enum):
    """节目单运行时状态枚举。

    转移路径::

        INACTIVE ──start()──▶ RUNNING ──skip()到末段──▶ COMPLETED ──unload()──▶ UNLOADED
                            │  ◀──rewind()──┘
                            └─unload()──▶ UNLOADED

    子状态（``is_paused`` 标志位，``status`` 值仍为 ``RUNNING``）::

        RUNNING ──pause()──▶ RUNNING (paused) ──resume()──▶ RUNNING
    """

    INACTIVE = "inactive"
    """未激活（初始状态 / 卸载后未再次启动）"""

    LOADING = "loading"
    """加载中（保留位；加载期间由调用方置位，本模块不主动设置）"""

    RUNNING = "running"
    """运行中（含 PAUSED 子状态——``is_paused=True`` 标志位）"""

    COMPLETED = "completed"
    """已完成（最后一段走完；``current_segment_id=None``）"""

    UNLOADED = "unloaded"
    """已卸载（``agenda_started_at_ms=None``，等下一次 ``start()`` 视为新一轮）"""


# ---------------------------------------------------------------------------
# Storage 协议（duck-typed；SQLite 实现位于 modules/storage）
# ---------------------------------------------------------------------------


@runtime_checkable
class AgendaStore(Protocol):
    """节目单存储接口（duck-typed Protocol；Wave 6 新增）。

    接口要求：
    - ``load_agenda_plan(agenda_id) -> Optional[dict]``：返回 agenda_plan 行（含 label / order /
      starts_at_ms / expected_ms / note 等字段），不存在返回 None
    - ``dump_agenda_runtime(agenda_id) -> List[dict]``：返回该 agenda_id 对应 agenda_runtime 行列表
    - ``save_agenda_runtime(runtime_rows) -> None``：覆盖式写入 runtime（事务）
    - ``append_agenda_runtime(runtime_rows) -> None``：追加新行
    - ``delete_agenda_runtime(agenda_id) -> None``：删除该 agenda_id 的全部 runtime 行

    实现：SQLiteStore（Wave 6 后台记账调用）；测试时可注入 InMemoryAgendaStore。
    """

    async def load_agenda_plan(self, agenda_id: str) -> Optional[dict]: ...

    async def dump_agenda_runtime(self, agenda_id: str) -> List[dict]: ...

    async def save_agenda_runtime(self, runtime_rows: List[dict]) -> None: ...

    async def append_agenda_runtime(self, runtime_rows: List[dict]) -> None: ...

    async def delete_agenda_runtime(self, agenda_id: str) -> None: ...


# ---------------------------------------------------------------------------
# AgendaRuntimeRow（运行时进度行 duck-typed 包装）
# ---------------------------------------------------------------------------


class AgendaRuntimeRow:
    """agenda_runtime 表行的轻量包装（duck-typed；可作为 dict 传给 store）。

    Attributes:
        plan_id: 对应 agenda_plan.id（None 表示 ad-hoc 创建）
        label: 环节标题
        order: 顺序顺序
        starts_at_ms: 起始时刻（Unix 毫秒）
        expected_ms: 期望停留时长（毫秒）
        done: 是否已完成（0/1）
        current: 是否当前环节（0/1）
        note: 备注（可选）
        inserted_by: 插入者标识（"human" / "ai" / "planner"）
    """

    def __init__(
        self,
        *,
        plan_id: Optional[int] = None,
        label: str = "",
        order: int = 0,
        starts_at_ms: int = 0,
        expected_ms: int = 0,
        done: bool = False,
        current: bool = False,
        note: Optional[str] = None,
        inserted_by: str = "ai",
    ) -> None:
        self.plan_id = plan_id
        self.label = label
        self.order = order
        self.starts_at_ms = starts_at_ms
        self.expected_ms = expected_ms
        self.done = done
        self.current = current
        self.note = note
        self.inserted_by = inserted_by

    def to_dict(self) -> dict[str, Any]:
        """转换为 dict（适配 SQLiteStore.execute() 写入）。"""
        return {
            "plan_id": self.plan_id,
            "label": self.label,
            "order": self.order,
            "starts_at_ms": self.starts_at_ms,
            "expected_ms": self.expected_ms,
            "done": 1 if self.done else 0,
            "current": 1 if self.current else 0,
            "note": self.note,
            "inserted_by": self.inserted_by,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "AgendaRuntimeRow":
        """从 SQLite 行 dict 构造（兼容 done/current 0/1 与 bool）。"""
        return cls(
            plan_id=row.get("plan_id"),
            label=row.get("label", ""),
            order=row.get("order", 0),
            starts_at_ms=row.get("starts_at_ms", 0),
            expected_ms=row.get("expected_ms", 0),
            done=bool(row.get("done", 0)),
            current=bool(row.get("current", 0)),
            note=row.get("note"),
            inserted_by=row.get("inserted_by", "ai"),
        )


# ---------------------------------------------------------------------------
# 推进历史环形缓冲容量
# ---------------------------------------------------------------------------

#: 推进历史环形缓冲容量（最近 50 条；超出时 deque 自动丢弃最早条目）
#: 用于 Dashboard 节目单调试可观察性：展示"为什么跳到这里 / 上一段停留多久"
TRANSITIONS_MAXLEN: int = 50


# ---------------------------------------------------------------------------
# AgendaState
# ---------------------------------------------------------------------------


class AgendaState:
    """节目单运行时状态机。

    非线程安全；仅在 StreamerAgent 单一 asyncio 事件循环内使用。
    通过方法调用驱动，**不**订阅 EventBus。

    Attributes:
        status: 当前状态枚举值
        current_segment_id: 当前所在环节 id；无则为 ``None``
        segment_started_at_ms: 当前环节的"开始时刻"锚点（Unix 毫秒）
        agenda_started_at_ms: **整场时间轴锚点**——``start()`` 时记录，
            ``unload()`` 时清除
        agenda: 当前加载的节目单引用（``Agenda`` 或 duck-typed）
        expanded_cache: 环节 id → 扩展内容缓存
        completed_segment_ids: 已走完的环节 id 列表
        is_paused: 是否处于暂停子状态（``status==RUNNING`` 时才有意义）
        agenda_id: 当前 agenda 在 agenda_plan 表的主键（持久化标识）
        manually_overridden (property): 是否被手动操作覆盖
    """

    def __init__(
        self,
        *,
        clock: Optional[Callable[[], int]] = None,
        store: Optional[AgendaStore] = None,
    ) -> None:
        """
        Args:
            clock: 可选时钟回调（无参 → int 毫秒）；用于测试中注入确定性时间。
                优先级：方法参数 ``now_ms`` > ``clock()`` > 真实时钟。
            store: 可选 AgendaStore 适配（duck-typed）。
                默认 None 表示不持久化（in-memory 模式）；Wave 6 后台记账器会注入。
        """
        # ----- 状态机字段 -----
        self.status: AgendaStatus = AgendaStatus.INACTIVE
        self.current_segment_id: Optional[str] = None
        self.segment_started_at_ms: int = 0

        # ----- 整场时间轴 -----
        self.agenda_started_at_ms: Optional[int] = None
        self.agenda: Optional[Any] = None
        self.agenda_id: Optional[str] = None

        # ----- 扩展内容缓存 -----
        self.expanded_cache: Dict[str, Any] = {}

        # ----- 已完成环节 -----
        self.completed_segment_ids: List[str] = []

        # ----- 暂停状态 -----
        self.is_paused: bool = False
        self.paused_elapsed_ms: int = 0
        self._paused_at_ms: Optional[int] = None

        # ----- 跳转/扩展标记 -----
        self._needs_expansion: Set[str] = set()
        self._manually_overridden: bool = False

        # ----- 推进历史（环形缓冲）-----
        self._transitions: Deque[Dict[str, Any]] = deque(maxlen=TRANSITIONS_MAXLEN)

        # ----- 时钟注入 -----
        self._clock: Optional[Callable[[], int]] = clock

        # ----- Storage 适配 -----
        self._store: Optional[AgendaStore] = store

    # ------------------------------------------------------------------
    # 时钟注入
    # ------------------------------------------------------------------

    def _resolve_now(self, now_ms: Optional[int]) -> int:
        """解析可选的 ``now_ms``：参数 > 注入回调 > 真实时钟。"""
        if now_ms is not None:
            return now_ms
        if self._clock is not None:
            return self._clock()
        return _real_now_ms()

    # ------------------------------------------------------------------
    # 状态转移
    # ------------------------------------------------------------------

    def start(self, agenda: Any, *, now_ms: Optional[int] = None) -> None:
        """启动新节目单，重置所有运行时状态。"""
        self.agenda = agenda
        self.status = AgendaStatus.RUNNING
        self.agenda_id = getattr(agenda, "agenda_id", None)
        self.agenda_started_at_ms = self._resolve_now(now_ms)
        self.completed_segment_ids = []
        self.expanded_cache = {}
        self._needs_expansion = set()
        self._manually_overridden = False
        self.is_paused = False
        self.paused_elapsed_ms = 0
        self._paused_at_ms = None
        # 默认从第一段开始
        segments = list(getattr(agenda, "segments", []) or [])
        if segments:
            first_id = getattr(segments[0], "id", None)
            if first_id is not None:
                self.current_segment_id = first_id
                self.segment_started_at_ms = self._resolve_now(now_ms)
                # 首次启动时如果首段不在缓存则需扩展
                self._needs_expansion.add(first_id)
        # 记录推进
        self._append_transition(
            event="start",
            segment_id=self.current_segment_id,
            reason=None,
            now_ms=self.agenda_started_at_ms,
        )

    def pause(self, *, now_ms: Optional[int] = None) -> None:
        """暂停当前环节（仅 RUNNING 有效）。"""
        if self.status != AgendaStatus.RUNNING or self.is_paused:
            return
        self.is_paused = True
        self._paused_at_ms = self._resolve_now(now_ms)
        self._append_transition(
            event="pause",
            segment_id=self.current_segment_id,
            reason=None,
            now_ms=self._paused_at_ms,
        )

    def resume(self, *, now_ms: Optional[int] = None) -> None:
        """恢复运行（从暂停中）。"""
        if not self.is_paused:
            return
        now = self._resolve_now(now_ms)
        if self._paused_at_ms is not None:
            self.paused_elapsed_ms += max(0, now - self._paused_at_ms)
        self.is_paused = False
        self._paused_at_ms = None
        self._append_transition(
            event="resume",
            segment_id=self.current_segment_id,
            reason=None,
            now_ms=now,
        )

    def skip(self, *, now_ms: Optional[int] = None) -> Optional[str]:
        """手动跳到下一段。返回新环节 id；已到末尾返回 None。"""
        nxt = self._next_segment_id(self.current_segment_id) if self.current_segment_id else None
        if nxt is None:
            # 已到末尾：标记完成
            if self.current_segment_id is not None:
                self.completed_segment_ids.append(self.current_segment_id)
            self.current_segment_id = None
            self.status = AgendaStatus.COMPLETED
            self._append_transition(
                event="skip_to_end",
                segment_id=None,
                reason="end_of_agenda",
                now_ms=self._resolve_now(now_ms),
            )
            return None
        # 标记当前为完成
        if self.current_segment_id is not None:
            self.completed_segment_ids.append(self.current_segment_id)
        self.current_segment_id = nxt
        self.segment_started_at_ms = self._resolve_now(now_ms)
        # 清除旧段的扩展缓存（新段需重新生成）
        self.expanded_cache.pop(self.current_segment_id, None)
        self._needs_expansion.add(nxt)
        self._manually_overridden = True
        self._append_transition(
            event="skip",
            segment_id=nxt,
            reason=None,
            now_ms=self.segment_started_at_ms,
        )
        return nxt

    def rewind(self, *, now_ms: Optional[int] = None) -> Optional[str]:
        """手动回退到上一段。返回新环节 id；已在首段返回 None。"""
        prev = self._prev_segment_id(self.current_segment_id) if self.current_segment_id else None
        if prev is None:
            return None
        self.current_segment_id = prev
        self.segment_started_at_ms = self._resolve_now(now_ms)
        self.expanded_cache.pop(self.current_segment_id, None)
        self._needs_expansion.add(prev)
        self._manually_overridden = True
        self._append_transition(
            event="rewind",
            segment_id=prev,
            reason=None,
            now_ms=self.segment_started_at_ms,
        )
        return prev

    def jump_to(self, segment_id: str, *, now_ms: Optional[int] = None) -> None:
        """手动跳到指定环节 id。"""
        if self._find_segment(segment_id) is None:
            raise ValueError(f"AgendaState.jump_to: 不存在的环节 id='{segment_id}'")
        self.current_segment_id = segment_id
        self.segment_started_at_ms = self._resolve_now(now_ms)
        self.expanded_cache.pop(self.current_segment_id, None)
        self._needs_expansion.add(segment_id)
        self._manually_overridden = True
        self._append_transition(
            event="jump_to",
            segment_id=segment_id,
            reason=None,
            now_ms=self.segment_started_at_ms,
        )

    def advance_to(self, segment_id: str, *, now_ms: Optional[int] = None) -> None:
        """自动推进到指定环节（调度器调用）。

        与 ``jump_to`` 的区别：**不**设置 ``_manually_overridden``。
        若目标不在 ``expanded_cache`` 中 → 加入 ``_needs_expansion`` 集合。
        """
        if self._find_segment(segment_id) is None:
            raise ValueError(f"AgendaState.advance_to: 不存在的环节 id='{segment_id}'")
        # 标记当前为完成（如有）
        if self.current_segment_id is not None and self.current_segment_id != segment_id:
            self.completed_segment_ids.append(self.current_segment_id)
        self.current_segment_id = segment_id
        self.segment_started_at_ms = self._resolve_now(now_ms)
        if segment_id not in self.expanded_cache:
            self._needs_expansion.add(segment_id)
        # 自动推进后清除 manually_overridden（让下一次自动路径可正常推进）
        self._manually_overridden = False
        self._append_transition(
            event="advance",
            segment_id=segment_id,
            reason="auto",
            now_ms=self.segment_started_at_ms,
        )

    def unload(self, *, now_ms: Optional[int] = None) -> None:
        """卸载节目单，重置状态机到 UNLOADED。"""
        self.status = AgendaStatus.UNLOADED
        self.agenda = None
        self.agenda_id = None
        self.current_segment_id = None
        self.segment_started_at_ms = 0
        self.agenda_started_at_ms = None
        self.completed_segment_ids = []
        self.expanded_cache = {}
        self._needs_expansion = set()
        self._manually_overridden = False
        self.is_paused = False
        self.paused_elapsed_ms = 0
        self._paused_at_ms = None
        self._append_transition(
            event="unload",
            segment_id=None,
            reason=None,
            now_ms=self._resolve_now(now_ms),
        )

    # ------------------------------------------------------------------
    # 推进历史
    # ------------------------------------------------------------------

    def _append_transition(
        self,
        *,
        event: str,
        segment_id: Optional[str],
        reason: Optional[str],
        now_ms: int,
    ) -> None:
        """记录一次状态机转移（环形缓冲；最近 50 条）。"""
        self._transitions.append(
            {
                "event": event,
                "segment_id": segment_id,
                "reason": reason,
                "timestamp_ms": now_ms,
            }
        )

    def get_transitions(self) -> List[Dict[str, Any]]:
        """返回推进历史列表（旧→新）。"""
        return list(self._transitions)

    # ------------------------------------------------------------------
    # 整场时间轴查询（受 pause 影响）
    # ------------------------------------------------------------------

    def get_elapsed_live_ms(self, *, now_ms: Optional[int] = None) -> Optional[int]:
        """返回整场直播经过时长（Unix 毫秒）。

        未启动返回 None；wall clock 推进（不扣除暂停时长——与 OutlineState 一致）。
        """
        if self.agenda_started_at_ms is None:
            return None
        now = self._resolve_now(now_ms)
        return max(0, now - self.agenda_started_at_ms)

    def get_total_planned_ms(self) -> Optional[int]:
        """返回整场节目单的计划总时长（毫秒）。

        委托 ``agenda.get_total_planned_ms()``；``agenda`` 未加载时返回 ``None``。
        """
        if self.agenda is None:
            return None
        return self.agenda.get_total_planned_ms()

    def get_progress_percent(self, *, now_ms: Optional[int] = None) -> Optional[float]:
        """返回整场直播进度百分比（夹取到 ``[0.0, 100.0]``）。"""
        elapsed = self.get_elapsed_live_ms(now_ms=now_ms)
        total = self.get_total_planned_ms()
        if elapsed is None or total is None or total <= 0:
            return None
        pct = elapsed / total * 100.0
        if pct < 0.0:
            return 0.0
        if pct > 100.0:
            return 100.0
        return pct

    # ------------------------------------------------------------------
    # 当前环节时间（受 pause 影响）
    # ------------------------------------------------------------------

    def get_current_segment_elapsed_ms(self, *, now_ms: Optional[int] = None) -> int:
        """当前环节已用时长（**扣除**暂停时长）。"""
        if self.current_segment_id is None or self.status != AgendaStatus.RUNNING:
            return 0
        if self.is_paused and self._paused_at_ms is not None:
            effective_now = self._paused_at_ms
        else:
            effective_now = self._resolve_now(now_ms)
        return max(0, effective_now - self.segment_started_at_ms - self.paused_elapsed_ms)

    def get_current_segment_remaining_ms(self, *, now_ms: Optional[int] = None) -> int:
        """当前环节剩余时长（**扣除**暂停时长，超时返回 0）。"""
        if self.current_segment_id is None or self.agenda is None:
            return 0
        seg = self._find_segment(self.current_segment_id)
        if seg is None:
            return 0
        duration = getattr(seg, "duration_ms", 0) or 0
        elapsed = self.get_current_segment_elapsed_ms(now_ms=now_ms)
        return max(0, duration - elapsed)

    # ------------------------------------------------------------------
    # 扩展内容查询（agenda_loader 调用）
    # ------------------------------------------------------------------

    def needs_expansion(self, segment_id: str) -> bool:
        """指定环节是否需要 AI 扩展。"""
        return segment_id in self._needs_expansion

    def get_expanded(self, segment_id: str) -> Optional[Any]:
        """获取环节的扩展内容（已缓存），无则返回 ``None``。"""
        return self.expanded_cache.get(segment_id)

    def cache_expanded(self, expanded: Any) -> None:
        """缓存环节的扩展内容（由 agenda_loader 调用）。"""
        seg_id = getattr(expanded, "segment_id", None)
        if not seg_id:
            raise ValueError("cache_expanded() 要求 expanded.segment_id 非空")
        self.expanded_cache[seg_id] = expanded
        self._needs_expansion.discard(seg_id)

    # ------------------------------------------------------------------
    # 手动覆盖标记
    # ------------------------------------------------------------------

    @property
    def manually_overridden(self) -> bool:
        """是否被手动操作覆盖（skip/rewind/jump）；调度器据此跳过下一次自动推进。"""
        return self._manually_overridden

    def clear_manually_overridden(self) -> None:
        """清除手动覆盖标记（自动路径推进成功后由调度器调用）。"""
        self._manually_overridden = False

    # ------------------------------------------------------------------
    # Storage 适配（duck-typed store）
    # ------------------------------------------------------------------

    async def persist_runtime(self) -> bool:
        """把当前 ``completed_segment_ids`` / ``current_segment_id`` 写回 store。

        Returns:
            True = 写入成功；False = 无 store 注入或写入异常（不抛）。
        """
        if self._store is None or self.agenda is None:
            return False
        # 构造 runtime_rows（一个 AgendaItem = 一行）
        rows = []
        segments = list(getattr(self.agenda, "segments", []) or [])
        plan_lookup = {getattr(s, "id", None): idx for idx, s in enumerate(segments)}
        for idx, seg in enumerate(segments):
            seg_id = getattr(seg, "id", None)
            if seg_id is None:
                continue
            row = AgendaRuntimeRow(
                plan_id=plan_lookup.get(seg_id),
                label=getattr(seg, "title", ""),
                order=idx,
                starts_at_ms=getattr(seg, "duration_ms", 0) or 0,
                expected_ms=getattr(seg, "duration_ms", 0) or 0,
                done=seg_id in self.completed_segment_ids,
                current=seg_id == self.current_segment_id,
                note=None,
                inserted_by="ai",
            )
            rows.append(row.to_dict())
        try:
            agenda_id = self.agenda_id or ""
            await self._store.delete_agenda_runtime(agenda_id)
            if rows:
                await self._store.append_agenda_runtime(rows)
            return True
        except Exception:
            # 失败隔离：持久化失败不影响 Agent 继续运行
            return False

    async def restore_runtime(self, agenda: Any) -> bool:
        """从 store 加载 runtime 行并恢复状态机（如有持久化记录）。

        Args:
            agenda: 当前加载的 Agenda 实例（用于核对 segment id 合法性）

        Returns:
            True = 恢复成功；False = 无 store 注入或无可用数据。
        """
        if self._store is None or agenda is None:
            return False
        agenda_id = getattr(agenda, "agenda_id", None)
        if agenda_id is None:
            return False
        try:
            rows = await self._store.dump_agenda_runtime(agenda_id)
        except Exception:
            return False
        if not rows:
            return False
        completed = []
        current_id: Optional[str] = None
        for row_dict in rows:
            seg_id = row_dict.get("label") or ""  # AgendaRuntimeRow.label 存了 segment_id 替代
            # 注：上面 label 存的是 title；这里需要找更明确的映射
            # 修订：通过 order 找 segment_id
            order = row_dict.get("order", 0)
            segments = list(getattr(agenda, "segments", []) or [])
            if order < len(segments):
                seg_id = getattr(segments[order], "id", None)
            else:
                seg_id = None
            if seg_id is None:
                continue
            done = bool(row_dict.get("done", 0))
            current = bool(row_dict.get("current", 0))
            if done:
                completed.append(seg_id)
            if current:
                current_id = seg_id
        if current_id is None:
            # 没有 current 行 → 默认第一段未完成
            segments = list(getattr(agenda, "segments", []) or [])
            if segments:
                current_id = getattr(segments[0], "id", None)
        self.completed_segment_ids = completed
        self.current_segment_id = current_id
        return True

    # ------------------------------------------------------------------
    # 快照（Dashboard / Prompt 注入用）
    # ------------------------------------------------------------------

    def get_snapshot(self, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
        """生成 Dashboard 状态展示快照。"""
        seg_total = len(self.agenda.segments) if self.agenda else 0

        current_seg_dict: Optional[Dict[str, Any]] = None
        if self.current_segment_id and self.agenda:
            seg = self._find_segment(self.current_segment_id)
            if seg is not None:
                duration_ms = getattr(seg, "duration_ms", 0) or 0
                remaining_ms = self.get_current_segment_remaining_ms(now_ms=now_ms)
                elapsed_ms = max(0, duration_ms - remaining_ms) if duration_ms > 0 else 0
                current_seg_dict = {
                    "id": seg.id,
                    "title": getattr(seg, "title", ""),
                    "duration_ms": duration_ms,
                    "elapsed_ms": elapsed_ms,
                    "remaining_ms": remaining_ms,
                    "expanded": self.current_segment_id in self.expanded_cache,
                    "needs_expansion": self.needs_expansion(self.current_segment_id),
                }

        next_seg_dict: Optional[Dict[str, Any]] = None
        if self.current_segment_id and self.agenda:
            nxt = self._next_segment_id(self.current_segment_id)
            if nxt is not None:
                seg_obj = self._find_segment(nxt)
                if seg_obj is not None:
                    next_seg_dict = {
                        "id": seg_obj.id,
                        "title": getattr(seg_obj, "title", ""),
                    }

        return {
            "status": self.status.value,
            "current_segment": current_seg_dict,
            "next_segment": next_seg_dict,
            "completed_count": len(self.completed_segment_ids),
            "total_count": seg_total,
            "is_paused": self.is_paused,
            "elapsed_live_ms": self.get_elapsed_live_ms(now_ms=now_ms),
            "total_planned_ms": self.get_total_planned_ms(),
            "progress_percent": self.get_progress_percent(now_ms=now_ms),
            "agenda_id": self.agenda_id,
            "agenda_title": getattr(self.agenda, "title", None) if self.agenda else None,
            "manually_overridden": self.manually_overridden,
        }

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _find_segment(self, segment_id: str) -> Optional[Any]:
        """按 id 查找 AgendaSegment 实例（duck-typed）。"""
        if self.agenda is None:
            return None
        for seg in self.agenda.segments:
            if seg.id == segment_id:
                return seg
        return None

    def _next_segment_id(self, current_id: Optional[str]) -> Optional[str]:
        """返回大纲中 ``current_id`` 的下一环节 id；末尾或找不到返回 ``None``。"""
        if self.agenda is None or current_id is None:
            return None
        ids = [seg.id for seg in self.agenda.segments]
        try:
            idx = ids.index(current_id)
        except ValueError:
            return None
        if idx + 1 >= len(ids):
            return None
        return ids[idx + 1]

    def _prev_segment_id(self, current_id: Optional[str]) -> Optional[str]:
        """返回 ``current_id`` 的上一环节 id；首段或找不到返回 ``None``。"""
        if self.agenda is None or current_id is None:
            return None
        ids = [seg.id for seg in self.agenda.segments]
        try:
            idx = ids.index(current_id)
        except ValueError:
            return None
        if idx <= 0:
            return None
        return ids[idx - 1]
