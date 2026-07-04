"""
AmaidesuDecider - 直播专用决策器

借鉴 MaiBot Maisaka 的"双阶段决策"思路，但做成轻量、低延迟、完全自包含的实现：

- Stage 1 节奏门控（TimingGate + MessageBuffer）：聚合突发弹幕，按采样率/强制触发/退避
  决定"要不要参与"，对应 MaiBot 的 Timing Gate。
- Stage 2 内容决策（单次结构化 LLM 调用）：决定"说什么 + 情绪"，合并了 MaiBot 的
  Planner 与 Replyer，去掉多轮内部循环与学习库，适配直播低延迟。

约束：
- 只使用 Amaidesu 自己的 LLMManager / PromptManager / ContextService，不依赖 MaiBot。
- 不订阅 input/output 事件，由 DeciderManager 调用 decide()；只发布 decision.intent.generated。
- 本期输出 speech + emotion（12 枚举），action 字段预留扩展点。
"""

from typing import Any, Dict, List, Literal, Optional

import asyncio
import json
import re

from pydantic import Field

from src.stages.decision.registry import decider
from src.modules.config.schemas.base import BaseConfig
from src.modules.config.service import ConfigService
from src.modules.context import ContextService, MessageRole
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload
from src.modules.llm.manager import LLMManager
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager
from src.modules.types import Intent, IntentAction, IntentEmotion, IntentMetadata
from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.capabilities import CapabilitiesProvider, UnifiedCapabilitiesView
from src.modules.time_utils import now_ms

from .message_buffer import MessageBuffer
from .timing_gate import TimingGate


