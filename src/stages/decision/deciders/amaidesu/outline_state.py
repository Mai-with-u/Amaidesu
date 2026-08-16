"""OutlineState - 直播大纲运行时状态机 + 手动控制 + 整场时间轴。

职责边界（重要）
----------------
本模块是 Task 6 产出，**只**回答"当前直播大纲跑到哪了、整场进度几分之几、
能不能暂停/跳转/回退"——返回状态变更/快照 dict，**不**实现调度循环
（Task 9 ``outline_scheduler.py``）、**不**调用 LLM（Task 5 ``outline_loader.py``）、
**不**持久化、**不**订阅 EventBus。

设计要点（沿用 ``RoomState`` / ``ProactiveTrigger`` 范式）
---------------------------------------------------------
- **可注入时钟**：所有方法接受可选 ``now_ms`` 参数（无参调用退回真实时钟），
  测试中传入确定性时间戳即可重现任意暂停/跳转场景，**禁止**依赖 ``time.sleep``。
- **纯内存状态**：所有字段均为实例属性，无文件/数据库持久化（重启即重置）。
- **状态机**::

      INACTIVE ──start()──▶ RUNNING ──skip()到末段──▶ COMPLETED ──unload()──▶ UNLOADED
                              │  ◀──rewind()──┘
                              ├─pause()──▶ PAUSED（status 仍为 RUNNING）──resume()──▶ RUNNING
                              │
                              └─unload()──▶ UNLOADED

  - ``LOADING`` 为 T5 加载过程保留位；本模块不主动设置（由调用方在 ``start()`` 前置位）。
  - ``PAUSED`` 是 ``RUNNING`` 的子状态（``is_paused=True`` 标志位），status 值仍为 ``RUNNING``。

- **整场时间轴锚点** ``outline_started_at_ms`` 在 ``start()`` 时记录，``unload()`` 时清除。
  ``get_elapsed_live_ms()`` = ``now - outline_started_at_ms``，**不**扣除暂停时长
  （暂停期间整场时间按 wall clock 推进，进度百分比被夹取到 100% 兜底；如需
  "实际直播时间"应基于 segment 累计自行计算——YAGNI：当前无消费者需要）。

- **暂停语义**：``pause()`` 记录 ``_paused_at_ms`` 冻结 segment 时间，``resume()`` 时把
  暂停时长累加到 ``paused_elapsed_ms``。**segment 维度的"剩余时长"** 在暂停期间
  保持不变（不依赖 wall clock 推进），由 ``get_current_segment_remaining_ms()`` 暴露。

- **跳过语义**：``skip()`` 时清除当前环节 ``expanded_cache`` 条目（新段需重新生成），
  并把当前环节 id 加入 ``completed_segment_ids``。

- **跳转语义**：``jump_to(seg)`` 跳到任意环节（不限方向），**不**把当前环节标记为完成；
  若目标不在 ``expanded_cache`` 中 → 加入 ``_needs_expansion`` 集合（Task 9 据此触发
  即时扩展，扩展完成后 ``cache_expanded()`` 会自动从集合移除）。

- **手动覆盖**：``skip()`` / ``rewind()`` / ``jump_to()`` 隐式设置 ``_manually_overridden=True``，
  调度器（Task 9）据此跳过下一次自动推进；``advance_to()``（自动路径）**不**设置该标志。

使用方契约
----------
- 调度器（Task 9）：每个 tick 调用 ``advance_to(seg, now_ms=...)`` 推进 + ``get_snapshot()`` 渲染。
- Dashboard：调 ``get_snapshot()`` 展示整场进度条 + 环节卡片；调 ``skip()`` / ``pause()`` /
  ``resume()`` / ``rewind()`` / ``jump_to()`` 实现手动控制按钮。
- Planner/Replyer：通过 ``expanded_cache[seg_id]`` 取扩展内容；通过
  ``get_elapsed_live_ms()`` / ``get_total_planned_ms()`` / ``get_progress_percent()``
  拼装整场进度信息注入 prompt（Task 7 模板）。

不在此处
--------
- 不实现调度循环（Task 9）
- 不调用 LLM 生成扩展（Task 5）
- 不持久化状态
- 不订阅 EventBus
- 不实现 Dashboard HTTP API（Task 10）
"""

