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
  - queue.get() → LLM 压缩（一次调用：时间线摘要 + 话题总结句）→ 写摘要层
  - 并发 = 1（顺序保证：摘要块必须按时间序——乱序 = 倒叙）
  - 失败可丢弃可重算；摘要用 summary profile（不抢主决策优先级）
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from json_repair import repair_json

from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.time_utils import now_ms as _real_now_ms

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
# 画像增量压缩系统提示词模板键（正文见 prompts/profile_system.md）
_PROFILE_SYSTEM_TEMPLATE = "profile_system"


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

    事实写入面：摘要与事实提取经压缩 worker 落库（``memory`` 注入观众
    事实/画像读写服务时启用）。话题/付费数据的权威表分别是 topics /
    付费明细三表——扁平事实副本机制已废弃，不再从事件流二次写入。
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
        memory: Optional[Any] = None,
        memory_policy: Optional[Dict[str, Any]] = None,
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
            memory: 观众事实/画像读写服务（鸭子类型 ``SimpleMemory``）。
                ``None`` 时事实提取与画像生成功能关闭——BackgroundMaintainer
                整体降级为"只记账"。
            memory_policy: 画像行为策略（核心 ``[memory]`` 段，storage.toml）：
                ``fact_extraction_enabled`` / ``profile_min_interactions`` /
                ``profile_max_length`` / ``facts_per_batch``。``None`` 时走内置默认。
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
        # 写入面——memory / event_bus / chat_repo / topic_repo 由 main.py 装配；None 时各自降级
        self._memory = memory
        self._event_bus = event_bus
        self._topic_repo = topic_repo
        # 提示词面——prompt_manager 由 StreamerAgent 构造透传（必填）
        self._prompt_manager = prompt_manager
        # 摘要/画像系统提示词渲染缓存（摘要零变量恒定；画像含长度变量）
        self._summary_system_prompt: Optional[str] = None
        self._profile_system_prompt: str = ""
        # 画像行为策略（[memory] 段；提取开关/门槛/长度/单批事实条数）
        policy = memory_policy if isinstance(memory_policy, dict) else {}
        self._fact_extraction_enabled: bool = bool(policy.get("fact_extraction_enabled", True))
        self._profile_min_interactions: int = int(policy.get("profile_min_interactions", 3) or 3)
        self._profile_max_length: int = int(policy.get("profile_max_length", 400) or 400)
        self._facts_per_batch: int = int(policy.get("facts_per_batch", 5) or 5)
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
            f"memory={'on' if self._memory is not None else 'off'}, "
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

        # 1. 写 live_sessions（热度/计数快照）
        if self._sessions_repo is not None:
            try:
                await self._write_live_session(ts)
            except Exception as exc:
                self._logger.warning(f"写 live_sessions 失败: {exc}")

        # 2. 摘要门控（按热度间隔；上次摘要后无新弹幕则跳过 LLM 调用）
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
            viewer_count=0,  # TODO: 接入观众统计
            audience_total=0,
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
        """处理压缩任务（summary = 摘要+事实提取；profiles = 画像增量生成）。"""
        task_type = task.get("type")
        if task_type == "summary":
            await self._summarize_topic(task.get("now_ms", _real_now_ms()))
        elif task_type == "profiles":
            await self._generate_profiles()

    async def _summarize_topic(self, now_ms: int) -> None:
        """调 LLM 生成话题摘要 + 顺便提取观众事实（summary profile，一次调用双任务）。

        输入 = live_chat 当前场次的最近 viewer 行（"真实观众弹幕"语义由
        SQL 的 ``sender_role='viewer'`` 过滤承载）+ 时间窗内的 SC（SC 不落
        live_chat，但它是最有价值的事实源——观众主动说的完整话）。无显式
        场次时静默跳过；窗口内无观众弹幕（仅主播自嗨）则清空 topic_summary
        防自嗨循环。

        LLM 输出严格 JSON ``{"summary", "facts"}``；解析失败时整体降级为
        纯文本摘要（旧契约），事实提取失败不影响话题摘要。
        事实归属程序化：message_id 反查批内消息 → (platform, user_id)，
        不靠 LLM 报人名；引用批内不存在的 id 视为幻觉丢弃。
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

        # 消息批（canonical 格式与 Planner 同源）+ 归属映射（id → (platform, user_id)）
        lines: List[str] = []
        evidence_map: Dict[str, tuple[str, str]] = {}
        for row in rows:
            message_id = str(row["message_id"] or "")
            line = canonical_content(
                role="user",
                nickname=row["sender_name"] or "观众",
                text=row["content"],
                message_id=message_id,
            )
            lines.append(line)
            if message_id:
                evidence_map[message_id] = (str(row["platform"] or ""), str(row["sender_id"] or ""))

        # SC 并入提取输入（带归属映射；弹幕批为空时 SC 独立成批）
        window_start_ms = previous_summary_ms or max(now_ms - self._summary_interval_ms, 0)
        try:
            sc_rows = await self._chat_repo.list_super_chats_since(live_session_id=live_pk, since_ms=window_start_ms)
        except Exception as exc:
            self._logger.warning(f"读取时间窗内 SC 失败（事实源缺 SC）: {exc}")
            sc_rows = []
        for row in sc_rows:
            message_id = str(row["message_id"] or "")
            nickname = row["user_name"] or "观众"
            text = f"{nickname} 发送 SC：{row['message']}"
            line = canonical_content(role="user", nickname=nickname, text=text, message_id=message_id)
            lines.append(line)
            if message_id:
                evidence_map[message_id] = (str(row["platform"] or ""), str(row["user_id"] or ""))

        history_text = "\n".join(lines)
        if not history_text.strip():
            return

        prompt = f"以下是最近直播间消息（弹幕与醒目留言），请完成话题总结与事实提取：\n\n{history_text}"
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

        summary, facts = self._parse_summary_and_facts(response.content)
        self._room_state.set_topic_summary(summary, now_ms=now_ms)
        self._last_summary_ms = now_ms
        self._logger.debug(f"话题摘要已更新: {summary[:50]}")
        # 摘要落地 → 存储写入，失败降级不阻断记账
        await self._persist_topic_snapshot(summary, now_ms=now_ms, previous_summary_ms=previous_summary_ms)
        # 提取开关关闭时跳过事实链路（摘要照常）；画像生成随之无新原料自然静默
        if not self._fact_extraction_enabled:
            return
        if facts:
            await self._store_viewer_facts(facts, evidence_map)
        try:
            self._compress_queue.put_nowait({"type": "profiles"})
        except asyncio.QueueFull:
            self._logger.debug("压缩队列已满，跳过本轮画像生成请求")

    def _parse_summary_and_facts(self, raw: str) -> tuple[str, List[Dict[str, str]]]:
        """解析 LLM 输出的 ``{"summary", "facts"}`` JSON；失败时整体降级为纯文本摘要。

        容错链：json.loads → json_repair（项目既有依赖）；两者皆失败时把
        原文当摘要（旧契约形态），facts 返回空列表——话题摘要永不被事实
        提取的解析失败拖垮。facts 条目缺字段 / 非法类型直接丢弃。
        """
        raw = (raw or "").strip()
        # 剥离常见 Markdown 代码围栏（LLM 即使被要求"只输出 JSON"也会偶发包裹）
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = repair_json(raw, return_objects=True)
            except Exception:
                data = None
        if not isinstance(data, dict):
            # 纯文本降级：整体当摘要
            return raw, []

        summary = str(data.get("summary", "") or "").strip() or raw
        raw_facts = data.get("facts")
        facts: List[Dict[str, str]] = []
        if isinstance(raw_facts, list):
            for item in raw_facts[: self._facts_per_batch]:
                if not isinstance(item, dict):
                    continue
                message_id = str(item.get("message_id", "") or "").strip()
                fact_text = str(item.get("fact", "") or "").strip()
                if message_id and fact_text:
                    facts.append({"message_id": message_id, "fact": fact_text})
        return summary, facts

    async def _store_viewer_facts(
        self,
        facts: List[Dict[str, str]],
        evidence_map: Dict[str, tuple[str, str]],
    ) -> None:
        """把提取事实按 message_id 归属落 ``viewer_facts``（程序化归属，失败降级）。

        - 引用批内不存在的 id → 丢弃（防 LLM 幻觉）
        - 批内消息缺 platform / user_id → 丢弃（身份键不完整的原料不收）
        """
        if self._memory is None or not facts:
            return
        stored = 0
        for item in facts:
            message_id = item.get("message_id", "")
            identity = evidence_map.get(message_id)
            if identity is None:
                self._logger.debug(f"事实引用批内不存在的 message_id，丢弃（防幻觉）: {message_id!r}")
                continue
            platform, user_id = identity
            if not platform or not user_id:
                continue
            try:
                accepted = await self._memory.add_viewer_fact(
                    platform=platform,
                    user_id=user_id,
                    fact_text=item.get("fact", ""),
                    source_message_id=message_id,
                )
                stored += 1 if accepted else 0
            except Exception as exc:
                self._logger.warning(f"观众事实写入失败（{platform}/{user_id}）: {exc}")
        if stored:
            self._logger.debug(f"本轮观众事实入库 {stored}/{len(facts)} 条")

    # ------------------------------------------------------------------
    # 画像生成（增量压缩：旧画像 + 水位后新原料 → 新画像）
    # ------------------------------------------------------------------

    async def _generate_profiles(self) -> None:
        """扫描画像候选并逐人增量生成画像（summary profile；失败逐人降级）。

        候选 = ``viewer_facts`` 水位后有新事实且 ``viewers.interaction_count``
        达门槛的观众；原料 = 旧画像 + 水位后新事实 + 付费汇总 + 观众统计。
        单轮最多处理 3 人（防积压雪崩，剩余留给下一轮）。
        """
        if self._memory is None or self._llm_service is None:
            return
        try:
            candidates = await self._memory.list_profile_candidates(min_interactions=self._profile_min_interactions)
        except Exception as exc:
            self._logger.warning(f"画像候选查询失败: {exc}")
            return
        if not candidates:
            return

        for candidate in candidates[:3]:
            try:
                await self._generate_single_profile(candidate)
            except Exception as exc:
                self._logger.warning(f"画像生成失败（{candidate.platform}/{candidate.user_id}，跳过该人）: {exc}")

    async def _generate_single_profile(self, candidate: Any) -> None:
        """为单个候选观众生成增量画像并写回（水位推进）。"""
        assert self._memory is not None  # noqa: S101 调用方已 guard
        platform, user_id = candidate.platform, candidate.user_id
        profile_row = await self._memory.get_viewer_profile_with_watermark(platform=platform, user_id=user_id)
        old_text = profile_row.profile_text if profile_row else ""
        watermark_ms = profile_row.last_compressed_at_ms if profile_row else 0

        facts = await self._memory.list_facts_since(platform=platform, user_id=user_id, since_ms=watermark_ms)
        if not facts:
            return  # 无新原料不空转（候选查询与生成之间可能已被处理）

        material_lines = [f"- {fact.fact_text}" for fact in facts]
        contribution = ""
        if self._chat_repo is not None:
            try:
                summary = await self._chat_repo.summarize_user_contributions(user_id=user_id)
                gold = int(summary.get("gift_total_amount", 0)) + int(summary.get("sc_total_amount", 0))
                if gold > 0:
                    contribution = f"- 付费记录：累计约 {gold / 1000:.0f} 元（含礼物与 SC）"
            except Exception as exc:
                self._logger.debug(f"付费汇总读取失败（画像原料缺该项）: {exc}")

        prompt_parts = [
            f"观众标识：{platform}/{user_id}",
            f"【旧画像】\n{old_text}" if old_text else "【旧画像】（无，首次生成）",
            "【新事实】",
            *material_lines,
        ]
        if contribution:
            prompt_parts.append(contribution)
        prompt = "\n".join(prompt_parts)

        response = await self._llm_service.generate(
            prompt,
            profile=SUMMARY_PROFILE,
            system=self._get_profile_system_prompt(),
        )
        if not (getattr(response, "success", False) and getattr(response, "content", None)):
            self._logger.warning(f"画像生成 LLM 返回失败（{platform}/{user_id}）")
            return

        profile_text = response.content.strip()
        if not profile_text:
            return
        if len(profile_text) > self._profile_max_length:
            profile_text = profile_text[: self._profile_max_length]
        await self._memory.upsert_viewer_profile(
            platform=platform,
            user_id=user_id,
            profile_text=profile_text,
            last_compressed_at_ms=_real_now_ms(),
        )
        self._logger.debug(f"画像已更新（{platform}/{user_id}）: {profile_text[:50]}")

    # ------------------------------------------------------------------
    # 提示词渲染（零变量模板缓存复用）
    # ------------------------------------------------------------------

    def _get_summary_system_prompt(self) -> str:
        """渲染摘要+事实提取系统提示词。

        ``PromptManager`` 由 StreamerAgent 构造透传：调用方负责装配其扫描根
        与模板（启用 ``src/**/prompts/`` 约定扫描时可发现包内 summary_system
        模板）。
        """
        if self._summary_system_prompt is None:
            self._summary_system_prompt = self._prompt_manager.render(_SUMMARY_SYSTEM_TEMPLATE)
        return self._summary_system_prompt

    def _get_profile_system_prompt(self) -> str:
        """渲染画像增量压缩系统提示词（含长度变量，结果缓存复用）。"""
        if not self._profile_system_prompt:
            self._profile_system_prompt = self._prompt_manager.render(
                _PROFILE_SYSTEM_TEMPLATE,
                profile_max_length=self._profile_max_length,
            )
        return self._profile_system_prompt

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
                live_session_id=live_pk, start_ms=window_start, end_ms=now_ms, summary=summary, tags=None
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