@decider("amaidesu")
class AmaidesuDecider:
    """直播专用决策器（双阶段轻量版）。"""

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Decider 注册信息"""
        return {"layer": "decision", "name": "amaidesu", "class": cls, "source": "builtin:amaidesu"}

    class ConfigSchema(BaseConfig):
        """直播决策器配置 Schema。

        借鉴 MaiBot 的频率/强制/退避机制，适配直播弹幕场景。
        """

        # Stage 2 LLM
        client: Literal["llm", "llm_fast", "vlm"] = Field(default="llm_fast", description="内容决策使用的 LLM 客户端")
        fallback_mode: Literal["silent", "simple", "echo"] = Field(
            default="silent", description="LLM 失败时的降级模式：silent 不发言 / simple 通用回复 / echo 复述"
        )
        history_limit: int = Field(default=10, ge=0, description="构建 prompt 时引用的历史消息条数")

        # Stage 1 弹幕聚合
        batch_window_ms: int = Field(default=1500, ge=0, description="弹幕聚合时间窗口（毫秒）")
        batch_max_size: int = Field(default=8, ge=1, description="单批最多聚合的弹幕条数")
        tick_interval_ms: int = Field(default=300, ge=50, description="后台聚合检查间隔（毫秒）")

        # Stage 1 节奏门控
        participation_rate: float = Field(
            default=0.3, ge=0.0, le=1.0, description="普通弹幕采样率（类比 talk_value），0 为静默"
        )
        force_data_types: List[str] = Field(
            default_factory=lambda: ["super_chat", "guard"], description="强制响应的数据类型"
        )
        force_importance: float = Field(default=0.8, ge=0.0, le=1.0, description="importance 达到该值则强制响应")
        no_action_backoff_base_ms: int = Field(default=8000, ge=0, description="冷场 no_action 退避基数（毫秒）")
        no_action_backoff_cap_ms: int = Field(default=60000, ge=0, description="冷场 no_action 退避上限（毫秒）")

        # 可选 LLM 节奏门控
        use_llm_timing_gate: bool = Field(default=False, description="是否启用额外的 LLM 节奏门控（非强制批次）")

        # 动作选择
        enable_action_selection: bool = Field(
            default=True, description="是否让 LLM 从 OutputHandler 能力中选择动作（需注入能力提供者）"
        )

        # 人设默认值（无 persona 配置时使用）
        bot_name: str = Field(default="爱德丝", description="VTuber 名称")

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        llm_service: LLMManager,
        prompt_service: PromptManager,
        config_service: Optional[ConfigService] = None,
        context_service: Optional[ContextService] = None,
        capabilities_provider: Optional[CapabilitiesProvider] = None,
    ):
        self.typed_config = self.ConfigSchema.from_dict(config)
        self.logger = get_logger("AmaidesuDecider")

        self._event_bus = event_bus
        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._config_service = config_service
        self._context_service = context_service
        self._capabilities_provider = capabilities_provider

        # 能力快照（首次决策时惰性加载并缓存）
        self._capabilities_loaded = False
        self._action_list_str = ""
        self._valid_action_names: set[str] = set()

        self.client_type = self.typed_config.client
        self.fallback_mode = self.typed_config.fallback_mode

        self._buffer = MessageBuffer()
        self._timing_gate = TimingGate(
            participation_rate=self.typed_config.participation_rate,
            force_data_types=self.typed_config.force_data_types,
            force_importance=self.typed_config.force_importance,
            backoff_base_ms=self.typed_config.no_action_backoff_base_ms,
            backoff_cap_ms=self.typed_config.no_action_backoff_cap_ms,
        )

        self._running = False
        self._flush_task: Optional[asyncio.Task] = None
        self._flush_lock = asyncio.Lock()

        # 统计信息
        self._total_messages = 0
        self._total_batches = 0
        self._total_replies = 0
        self._total_no_action = 0
        self._failed_requests = 0

    async def setup(self) -> None:
        """启动后台聚合循环。"""
        self.logger.info("初始化 AmaidesuDecider...")
        if self._llm_service is None:
            raise RuntimeError("LLM Manager 未注入！请确保在 setup 中正确配置。")

        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        self.logger.info(
            f"AmaidesuDecider 初始化完成 (Client: {self.client_type}, "
            f"采样率: {self.typed_config.participation_rate}, "
            f"聚合窗口: {self.typed_config.batch_window_ms}ms)"
        )

    async def decide(self, normalized_message: "NormalizedMessage") -> None:
        """接收单条消息，入缓冲并更新强制触发状态（快速返回，决策在后台循环完成）。"""
        self._total_messages += 1
        forced = self._timing_gate.is_forced(normalized_message)
        self._buffer.add(normalized_message, arrival_ms=now_ms(), forced=forced)

    async def cleanup(self) -> None:
        """停止后台循环并打印统计。"""
        self.logger.info("清理 AmaidesuDecider...")
        self._running = False
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        self.logger.info(
            f"统计: 消息={self._total_messages}, 批次={self._total_batches}, "
            f"发言={self._total_replies}, no_action={self._total_no_action}, "
            f"LLM失败={self._failed_requests}"
        )
        self.logger.info("AmaidesuDecider 已清理")

    # ==================== Stage 1: 聚合 + 节奏门控 ====================

    async def _flush_loop(self) -> None:
        """后台循环：周期性检查缓冲并触发批次决策。"""
        interval = self.typed_config.tick_interval_ms / 1000.0
        try:
            while self._running:
                await asyncio.sleep(interval)
                try:
                    await self._maybe_flush()
                except Exception as e:
                    self.logger.error(f"批次决策异常: {e}", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _maybe_flush(self) -> None:
        """判断是否应该取出一批并决策。"""
        if self._buffer.is_empty:
            return

        # 上一批仍在决策时跳过本 tick，避免发言交叠堆积
        if self._flush_lock.locked():
            return

        async with self._flush_lock:
            if self._buffer.is_empty:
                return

            now = now_ms()
            flush_due = (
                self._buffer.force
                or self._buffer.size >= self.typed_config.batch_max_size
                or (now - self._buffer.first_arrival_ms) >= self.typed_config.batch_window_ms
            )
            if not flush_due:
                return

            forced = self._buffer.force
            batch = self._buffer.drain()
            if not batch:
                return

            self._total_batches += 1

            act, reason = self._timing_gate.should_act(forced=forced)
            if not act:
                self.logger.info(f"节奏门控跳过本批 ({len(batch)} 条), 原因: {reason}")
                return

            if self.typed_config.use_llm_timing_gate and not forced:
                gate_act = await self._run_llm_timing_gate(batch)
                if not gate_act:
                    self.logger.debug(f"LLM 节奏门控决定不参与本批 ({len(batch)} 条)")
                    self._timing_gate.record_result(replied=False)
                    self._total_no_action += 1
                    return

            await self._make_decision(batch, trigger_reason=reason)

    # ==================== Stage 2: 内容决策 ====================

    async def _make_decision(self, batch: List["NormalizedMessage"], *, trigger_reason: str) -> None:
        """对一批弹幕做单次结构化 LLM 决策并发布 Intent。"""
        session_id = batch[-1].source
        danmaku_batch = MessageBuffer.render_batch_text(batch)

        history_text = await self._get_history_text(session_id)
        persona = self._get_persona_config()
        room_context = self._build_room_context(batch)
        self._ensure_capabilities()

        prompt = self._prompt_service.render_safe(
            "decision/amaidesu_planner",
            danmaku_batch=danmaku_batch,
            bot_name=persona.get("bot_name", self.typed_config.bot_name),
            personality=persona.get("personality", "活泼开朗，有些调皮，喜欢和直播间观众互动"),
            style_constraints=persona.get("style_constraints", "口语化、简短，像在直播间和观众聊天，避免机械式回复"),
            history=history_text,
            room_context=room_context,
            action_list=self._action_list_str or "（当前无可用动作，action 请留空字符串）",
        )

        try:
            self.logger.info(f"AmaidesuDecider 决策中 ({len(batch)} 条, 触发: {trigger_reason})")
            response = await self._llm_service.chat(prompt=prompt, client_type=self.client_type)
        except Exception as e:
            self._failed_requests += 1
            self.logger.error(f"LLM 调用异常: {e}", exc_info=True)
            await self._handle_fallback(batch, session_id)
            return

        if not response.success:
            self._failed_requests += 1
            self.logger.error(f"LLM 调用失败: {response.error}")
            await self._handle_fallback(batch, session_id)
            return

        cleaned_json = self._clean_llm_json(response.content)
        try:
            parsed_data = json.loads(cleaned_json)
        except json.JSONDecodeError as e:
            self._failed_requests += 1
            self.logger.error(f"JSON 解析失败: {e}, 清理后的内容: {cleaned_json[:200]}")
            await self._handle_fallback(batch, session_id)
            return

        should_reply = bool(parsed_data.get("should_reply", True))
        speech = (parsed_data.get("text", "") or parsed_data.get("speech", "")).strip()

        if not should_reply or not speech:
            self.logger.info("LLM 决定本批不发言 (should_reply=false 或空文本)")
            self._timing_gate.record_result(replied=False)
            self._total_no_action += 1
            return

        intent = self._create_intent(parsed_data, speech)
        intent.metadata.source_message_id = batch[-1].message_id
        await self._publish_intent(intent)
        await self._save_context(session_id, danmaku_batch, speech)

        self._timing_gate.record_result(replied=True)
        self._total_replies += 1
        self.logger.info(f"AmaidesuDecider 发言: {speech}")

    async def _run_llm_timing_gate(self, batch: List["NormalizedMessage"]) -> bool:
        """可选的 LLM 节奏门控：判断当前是否适合插话。返回 True 表示参与。"""
        danmaku_batch = MessageBuffer.render_batch_text(batch)
        persona = self._get_persona_config()
        prompt = self._prompt_service.render_safe(
            "decision/amaidesu_timing_gate",
            danmaku_batch=danmaku_batch,
            bot_name=persona.get("bot_name", self.typed_config.bot_name),
        )
        try:
            response = await self._llm_service.chat(prompt=prompt, client_type=self.client_type)
        except Exception as e:
            self.logger.warning(f"LLM 节奏门控调用异常，默认参与: {e}")
            return True

        if not response.success:
            self.logger.warning(f"LLM 节奏门控调用失败，默认参与: {response.error}")
            return True

        try:
            parsed = json.loads(self._clean_llm_json(response.content))
        except json.JSONDecodeError:
            self.logger.warning("LLM 节奏门控返回非 JSON，默认参与")
            return True

        return str(parsed.get("action", "act")).strip().lower() == "act"

    # ==================== 辅助方法 ====================

    async def _get_history_text(self, session_id: str) -> str:
        """从 ContextService 取历史并渲染为文本（可选服务，缺失时返回空）。"""
        if not self._context_service or self.typed_config.history_limit <= 0:
            return ""
        try:
            history = await self._context_service.get_history(session_id, limit=self.typed_config.history_limit)
            lines = []
            for msg in history:
                role_name = "用户" if msg.role == MessageRole.USER else "助手"
                lines.append(f"{role_name}: {msg.content}")
            return "\n".join(lines)
        except Exception as e:
            self.logger.warning(f"获取历史上下文失败: {e}")
            return ""

    async def _save_context(self, session_id: str, danmaku_batch: str, speech: str) -> None:
        """将本批弹幕与回复写回上下文（可选服务）。"""
        if not self._context_service:
            return
        try:
            await self._context_service.add_message(
                session_id=session_id,
                role=MessageRole.USER,
                content=danmaku_batch,
            )
            await self._context_service.add_message(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content=speech,
            )
        except Exception as e:
            self.logger.warning(f"保存上下文失败: {e}")

    def _get_persona_config(self) -> Dict[str, Any]:
        """读取 VTuber 人设配置，无法获取时返回空字典。"""
        if self._config_service is None:
            return {}
        try:
            return self._config_service.get_section("persona", {})
        except Exception as e:
            self.logger.warning(f"读取 persona 配置失败: {e}")
            return {}

    def _build_room_context(self, batch: List["NormalizedMessage"]) -> str:
        """根据本批消息构建简短的直播间上下文描述。"""
        last = batch[-1]
        parts = []
        if last.platform:
            parts.append(f"平台: {last.platform}")
        if last.room_id:
            parts.append(f"直播间: {last.room_id}")
        parts.append(f"本批弹幕数: {len(batch)}")
        return "，".join(parts)

    def _clean_llm_json(self, raw_output: str) -> str:
        """清理 LLM 返回的 JSON 字符串（与 LLMDecider 一致的三步清理）。"""
        cleaned = raw_output.strip()
        cleaned = re.sub(r"^```json\s*", "", cleaned)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]

        cleaned = re.sub(r",\s*}", "}", cleaned)
        cleaned = re.sub(r",\s*]", "]", cleaned)
        return cleaned

    def _create_intent(self, parsed_data: Dict[str, Any], speech: str) -> Intent:
        """从解析后的 JSON 构造 Intent（speech + emotion + 经能力校验的 action）。"""
        emotion_raw = str(parsed_data.get("emotion", "neutral")).lower()
        try:
            emotion_obj: Optional[IntentEmotion] = IntentEmotion(name=emotion_raw, intensity=0.5)
        except Exception:
            self.logger.warning(f"LLM 情绪 '{emotion_raw}' 不在枚举中，降级为 neutral")
            emotion_obj = IntentEmotion(name="neutral", intensity=0.5)

        action_obj = self._build_action(parsed_data)

        return Intent(
            emotion=emotion_obj,
            action=action_obj,
            speech=speech,
            metadata=IntentMetadata(source_id="amaidesu", decision_time_ms=now_ms()),
        )

    def _build_action(self, parsed_data: Dict[str, Any]) -> Optional[IntentAction]:
        """从 LLM 输出构造并校验 IntentAction。

        - 期望 `action` 为全限定名 `<handler>.<local_action>`（来自能力清单）。
        - 启用动作选择且已加载能力时，对动作名做白名单校验，非法则丢弃。
        - `action_parameters` 必须为 dict，否则忽略参数。
        """
        action_raw = str(parsed_data.get("action", "")).strip()
        if not action_raw:
            return None

        if self.typed_config.enable_action_selection and self._valid_action_names:
            if action_raw not in self._valid_action_names:
                self.logger.warning(f"LLM 选择的动作 '{action_raw}' 不在可用能力清单中，丢弃")
                return None

        raw_params = parsed_data.get("action_parameters") or parsed_data.get("parameters") or {}
        parameters = raw_params if isinstance(raw_params, dict) else {}
        if raw_params and not isinstance(raw_params, dict):
            self.logger.warning(f"action_parameters 非对象（{type(raw_params).__name__}），忽略参数")

        try:
            return IntentAction(name=action_raw, parameters=parameters)
        except Exception as e:
            self.logger.warning(f"构造 IntentAction 失败（name={action_raw!r}）：{e}")
            return None

    def _ensure_capabilities(self) -> None:
        """惰性加载并缓存 Output 能力快照（首次决策时调用一次）。"""
        if self._capabilities_loaded:
            return
        self._capabilities_loaded = True

        if not self.typed_config.enable_action_selection or self._capabilities_provider is None:
            return

        try:
            view = self._capabilities_provider.get_all_capabilities()
        except Exception as e:
            self.logger.warning(f"查询 Output 能力失败，动作选择降级为禁用：{e}")
            return

        self._valid_action_names = {entry.name for entry in view.actions}
        self._action_list_str = self._format_action_list(view)
        self.logger.info(f"已加载 {len(self._valid_action_names)} 个可用动作供决策选择")

    @staticmethod
    def _format_action_list(view: UnifiedCapabilitiesView) -> str:
        """把能力视图渲染为供 prompt 使用的动作清单文本。"""
        lines: List[str] = []
        for entry in view.actions:
            param_parts: List[str] = []
            for pname, spec in entry.parameters.items():
                seg = f"{pname}:{spec.type}"
                if spec.minimum is not None or spec.maximum is not None:
                    seg += f"[{spec.minimum}~{spec.maximum}]"
                if spec.default is not None:
                    seg += f"=默认{spec.default}"
                param_parts.append(seg)
            params_str = f"（参数: {', '.join(param_parts)}）" if param_parts else ""
            desc = entry.description or ""
            lines.append(f"- {entry.name}: {desc}{params_str}")
        return "\n".join(lines)

    async def _publish_intent(self, intent: Intent) -> None:
        """通过 event_bus 发布 decision.intent.generated 事件。"""
        if not self._event_bus:
            self.logger.error("EventBus 未初始化，无法发布事件")
            return
        await self._event_bus.emit(
            CoreEvents.DECISION_INTENT_GENERATED,
            IntentPayload.from_intent(intent, "amaidesu"),
            source="AmaidesuDecider",
        )

    async def _handle_fallback(self, batch: List["NormalizedMessage"], session_id: str) -> None:
        """LLM 失败时的降级处理。"""
        if self.fallback_mode == "silent":
            self._timing_gate.record_result(replied=False)
            self._total_no_action += 1
            return

        if self.fallback_mode == "echo":
            speech = f"刚才有观众说：{batch[-1].text}"
        else:  # simple
            speech = "哎呀刚刚没太听清，再说一遍嘛~"

        intent = Intent(
            emotion=IntentEmotion(name="neutral", intensity=0.5),
            action=None,
            speech=speech,
            metadata=IntentMetadata(
                source_id="amaidesu",
                decision_time_ms=now_ms(),
                source_message_id=batch[-1].message_id,
            ),
        )
        await self._publish_intent(intent)
        await self._save_context(session_id, MessageBuffer.render_batch_text(batch), speech)
        self._timing_gate.record_result(replied=True)
        self._total_replies += 1

    def get_statistics(self) -> Dict[str, Any]:
        """获取运行时统计信息。"""
        return {
            "total_messages": self._total_messages,
            "total_batches": self._total_batches,
            "total_replies": self._total_replies,
            "total_no_action": self._total_no_action,
            "failed_requests": self._failed_requests,
            "no_action_streak": self._timing_gate.no_action_count,
            "client_type": self.client_type,
        }

    def get_info(self) -> Dict[str, Any]:
        """获取 Decider 配置信息。"""
        return {
            "name": "AmaidesuDecider",
            "version": "1.0.0",
            "client_type": self.client_type,
            "template_name": "decision/amaidesu_planner",
            "participation_rate": self.typed_config.participation_rate,
            "fallback_mode": self.fallback_mode,
        }