from __future__ import annotations

from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
)

from src.modules.time_utils import now_ms as _real_now_ms


if TYPE_CHECKING:
    # ``ExpandedSegment`` 由 T5 ``outline_loader.py`` 定义（Pydantic 模型）。
    # 本模块仅在类型注解阶段引用它（``from __future__ import annotations`` 已开启，
    # 所有注解都是字符串），运行时**不**触发 ``outline_loader`` 的导入，规避
    # ``src.modules.logging`` → ``src.modules.dashboard`` → ``deciders.llm`` 的
    # dev 分支 pre-existing 循环导入。
    from .outline_loader import ExpandedSegment  # noqa: F401


__all__ = ["OutlineStatus", "OutlineState"]


# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------


class OutlineStatus(Enum):
    """大纲运行时状态枚举。

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
    """加载中（保留位；T5 加载期间由调用方置位，本模块不主动设置）"""

    RUNNING = "running"
    """运行中（含 PAUSED 子状态——``is_paused=True`` 标志位）"""

    COMPLETED = "completed"
    """已完成（最后一段走完；``current_segment_id=None``）"""

    UNLOADED = "unloaded"
    """已卸载（``outline_started_at_ms=None``，等下一次 ``start()`` 视为新一轮）"""


# ---------------------------------------------------------------------------
# OutlineState
# ---------------------------------------------------------------------------


class OutlineState:
    """直播大纲运行时状态机。

    非线程安全；仅在 AmaidesuDecider 单一 asyncio 事件循环内使用。
    纯内存状态（重启即重置），无 EventBus 订阅、无文件持久化——由调度器 /
    Dashboard / 测试代码直接调用方法驱动。

    Attributes:
        status: 当前状态枚举值（INACTIVE / LOADING / RUNNING / COMPLETED / UNLOADED）
        current_segment_id: 当前所在环节 id；无则为 ``None``
        segment_started_at_ms: 当前环节的"开始时刻"锚点（Unix 毫秒）；
            受暂停影响（``pause()`` 冻结、``resume()`` 通过 ``paused_elapsed_ms`` 补偿）
        outline_started_at_ms: **整场时间轴锚点**——``start()`` 时记录，
            ``unload()`` 时清除（重置为 ``None``），重载视为新一轮不累计旧时长
        outline: 当前加载的大纲引用（``StreamOutline`` 或 duck-typed）；
            ``get_total_planned_ms()`` 委托其计算
        expanded_cache: 环节 id → 扩展内容缓存（``Dict[str, ExpandedSegment]``，
            ``ExpandedSegment`` 由 ``outline_loader.py`` 定义；本模块通过 duck typing 访问）
        completed_segment_ids: 已走完的环节 id 列表（按 ``skip()`` / ``advance_to()`` 顺序追加）
        is_paused: 是否处于暂停子状态（``status==RUNNING`` 时才有意义）
        manually_overridden (property): 是否被手动操作（skip/rewind/jump）覆盖；
            Task 9 调度器据此跳过下一次自动推进
    """

    def __init__(self, *, clock: Optional[Callable[[], int]] = None) -> None:
        """
        Args:
            clock: 可选时钟回调（无参 → int 毫秒）；用于测试中注入确定性时间。
                优先级：方法参数 ``now_ms`` > ``clock()`` > 真实时钟。
                默认 ``None`` 退到 ``src.modules.time_utils.now_ms``。
        """
        # ----- 状态机字段 -----
        self.status: OutlineStatus = OutlineStatus.INACTIVE
        self.current_segment_id: Optional[str] = None
        # segment_started_at_ms 在 start() / skip() / rewind() / jump_to() / advance_to() 时设置；
        # 初始为 0，INACTIVE 状态无意义但避免 Optional（与 spec 保持一致：int 非 Optional）
        self.segment_started_at_ms: int = 0

        # ----- 整场时间轴 -----
        self.outline_started_at_ms: Optional[int] = None
        # outline 引用（StreamOutline 或 duck-typed），委托 get_total_planned_ms() 计算
        self.outline: Optional[Any] = None

        # ----- 扩展内容缓存（Dict[str, ExpandedSegment]） -----
        # ExpandedSegment 由 outline_loader.py 定义；本模块不主动导入
        self.expanded_cache: Dict[str, Any] = {}

        # ----- 已完成环节 -----
        self.completed_segment_ids: List[str] = []

        # ----- 暂停状态 -----
        self.is_paused: bool = False
        # 累计暂停时长（毫秒，跨多次 pause/resume 周期）；用于 segment 维度时间补偿
        self.paused_elapsed_ms: int = 0
        # 当前暂停开始时刻（is_paused=True 时有效）；resume 时累加 (now - _paused_at_ms) 到 paused_elapsed_ms
        self._paused_at_ms: Optional[int] = None

        # ----- 跳转/扩展标记 -----
        # jump_to 时若目标不在 expanded_cache，则加入此集合；Task 9 据此触发即时扩展
        self._needs_expansion: Set[str] = set()
        # 手动操作标记（skip/rewind/jump 后置 True）；Task 9 据此跳过自动推进
        self._manually_overridden: bool = False

        # ----- 时钟注入 -----
        self._clock: Optional[Callable[[], int]] = clock

    # ------------------------------------------------------------------
    # 时钟注入
    # ------------------------------------------------------------------

    def _resolve_now(self, now_ms: Optional[int]) -> int:
        """解析可选的 ``now_ms``：参数 > 注入回调 > 真实时钟。

        Args:
            now_ms: 调用方注入的 Unix 毫秒时间戳；``None`` 时回退到注入回调 / 真实时钟

        Returns:
            解析后的 Unix 毫秒时间戳
        """
        if now_ms is not None:
            return now_ms
        if self._clock is not None:
            return self._clock()
        return _real_now_ms()

    # ------------------------------------------------------------------
    # 状态转移：start / pause / resume / skip / rewind / jump_to / advance_to / unload
    # ------------------------------------------------------------------

    def start(self, outline: Any, *, now_ms: Optional[int] = None) -> None:
        """启动新大纲，重置所有运行时状态。

        语义：
            1. 校验 ``outline.segments`` 非空，否则抛 ``ValueError``
            2. 设置 ``self.outline = outline``（持有引用，供 ``get_total_planned_ms()``）
            3. 记录 ``outline_started_at_ms = now``（**整场时间轴锚点**）
            4. 推进到首段：``current_segment_id = outline.segments[0].id``、
               ``segment_started_at_ms = now``
            5. 清空 ``expanded_cache`` / ``completed_segment_ids`` / ``_needs_expansion``
            6. 重置暂停状态：``is_paused=False``、``paused_elapsed_ms=0``、``_paused_at_ms=None``
            7. 重置 ``_manually_overridden=False``
            8. ``status = RUNNING``

        Args:
            outline: :class:`StreamOutline` 实例（duck-typed，仅访问
                ``segments[i].id`` 和 ``get_total_planned_ms()``）。
            now_ms: 启动时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟。

        Raises:
            ValueError: ``outline.segments`` 为空

        Note:
            重启视为新一轮，**不**累计旧时长（旧的 ``outline_started_at_ms`` 已被
            上一次 ``unload()`` 清除；如果直接再次 ``start()`` 而不 ``unload()``，
            锚点也会被本次 ``start()`` 覆盖，符合"新一轮"语义）。
        """
        now = self._resolve_now(now_ms)
        segs = getattr(outline, "segments", None) or []
        if not segs:
            raise ValueError("OutlineState.start() 拒绝空大纲（outline.segments 为空）")

        self.outline = outline
        self.outline_started_at_ms = now
        self.expanded_cache = {}
        self.completed_segment_ids = []
        self._needs_expansion = set()
        self.paused_elapsed_ms = 0
        self._paused_at_ms = None
        self.is_paused = False
        self._manually_overridden = False
        # 推进到首段
        self.current_segment_id = segs[0].id
        self.segment_started_at_ms = now
        self.status = OutlineStatus.RUNNING

    def pause(self, *, now_ms: Optional[int] = None) -> None:
        """冻结当前环节时间推进。

        语义：
            - 仅当 ``status == RUNNING`` 且 ``is_paused == False`` 时生效；其余情况幂等 no-op
            - 记录 ``_paused_at_ms = now``；设置 ``is_paused = True``
            - 调用后 ``get_current_segment_elapsed_ms()`` / ``get_current_segment_remaining_ms()``
              冻结在暂停时刻

        Args:
            now_ms: 暂停时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟
        """
        if self.status != OutlineStatus.RUNNING or self.is_paused:
            return
        self._paused_at_ms = self._resolve_now(now_ms)
        self.is_paused = True

    def resume(self, *, now_ms: Optional[int] = None) -> None:
        """恢复时间推进，把暂停时长累加到 ``paused_elapsed_ms``。

        语义：
            - 仅当 ``is_paused == True`` 时生效；其余情况幂等 no-op
            - ``paused_elapsed_ms += max(0, now - _paused_at_ms)``
            - 清空 ``_paused_at_ms``；设置 ``is_paused = False``

        Args:
            now_ms: 恢复时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟
        """
        if not self.is_paused or self._paused_at_ms is None:
            return
        now = self._resolve_now(now_ms)
        delta = now - self._paused_at_ms
        if delta > 0:
            self.paused_elapsed_ms += delta
        self._paused_at_ms = None
        self.is_paused = False

    def skip(self, *, now_ms: Optional[int] = None) -> None:
        """当前环节标记完成，推进到下一段。

        语义：
            - 仅当 ``status == RUNNING`` 且 ``current_segment_id`` 非空时生效；否则 no-op
            - 清除当前环节 ``expanded_cache`` 条目（skip 后的新段需重新生成扩展）
            - 从 ``_needs_expansion`` 移除当前环节（不再需要扩展）
            - 当前环节 id 加入 ``completed_segment_ids``（若不在）
            - 若存在下一段：
                * ``current_segment_id = next``、``segment_started_at_ms = now``
                * ``paused_elapsed_ms = 0``、``_paused_at_ms = None``、``is_paused = False``
            - 若已是最后一段：
                * ``current_segment_id = None``、``status = COMPLETED``
            - 设置 ``_manually_overridden = True``

        Args:
            now_ms: 推进时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟
        """
        if self.status != OutlineStatus.RUNNING or self.current_segment_id is None:
            return
        current = self.current_segment_id
        now = self._resolve_now(now_ms)
        # 清除当前环节的扩展缓存（新段需重新生成）
        self.expanded_cache.pop(current, None)
        self._needs_expansion.discard(current)
        # 当前环节标记完成
        if current not in self.completed_segment_ids:
            self.completed_segment_ids.append(current)
        # 推进到下一段或标记 COMPLETED
        next_id = self._next_segment_id(current)
        if next_id is None:
            self.current_segment_id = None
            self.status = OutlineStatus.COMPLETED
        else:
            self.current_segment_id = next_id
            self.segment_started_at_ms = now
            self.paused_elapsed_ms = 0
            self._paused_at_ms = None
            self.is_paused = False
        self._manually_overridden = True

    def rewind(self, *, now_ms: Optional[int] = None) -> None:
        """回退到上一未完成环节（撤销最近一次 skip / advance_to）。

        语义：
            - 仅当 ``completed_segment_ids`` 非空时生效；否则 no-op（已在首段或尚未推进）
            - 弹出 ``completed_segment_ids`` 末尾元素作为新 current
            - 重置 ``segment_started_at_ms = now``、暂停状态归零
            - 若 ``status`` 之前是 ``COMPLETED`` → 回到 ``RUNNING``（撤销完成态）
            - 设置 ``_manually_overridden = True``

        Note:
            "上一未完成环节" = 刚刚被 ``skip()`` / ``advance_to()`` 标记完成的那个环节。
            这是单步撤销，不支持多步回退（多步回退可由调用方多次 ``rewind()`` 串联）。

        Args:
            now_ms: 回退时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟
        """
        if not self.completed_segment_ids:
            return
        prev = self.completed_segment_ids.pop()
        now = self._resolve_now(now_ms)
        self.current_segment_id = prev
        self.segment_started_at_ms = now
        self.paused_elapsed_ms = 0
        self._paused_at_ms = None
        self.is_paused = False
        # 若之前是 COMPLETED（末段被 skip 后又 rewind），回到 RUNNING
        if self.status != OutlineStatus.RUNNING:
            self.status = OutlineStatus.RUNNING
        self._manually_overridden = True

    def jump_to(self, segment_id: str, *, now_ms: Optional[int] = None) -> None:
        """跳转到指定环节（**不**标记当前环节为完成）。

        语义：
            - 校验目标 ``segment_id`` 存在于 ``outline.segments``；否则抛 ``ValueError``
            - 若目标不在 ``expanded_cache`` → 加入 ``_needs_expansion``（Task 9 触发即时扩展）
            - 若目标在 ``expanded_cache`` → 从 ``_needs_expansion`` 移除（已扩展）
            - ``current_segment_id = segment_id``、``segment_started_at_ms = now``
            - 重置暂停状态；``status = RUNNING``
            - 设置 ``_manually_overridden = True``

        Args:
            segment_id: 目标环节 id（必须存在于当前 ``outline.segments``）
            now_ms: 跳转时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟

        Raises:
            ValueError: 当前未 ``start()``（``outline`` 为 ``None``）或目标环节不存在
        """
        if self.outline is None:
            raise ValueError("OutlineState.jump_to() 调用前未 start()（outline 为 None）")
        all_ids = {seg.id for seg in self.outline.segments}
        if segment_id not in all_ids:
            raise ValueError(f"jump_to 目标 segment_id={segment_id!r} 不在大纲中（可用: {sorted(all_ids)}）")
        now = self._resolve_now(now_ms)
        # 扩展标记：未在缓存 → 需要扩展；已在缓存 → 清掉标记
        if segment_id in self.expanded_cache:
            self._needs_expansion.discard(segment_id)
        else:
            self._needs_expansion.add(segment_id)
        # 跳转
        self.current_segment_id = segment_id
        self.segment_started_at_ms = now
        self.paused_elapsed_ms = 0
        self._paused_at_ms = None
        self.is_paused = False
        self.status = OutlineStatus.RUNNING
        self._manually_overridden = True

    def advance_to(self, segment_id: str, *, now_ms: Optional[int] = None) -> None:
        """自动推进到指定环节（**由调度器 Task 9 调用，区别于手动 skip**）。

        与 :meth:`skip` 的关键区别：
            - **不**设置 ``_manually_overridden``（这是自动路径，调度器自己在下一步会清除）
            - 其余语义（清缓存、加 completed、重置暂停）与 ``skip()`` 一致

        额外校验：
            - ``segment_id`` 存在于 ``outline.segments``，否则抛 ``ValueError``
            - ``segment_id`` 与 ``current_segment_id`` 相同 → no-op（防止调度器死循环）
            - 仅当 ``status == RUNNING`` 且 ``current_segment_id`` 非空时生效；否则 no-op

        Args:
            segment_id: 目标环节 id（通常 = 下一环节或分支跳转目标）
            now_ms: 推进时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟

        Raises:
            ValueError: 当前未 ``start()``（``outline`` 为 ``None``）或目标环节不存在
        """
        if self.status != OutlineStatus.RUNNING or self.current_segment_id is None:
            return
        if segment_id == self.current_segment_id:
            return
        if self.outline is None:
            raise ValueError("OutlineState.advance_to() 调用前未 start()（outline 为 None）")
        all_ids = {seg.id for seg in self.outline.segments}
        if segment_id not in all_ids:
            raise ValueError(f"advance_to 目标 segment_id={segment_id!r} 不在大纲中（可用: {sorted(all_ids)}）")
        current = self.current_segment_id
        now = self._resolve_now(now_ms)
        # 标记完成 + 清除缓存（与 skip 一致）
        self.expanded_cache.pop(current, None)
        self._needs_expansion.discard(current)
        if current not in self.completed_segment_ids:
            self.completed_segment_ids.append(current)
        # 推进
        self.current_segment_id = segment_id
        self.segment_started_at_ms = now
        self.paused_elapsed_ms = 0
        self._paused_at_ms = None
        self.is_paused = False
        # 不设置 _manually_overridden（自动路径，调度器下一步会 clear_manually_overridden()）

    def mark_manually_overridden(self) -> None:
        """显式标记"被手动覆盖"。

        通常不需要显式调用（``skip()`` / ``rewind()`` / ``jump_to()`` 已隐式标记）；
        保留为公开 API 以应对其他手动控制路径（如外部 Dashboard API 直接修改状态后
        通过此方法通知状态机）。

        效果：``manually_overridden`` property 返回 ``True``，Task 9 调度器据此
        跳过下一次自动推进。
        """
        self._manually_overridden = True

    def clear_manually_overridden(self) -> None:
        """清除"被手动覆盖"标记（**由 Task 9 调度器在确认一次自动推进完成后调用**）。

        正常用户代码不应调用——手动操作路径（``skip/rewind/jump``）会自动设置标志，
        调度器在自动推进一次后调用此方法重置，避免无限跳过自动推进。
        """
        self._manually_overridden = False

    @property
    def manually_overridden(self) -> bool:
        """当前是否被手动操作（``skip/rewind/jump``）覆盖。

        Task 9 调度器据此决定是否跳过下一次自动推进：
        被手动覆盖时尊重用户操作，下次 tick 不再 auto-advance；调度器完成一次自动
        推进后调 :meth:`clear_manually_overridden` 重置。
        """
        return self._manually_overridden

    def unload(self) -> None:
        """卸载大纲，重置所有运行时状态。

        语义：
            - ``outline = None``
            - ``outline_started_at_ms = None``（**整场时间轴锚点清除**）
            - 清空 ``expanded_cache`` / ``completed_segment_ids`` / ``_needs_expansion``
            - ``current_segment_id = None``、``segment_started_at_ms = 0``
            - 暂停状态归零：``is_paused=False``、``paused_elapsed_ms=0``、``_paused_at_ms=None``
            - ``_manually_overridden = False``
            - ``status = UNLOADED``

        效果：
            - ``get_elapsed_live_ms()`` → ``None``
            - ``get_total_planned_ms()`` → ``None``
            - ``get_progress_percent()`` → ``None``

        下一次 ``start(new_outline)`` 视为新一轮，**不**累计旧时长。
        """
        self.outline = None
        self.outline_started_at_ms = None
        self.expanded_cache = {}
        self.completed_segment_ids = []
        self._needs_expansion = set()
        self.current_segment_id = None
        self.segment_started_at_ms = 0
        self.paused_elapsed_ms = 0
        self._paused_at_ms = None
        self.is_paused = False
        self._manually_overridden = False
        self.status = OutlineStatus.UNLOADED

    # ------------------------------------------------------------------
    # 整场时间轴：anchor-based 计算
    # ------------------------------------------------------------------

    def get_elapsed_live_ms(self, *, now_ms: Optional[int] = None) -> Optional[int]:
        """返回大纲启动至今的整场经过时长（Unix 毫秒）。

        计算：``max(0, now - outline_started_at_ms)``。未启动（``outline_started_at_ms is None``）
        返回 ``None``。

        注意：本计算**不**扣除暂停时长——暂停期间整场时间按 wall clock 推进，
        进度百分比会被夹取到 100% 兜底。这是设计选择：整场时间轴的语义是
        "直播间从开播到现在过了多久"，包含暂停；如需"实际直播进行时间"应基于
        segment 累计自行计算（YAGNI：当前无消费者需要）。

        Args:
            now_ms: 当前时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟

        Returns:
            经过毫秒数；未启动返回 ``None``
        """
        if self.outline_started_at_ms is None:
            return None
        now = self._resolve_now(now_ms)
        return max(0, now - self.outline_started_at_ms)

    def get_total_planned_ms(self) -> Optional[int]:
        """返回整场大纲的计划总时长（毫秒）。

        委托 ``outline.get_total_planned_ms()``；``outline`` 未加载时返回 ``None``。
        不对 total 做合法性校验（``StreamOutline.get_total_planned_ms`` 内部会
        累加 ``duration_ms``，理论上 ≥ 1000 * 段数，永不为 0）。
        """
        if self.outline is None:
            return None
        return self.outline.get_total_planned_ms()

    def get_progress_percent(self, *, now_ms: Optional[int] = None) -> Optional[float]:
        """返回整场直播进度百分比（夹取到 ``[0.0, 100.0]``）。

        计算：``elapsed_live_ms / total_planned_ms * 100``，夹取到 ``[0.0, 100.0]``。
        任一前置缺失返回 ``None``：
            - ``elapsed_live_ms is None``（未启动）
            - ``total_planned_ms is None``（未加载）
            - ``total_planned_ms <= 0``（大纲异常，防御性兜底）

        Args:
            now_ms: 当前时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟

        Returns:
            进度百分比（``float``，范围 ``[0.0, 100.0]``）；前置缺失返回 ``None``
        """
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
        """当前环节已用时长（**扣除**暂停时长）。

        计算：
            - ``RUNNING`` 状态 + 暂停中 → ``max(0, paused_at_ms - segment_started_at_ms - paused_elapsed_ms)``（冻结）
            - ``RUNNING`` 状态 + 未暂停 → ``max(0, now - segment_started_at_ms - paused_elapsed_ms)``
            - 非 ``RUNNING`` 或无 current → 0

        Args:
            now_ms: 当前时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟

        Returns:
            当前环节已用毫秒数；无当前环节返回 0
        """
        if self.current_segment_id is None or self.status != OutlineStatus.RUNNING:
            return 0
        # 暂停中：使用 _paused_at_ms 代替 now（冻结时间）
        if self.is_paused and self._paused_at_ms is not None:
            effective_now = self._paused_at_ms
        else:
            effective_now = self._resolve_now(now_ms)
        return max(0, effective_now - self.segment_started_at_ms - self.paused_elapsed_ms)

    def get_current_segment_remaining_ms(self, *, now_ms: Optional[int] = None) -> int:
        """当前环节剩余时长（**扣除**暂停时长，超时返回 0）。

        计算：``max(0, duration_ms - elapsed)``，其中 elapsed 来自
        :meth:`get_current_segment_elapsed_ms`（暂停期间冻结）。

        Args:
            now_ms: 当前时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟

        Returns:
            剩余毫秒数（≥ 0）；无 current / 无 duration 字段返回 0
        """
        if self.current_segment_id is None or self.outline is None:
            return 0
        seg = self._find_segment(self.current_segment_id)
        if seg is None:
            return 0
        duration = getattr(seg, "duration_ms", 0) or 0
        elapsed = self.get_current_segment_elapsed_ms(now_ms=now_ms)
        return max(0, duration - elapsed)

    # ------------------------------------------------------------------
    # 扩展内容查询（Task 5 扩展器 / Task 9 调度器调用）
    # ------------------------------------------------------------------

    def needs_expansion(self, segment_id: str) -> bool:
        """指定环节是否需要 AI 扩展（Task 9 据此触发即时扩展）。

        Returns:
            ``True`` 表示该环节未在 ``expanded_cache`` 中（已被 ``jump_to`` 标记需扩展）。
        """
        return segment_id in self._needs_expansion

    def get_expanded(self, segment_id: str) -> Optional[Any]:
        """获取环节的扩展内容（已缓存），无则返回 ``None``。

        返回的对象是 :class:`outline_loader.ExpandedSegment`（或 duck-typed 同构对象），
        含字段 ``segment_id`` / ``opening_line`` / ``topic_guidance`` / ``talking_points``。
        """
        return self.expanded_cache.get(segment_id)

    def cache_expanded(self, expanded: Any) -> None:
        """缓存环节的扩展内容（由 Task 5 扩展器 / Task 9 调度器调用）。

        缓存后自动从 ``_needs_expansion`` 移除（已扩展则不再需要扩展）。
        若同一 ``segment_id`` 已存在缓存则覆盖（幂等）。

        Args:
            expanded: :class:`ExpandedSegment` 实例（duck-typed：需有 ``segment_id`` 字段）
        """
        seg_id = getattr(expanded, "segment_id", None)
        if not seg_id:
            raise ValueError("cache_expanded() 要求 expanded.segment_id 非空")
        self.expanded_cache[seg_id] = expanded
        self._needs_expansion.discard(seg_id)

    # ------------------------------------------------------------------
    # 快照（Dashboard / Prompt 注入用）
    # ------------------------------------------------------------------

    def get_snapshot(self, *, now_ms: Optional[int] = None) -> Dict[str, Any]:
        """生成 Dashboard 状态展示快照。

        字段：
            - ``status``: 状态枚举值（str）
            - ``current_segment``: 当前环节 dict
              （``id`` / ``title`` / ``duration_ms`` / ``elapsed_ms`` /
              ``remaining_ms`` / ``expanded`` / ``needs_expansion``）；
              无 current 则为 ``None``
            - ``next_segment``: 下一环节预览 dict（``id`` / ``title``）；无则 ``None``
            - ``completed_count`` / ``total_count``: 已完成数 / 总环节数
            - ``is_paused``: bool
            - ``elapsed_live_ms``: 整场经过时长（毫秒，``int`` / ``None``）
            - ``total_planned_ms``: 整场计划时长（毫秒，``int`` / ``None``）
            - ``progress_percent``: 整场进度百分比（``float`` / ``None``）
            - ``outline_id`` / ``outline_title``: 大纲元数据；未加载为 ``None``
            - ``manually_overridden``: bool

        Args:
            now_ms: 当前时刻（Unix 毫秒）；``None`` 时使用注入 / 真实时钟

        Returns:
            dict（Dashboard JSON 序列化友好；嵌套值基本为基本类型或 ``None``）
        """
        seg_total = len(self.outline.segments) if self.outline else 0

        # current_segment
        current_seg_dict: Optional[Dict[str, Any]] = None
        if self.current_segment_id and self.outline:
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

        # next_segment preview
        next_seg_dict: Optional[Dict[str, Any]] = None
        if self.current_segment_id and self.outline:
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
            "outline_id": getattr(self.outline, "outline_id", None) if self.outline else None,
            "outline_title": getattr(self.outline, "title", None) if self.outline else None,
            "manually_overridden": self.manually_overridden,
        }

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _find_segment(self, segment_id: str) -> Optional[Any]:
        """按 id 查找 OutlineSegment 实例（duck-typed）。"""
        if self.outline is None:
            return None
        for seg in self.outline.segments:
            if seg.id == segment_id:
                return seg
        return None

    def _next_segment_id(self, current_id: str) -> Optional[str]:
        """返回大纲中 ``current_id`` 的下一环节 id；末尾或找不到返回 ``None``。"""
        if self.outline is None:
            return None
        ids = [seg.id for seg in self.outline.segments]
        try:
            idx = ids.index(current_id)
        except ValueError:
            return None
        if idx + 1 >= len(ids):
            return None
        return ids[idx + 1]
