"""BackgroundMaintainer - 主播 Agent 后台维护（双任务模型）

双任务模型（不是 1 个、不是 N 个）：
- 轻循环（周期 tick ~5s，纯机械，永不被阻塞）
- 压缩 worker（触发驱动，等 asyncio.Queue）
- Why 2 个不回 1 个：慢任务（LLM 秒级）不冻结快任务节拍
- Why 不 N 个：快任务合并无成本、管理复杂度封顶
- 生命周期：BackgroundMaintainer（非 Agent 无 LLM）统一 start/stop/cleanup
- 后台 Loop 的角色是"记账者+提醒者"——不注入上下文，
  只写状态（live_sessions）+发提醒；话题摘要从 live_chat 读观众弹幕
  （单一事实源，与写路径同源）

职责：
- **轻循环**（periodic tick ~5s）：
  - 直播间状态记账（热度/统计 → 写 live_sessions 表）
  - 话题增量聚合（关键词计数 O(1)，内存态）
  - 空转检测信号（由 ProactiveTrigger 承载；流程单超时提醒见 rundown_overdue 触发源）
  - 窗口滑动检查（事件量/时间阈值 → put 压缩队列）
- **压缩 worker**（asyncio.Queue 触发）：
  - queue.get() → LLM 一句话话题摘要 → 写 RoomState 与摘要层（timeline_summary / topics）
  - 并发 = 1（顺序保证：摘要块必须按时间序——乱序 = 倒叙）
  - 失败可丢弃可重算；摘要用 summary profile（不抢主决策优先级）

主播自己的记忆（场次回顾 / 观众画像）不在本循环里：那是收播时的事，见 ``memory/`` 子包。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.utils.time_utils import now_ms as _real_now_ms

from .canonical import canonical_content
from .room_state import RoomState

__all__ = ["BackgroundMaintainer"]


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

_DEFAULT_LIGHT_TICK_MS = 5_000
_DEFAULT_COLD_TIMEOUT_MS = 60_000
_DEFAULT_SUMMARY_INTERVAL_MS = 60_000
# 摘要 LLM 绑定：由代码显式声明（配置不承载绑定；封闭 profile 六成员之一，
# 与 Planner / Replyer 隔离，对应 model.toml [llm_profiles.summary]）
SUMMARY_PROFILE = "summary"
# 压缩队列上限（与 StreamerCompressorConfig.queue_max 默认对齐）
_DEFAULT_COMPRESSOR_QUEUE_MAX = 100
# 压缩 worker 并发（与 StreamerCompressorConfig.concurrency 默认对齐）
_DEFAULT_COMPRESSOR_CONCURRENCY = 1

# 摘要系统提示词模板键（正文见 src/agents/streamer/prompts/summary_system.md，
# 由包内 prompts/ 目录内聚承载）
_SUMMARY_SYSTEM_TEMPLATE = "summary_system"
# 话题摘要句长度上限（字符）：一句话总结，超出按截断处理
_SUMMARY_MAX_CHARS = 80


def _cfg(config: Any, key: str, default: Any) -> Any:
    """从配置对象读取字段值（dict 用 .get，其他对象用 getattr 兜底）。"""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class BackgroundMaintainer:
    """主播 Agent 后台维护器（双任务模型：轻循环 + 压缩 worker）。

    非 Agent（无 LLM 主决策权），纯机械循环 + 后台压缩任务。
    通过构造器注入依赖（room_state / storage store / llm_service）。

    写入面只有话题摘要（timeline_summary / topics）与场次心跳（live_sessions）。
    话题/付费数据的权威表分别是 topics / 付费明细三表，不从事件流二次写入；
    主播自己的记忆（场次回顾 / 观众画像）在收播时由 ``memory/`` 子包生成。
    """

    def __init__(
        self,
        config: Any,
        *,
        room_state: RoomState,
        llm_service: Optional[Any] = None,
        sessions_repo: Optional[Any] = None,
        chat_repo: Optional[Any] = None,
        topic_repo: Optional[Any] = None,
        session_manager: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
        prompt_manager: PromptManager,
    ) -> None:
        """初始化。

        Args:
            config: 配置字典或对象；读取
                - ``enabled``（默认 True）
                - ``light_tick_ms``（默认 5000）
                - ``cold_timeout_ms``（默认 60000）
                - ``summary_interval_ms``（默认 60000）
                - ``compressor_concurrency``（默认 1）
                - ``compressor_queue_max``（默认 100）
            room_state: ``RoomState`` 实例（轻循环读取快照）
            llm_service: LLM 管理器（可选；压缩 worker 调用）
            sessions_repo: ``SessionRepo``（轻循环写场次实时状态）
            session_manager: 场次管理器（``LiveSessionManager`` 或鸭子类型；
                提供 ``async resolve_pk() -> Optional[int]``）。心跳、话题快照
                与话题摘要的场次归属经它解析（与 live_chat 写路径同源）；
                ``None`` 时相关路径整体降级跳过。
            event_bus: 可选 ``EventBus``（观察面预留；本类当前不订阅事件——
                付费/话题数据的权威表是明细三表与 topics，不做二次副本）。
            chat_repo: 可选 ``ChatRepo``；提供时话题摘要读取 live_chat
                最近观众行（``sender_role="viewer"``）。
            topic_repo: 可选 ``TopicRepo``；提供时每次摘要成功后写
                ``timeline_summary``（摘要历史）与 ``topics``（当前话题快照投影）。
            prompt_manager: ``PromptManager`` 实例（必填；摘要系统提示词经其渲染）。
                由 StreamerAgent 构造透传，调用方负责装配其扫描根与模板。
        """
        self._config = config
        self._room_state = room_state
        self._llm_service = llm_service
        self._sessions_repo = sessions_repo
        self._chat_repo = chat_repo
        self._session_manager = session_manager
        # 写入面——event_bus / chat_repo / topic_repo 由组合根装配；None 时各自降级
        self._event_bus = event_bus
        self._topic_repo = topic_repo
        # 提示词面——prompt_manager 由 StreamerAgent 构造透传（必填）
        self._prompt_manager = prompt_manager
        # 摘要系统提示词渲染缓存（零变量恒定）
        self._summary_system_prompt: Optional[str] = None
        self._logger = get_logger("BackgroundMaintainer")

        self._enabled: bool = bool(_cfg(config, "enabled", True))
        self._light_tick_ms: int = _cfg(config, "light_tick_ms", _DEFAULT_LIGHT_TICK_MS)
        self._cold_timeout_ms: int = _cfg(config, "cold_timeout_ms", _DEFAULT_COLD_TIMEOUT_MS)
        self._summary_interval_ms: int = _cfg(config, "summary_interval_ms", _DEFAULT_SUMMARY_INTERVAL_MS)

        self._light_task: Optional[asyncio.Task] = None
        self._compress_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_summary_ms: int = 0
        self._compress_queue: asyncio.Queue = asyncio.Queue(
            maxsize=_cfg(config, "compressor_queue_max", _DEFAULT_COMPRESSOR_QUEUE_MAX)
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动轻循环 + 压缩 worker（创建 asyncio.Task）。

        ``config.enabled=False`` 时整体短路：轻循环不跑、压缩 worker 不创建——
        所有后台维护功能降级为"关闭"。
        """
        if self._running:
            return
        if not self._enabled:
            self._logger.info("BackgroundMaintainer 配置 enabled=false，跳过启动")
            return
        self._running = True
        self._light_task = asyncio.create_task(self._light_loop())
        self._compress_task = asyncio.create_task(self._compress_loop())
        self._logger.info(
            f"BackgroundMaintainer 已启动 "
            f"(light_tick={self._light_tick_ms}ms, "
            f"summary_interval={self._summary_interval_ms}ms, "
            f"event_bus={'on' if self._event_bus is not None else 'off'})"
        )

    async def stop(self) -> None:
        """停止双任务（取消 asyncio.Task + 排空队列）。"""
        self._running = False
        for task in (self._light_task, self._compress_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._light_task = None
        self._compress_task = None
        self._logger.info("BackgroundMaintainer 已停止")

    # ------------------------------------------------------------------
    # 轻循环（周期 tick ~5s）
    # ------------------------------------------------------------------

    async def _light_loop(self) -> None:
        """轻循环主入口（纯机械，永不被阻塞）。"""
        interval = max(self._light_tick_ms / 1000.0, 0.1)
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self._light_tick()
                except Exception as exc:
                    self._logger.exception(f"BackgroundMaintainer 轻循环 tick 异常: {exc}")
        except asyncio.CancelledError:
            raise

    async def _light_tick(self, *, now_ms: Optional[int] = None) -> None:
        """单次轻 tick：记账 + 摘要门控 + 窗口滑动检查。"""
        ts = now_ms if now_ms is not None else _real_now_ms()

        # 写 live_sessions（热度/计数快照）
        if self._sessions_repo is not None:
            try:
                await self._write_live_session(ts)
            except Exception as exc:
                self._logger.warning(f"写 live_sessions 失败: {exc}")

        # 摘要门控（按热度间隔；上次摘要后无新弹幕则跳过 LLM 调用）
        try:
            await self._maybe_summarize(ts)
        except Exception as exc:
            self._logger.warning(f"摘要门控失败: {exc}")

    async def _write_live_session(self, now_ms: int) -> None:
        """把当前 RoomState 快照写入 live_sessions 表（后台记账，每轻 tick 一次心跳）。

        场次归属经 ``LiveSessionManager.resolve_pk()`` 解析（显式场次进行中
        取其主键，否则默认场次）；管理器缺失时降级跳过——心跳不建行，
        场次行的创建/结账归 LiveSessionManager。
        """
        if self._sessions_repo is None or self._session_manager is None:
            return
        snapshot = self._room_state.get_snapshot(now_ms=now_ms)
        # 热度数字映射：low=1, medium=2, high=3
        heat_map = {"low": 1, "medium": 2, "high": 3}
        heat_int = heat_map.get(snapshot.heat, 1)

        try:
            live_pk = await self._session_manager.resolve_pk()
        except Exception as exc:  # noqa: BLE001 记账降级，不阻断轻循环
            self._logger.warning(f"场次归属解析失败，跳过本次心跳: {exc}")
            return
        await self._sessions_repo.update_live_session_stats(
            live_session_id=live_pk,
            heat=heat_int,
            # open-live 协议无"当前在线人数"推送，此列暂无可靠数据源
            viewer_count=0,
            audience_total=snapshot.audience_total,
            updated_at_ms=now_ms,
        )

    async def _maybe_summarize(self, now_ms: int) -> None:
        """摘要门控：按热度频率投递摘要任务。"""
        if self._llm_service is None or self._chat_repo is None:
            return
        snap = self._room_state.get_snapshot(now_ms=now_ms)
        interval = self._interval_for_heat(snap.heat)
        if now_ms - self._last_summary_ms < interval:
            return

        last_msg_ms = self._room_state.last_message_ms
        if last_msg_ms is not None and last_msg_ms <= self._last_summary_ms:
            return

        # 把压缩任务放入压缩队列（而非同步调用 → 不阻塞轻循环）
        try:
            self._compress_queue.put_nowait({"type": "summary", "now_ms": now_ms})
        except asyncio.QueueFull:
            self._logger.warning("压缩队列已满，丢弃本次摘要请求")

    def _interval_for_heat(self, heat: str) -> int:
        """根据热度调整摘要频率（高热 → 半间隔）。"""
        base = self._summary_interval_ms
        if heat == "high":
            return max(base // 2, 5_000)
        return base

    # ------------------------------------------------------------------
    # 压缩 worker（asyncio.Queue 触发）
    # ------------------------------------------------------------------

    async def _compress_loop(self) -> None:
        """压缩 worker 主入口（并发=1，顺序保证）。"""
        try:
            while self._running:
                try:
                    task = await asyncio.wait_for(
                        self._compress_queue.get(),
                        timeout=self._light_tick_ms / 1000.0,
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise

                try:
                    await self._handle_compress_task(task)
                except Exception as exc:
                    self._logger.exception(f"压缩 worker 处理失败: {exc}")
        except asyncio.CancelledError:
            raise

    async def _handle_compress_task(self, task: Dict[str, Any]) -> None:
        """处理压缩任务（当前只有 summary = 一句话话题摘要）。"""
        task_type = task.get("type")
        if task_type == "summary":
            await self._summarize_topic(task.get("now_ms", _real_now_ms()))

    async def _summarize_topic(self, now_ms: int) -> None:
        """调 LLM 生成一句话话题摘要（summary profile）。

        输入 = live_chat 当前场次的最近 viewer 行（"真实观众弹幕"语义由
        SQL 的 ``sender_role='viewer'`` 过滤承载）+ 时间窗内的 SC（SC 不落
        live_chat，但观众花钱说的话最能代表他们在聊什么）。无显式场次时静默
        跳过；窗口内无观众弹幕（仅主播自嗨）则清空 topic_summary 防自嗨循环。

        LLM 只输出摘要句本身；旧契约的 JSON 形态（``{"summary": …}``）与
        代码围栏在解析时容错剥掉。
        """
        if self._llm_service is None or self._chat_repo is None or self._session_manager is None:
            return
        previous_summary_ms = self._last_summary_ms
        try:
            live_pk = await self._session_manager.resolve_pk()
            if live_pk is None:
                return
            rows = await self._chat_repo.list_recent_live_chat(
                live_session_id=live_pk,
                limit=20,
                sender_role="viewer",
            )
        except Exception as exc:
            self._logger.warning(f"读取 live_chat 观众弹幕失败: {exc}")
            return

        if not rows:
            # 清空 topic_summary（防自嗨循环）
            self._room_state.set_topic_summary("", now_ms=now_ms)
            self._last_summary_ms = now_ms
            return

        # 消息批（canonical 格式与 Planner 同源）
        lines: List[str] = []
        for row in rows:
            lines.append(
                canonical_content(
                    role="user",
                    nickname=row["sender_name"] or "观众",
                    text=row["content"],
                    message_id=str(row["message_id"] or ""),
                )
            )

        # 时间窗内的 SC 并入摘要输入
        window_start_ms = previous_summary_ms or max(now_ms - self._summary_interval_ms, 0)
        try:
            sc_rows = await self._chat_repo.list_super_chats_since(live_session_id=live_pk, since_ms=window_start_ms)
        except Exception as exc:
            self._logger.warning(f"读取时间窗内 SC 失败（摘要输入缺 SC）: {exc}")
            sc_rows = []
        for row in sc_rows:
            nickname = row["user_name"] or "观众"
            text = f"{nickname} 发送 SC：{row['message']}"
            lines.append(
                canonical_content(role="user", nickname=nickname, text=text, message_id=str(row["message_id"] or ""))
            )

        history_text = "\n".join(lines)
        if not history_text.strip():
            return

        prompt = f"以下是最近直播间消息（弹幕与醒目留言），请用一句话总结观众在聊什么：\n\n{history_text}"
        try:
            response = await self._llm_service.generate(
                prompt,
                profile=SUMMARY_PROFILE,
                system=self._get_summary_system_prompt(),
            )
        except Exception as exc:
            self._logger.warning(f"话题摘要 LLM 调用异常: {exc}")
            return

        if not (getattr(response, "success", False) and getattr(response, "content", None)):
            self._logger.warning("话题摘要 LLM 返回失败")
            return

        summary = self._parse_summary(response.content)
        self._room_state.set_topic_summary(summary, now_ms=now_ms)
        self._last_summary_ms = now_ms
        self._logger.debug(f"话题摘要已更新: {summary[:50]}")
        # 摘要落地 → 存储写入，失败降级不阻断记账
        await self._persist_topic_snapshot(summary, now_ms=now_ms, previous_summary_ms=previous_summary_ms)

    def _parse_summary(self, raw: str) -> str:
        """把 LLM 输出收拾成一句摘要：剥代码围栏与引号、容忍旧 JSON 契约、只取首行、截长。"""
        text = (raw or "").strip()
        # 剥离常见 Markdown 代码围栏（LLM 即使被要求"只输出一句话"也会偶发包裹）
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        # 旧契约兼容：{"summary": "..."} 形态只取 summary 值
        match = re.search(r'"summary"\s*:\s*"([^"]*)"', text)
        if match:
            text = match.group(1)
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        first_line = first_line.strip("\"'“”「」")
        return first_line[:_SUMMARY_MAX_CHARS]

    # ------------------------------------------------------------------
    # 提示词渲染（零变量模板缓存复用）
    # ------------------------------------------------------------------

    def _get_summary_system_prompt(self) -> str:
        """渲染话题摘要系统提示词。

        ``PromptManager`` 由 StreamerAgent 构造透传：调用方负责装配其扫描根
        与模板（启用 ``src/**/prompts/`` 约定扫描时可发现包内 summary_system
        模板）。
        """
        if self._summary_system_prompt is None:
            self._summary_system_prompt = self._prompt_manager.render(_SUMMARY_SYSTEM_TEMPLATE)
        return self._summary_system_prompt

    async def _persist_topic_snapshot(self, summary: str, *, now_ms: int, previous_summary_ms: int) -> None:
        """摘要成功后把话题状态写入 ``timeline_summary`` 与 ``topics`` 表。

        - ``timeline_summary``：一行一段摘要历史，窗口为 [上次摘要时刻, 本次]
        - ``topics``：当前话题快照投影——先清本场旧行再插最新关键词 + 摘要句，
          消费者读到的永远是当前话题状态（历史轨迹由 timeline_summary 承担）
        - 场次归属经 ``LiveSessionManager.resolve_pk()`` 解析；管理器缺失时降级跳过

        异常降级：落库失败仅 warning，不阻断后台记账循环。
        """
        if self._topic_repo is None or self._session_manager is None:
            return
        try:
            live_pk = await self._session_manager.resolve_pk()
            window_start = previous_summary_ms or max(now_ms - self._summary_interval_ms, 0)
            await self._topic_repo.insert_timeline_summary(
                live_session_id=live_pk, start_ms=window_start, end_ms=now_ms, summary=summary
            )
            snapshot = self._room_state.get_snapshot(now_ms=now_ms)
            await self._topic_repo.delete_session_topics(live_session_id=live_pk)
            for rank, keyword in enumerate(snapshot.topics):
                await self._topic_repo.insert_topic(
                    live_session_id=live_pk,
                    label=keyword,
                    source="word_freq",
                    score=1.0 / (rank + 1),
                    trend=0.0,
                    duration_ms=self._summary_interval_ms,
                )
            if summary:
                await self._topic_repo.insert_topic(
                    live_session_id=live_pk,
                    label=summary,
                    source="summary",
                    score=1.0,
                    trend=0.0,
                    duration_ms=self._summary_interval_ms,
                    count=1,
                )
        except Exception as exc:  # noqa: BLE001 边界处吸收 + 日志，不阻断记账循环
            self._logger.warning(f"话题快照落库失败（timeline_summary/topics）: {exc}")
