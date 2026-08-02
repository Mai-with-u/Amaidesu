"""Planner - 双阶段决策器的 Stage 1（战术决策者）。

职责：
- 判断当前弹幕批次是否值得主播介入（should_reply）
- 产出 DecisionPlan：目标对象 / 话题摘要 / 回复指引 / 置信度
- **不写台词**（那是 Replyer 的职责）

核心契约：
1. **人设隔离**：Planner 的 prompt **零人设变量**（不传 $personality / $style_constraints /
   $bot_name）。人设由 Stage 2 的 Replyer 注入。这是两阶段拆分的 1:1 承诺。
2. **快速模型**：使用 ``planner_client``（默认 ``llm_fast``），与 Replyer 的
   ``replyer_client``（默认 ``llm``）分离，避免共享客户端实例。
3. **无工具调用**：``chat()`` 调用不传 ``tools`` 参数——Planner 只做结构化 JSON 输出。
4. **降级安全**：LLM 异常 / 脏 JSON 均返回 ``None``，由调用方（Decider 编排层）处理降级。

数据流：
    batch + room_state.snapshot + forced ──▶ render_safe('decision/amaidesu_planner_v2')
        ──▶ llm_service.chat(prompt, client_type=planner_client)
        ──▶ _clean_llm_json + json.loads
        ──▶ DecisionPlan（或 None）
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from src.modules.config.schemas.base import BaseConfig
from src.modules.logging import get_logger

from .plan import DecisionPlan
from .room_state import RoomState, RoomStateSnapshot

__all__ = ["Planner"]


# ---------------------------------------------------------------------------
# 配置 Schema（最小子集）
# ---------------------------------------------------------------------------


class _PlannerConfig(BaseConfig):
    """Planner 配置 Schema。

    仅声明 Planner 直接使用的字段；当从完整 AmaidesuDecider 配置字典加载时，
    BaseConfig.from_dict() 的漂移检测会自动剥离其余字段。
    """

    # 两阶段-Planner 使用的 LLM 客户端（快速模型，默认 llm_fast）
    # 与 Replyer 的 replyer_client（默认 llm）分离，避免共享客户端实例。
    planner_client: str = "llm_fast"


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class Planner:
    """Stage 1 战术决策者：判断"要不要参与 + 如何参与"。

    非线程安全；仅在 AmaidesuDecider 的单一 asyncio 事件循环内使用。
    通过 ``plan()`` 方法驱动，**不订阅 EventBus**——它是 Decider 内部子组件。
    """

    #: Planner 专用的提示词模板名（v2，零人设注入）
    TEMPLATE_NAME: str = "decision/amaidesu_planner_v2"

    def __init__(
        self,
        config: Any,
        llm_service: Any,
        prompt_service: Any,
        room_state: RoomState,
        capabilities_provider: Any = None,
    ) -> None:
        """初始化 Planner。

        Args:
            config: 配置字典或已解析的配置对象。支持两种形式：
                - ``dict``：经 ``_PlannerConfig.from_dict()`` 解析（自动剥离未知字段）
                - 已解析对象：若有 ``planner_client`` 属性则直接读取
            llm_service: LLM 管理器（``LLMManager`` 或鸭子类型），
                需提供 ``async chat(prompt, *, client_type) -> Response`` 接口
            prompt_service: 提示词管理器（``PromptManager`` 或鸭子类型），
                需提供 ``render_safe(template_name, **vars) -> str`` 接口
            room_state: 直播间态势规则层实例（``RoomState``）
            capabilities_provider: 可选的能力提供者，用于向 prompt 注入可用动作清单。
                None 时 prompt 的 action_list 为空串。
        """
        # 解析配置（容忍 dict / 已解析对象 / None）
        if config is None:
            self.typed_config = _PlannerConfig()
        elif hasattr(config, "planner_client"):
            # 已解析的配置对象（如 AmaidesuDecider.ConfigSchema 实例）
            self.typed_config = _PlannerConfig(planner_client=config.planner_client)
        elif isinstance(config, dict):
            self.typed_config = _PlannerConfig.from_dict(config)
        else:
            # 兜底：尝试当作 dict-like 解析
            self.typed_config = _PlannerConfig.from_dict(dict(config))

        self.planner_client: str = self.typed_config.planner_client

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._room_state = room_state
        self._capabilities_provider = capabilities_provider

        self.logger = get_logger("Planner")

    # ==================== 主入口 ====================

    async def plan(
        self,
        batch: List[Any],
        *,
        forced: bool = False,
    ) -> Optional[DecisionPlan]:
        """对一批弹幕做战术决策，产出 DecisionPlan。

        Args:
            batch: 本批弹幕列表（``NormalizedMessage`` 或鸭子类型）
            forced: 是否为强制回应批次（SC / 礼物 / 上舰等）。会透传到 prompt 的
                ``$forced`` 变量，影响 LLM 的 should_reply 判断。

        Returns:
            DecisionPlan：解析成功时返回；LLM 异常 / 脏 JSON / 调用失败时返回 None，
            由调用方（Decider 编排层）处理降级。
        """
        # 1. 组装上下文
        snapshot = self._room_state.get_snapshot()
        room_state_text = self._render_room_state(snapshot)
        danmaku_text = self._render_batch(batch)
        action_list = self._get_action_list()

        # 2. 渲染 prompt（★ 无 persona 变量）
        #    forced 透传为字符串（"true"/"false"），对齐模板中的文档约定
        try:
            prompt = self._prompt_service.render_safe(
                self.TEMPLATE_NAME,
                room_state=room_state_text,
                danmaku_batch=danmaku_text,
                forced=str(forced).lower(),
                action_list=action_list,
            )
        except Exception as e:
            self.logger.error(f"渲染 Planner prompt 失败: {e}", exc_info=True)
            return None

        # 3. 调用 LLM（★ 无 tools 参数）
        try:
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self.planner_client,
            )
        except Exception as e:
            self.logger.warning(f"Planner LLM 调用异常，返回 None 由调用方降级: {e}")
            return None

        # 4. 提取文本内容（兼容 LLMResponse / str 两种返回形式）
        content = self._extract_content(response)
        if content is None:
            self.logger.warning("Planner LLM 返回空内容或调用失败（success=False）")
            return None

        # 5. 清理 + 解析 JSON → DecisionPlan
        cleaned = self._clean_llm_json(content)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.logger.warning(f"Planner JSON 解析失败: {e}, 原始内容前 200 字: {cleaned[:200]}")
            return None

        if not isinstance(parsed, dict):
            self.logger.warning(f"Planner JSON 顶层非对象: {type(parsed).__name__}")
            return None

        return self._build_plan(parsed)

    # ==================== 辅助方法 ====================

    @staticmethod
    def _extract_content(response: Any) -> Optional[str]:
        """从 LLM 响应中提取文本内容。

        兼容两种返回形式：
        - ``LLMResponse`` 对象（实际运行时，含 ``.success`` / ``.content`` 字段）
        - ``str``（简化 mock 场景，如 QA 脚本中的 ``AsyncMock(return_value=json.dumps(...))``）

        Args:
            response: LLM 返回值

        Returns:
            文本内容；调用失败（success=False）或无内容时返回 None
        """
        if isinstance(response, str):
            return response
        # 鸭子类型：检查 success 标志
        success = getattr(response, "success", True)
        if success is False:
            return None
        return getattr(response, "content", None)

    def _build_plan(self, parsed: dict) -> Optional[DecisionPlan]:
        """从解析后的 JSON 字典构造 DecisionPlan。

        Args:
            parsed: JSON 解析后的字典

        Returns:
            DecisionPlan 实例；字段类型不匹配时返回 None
        """
        try:
            return DecisionPlan(
                should_reply=bool(parsed.get("should_reply", False)),
                target=(parsed.get("target") or None),
                topic_summary=str(parsed.get("topic_summary", "") or ""),
                reply_guidance=str(parsed.get("reply_guidance", "") or ""),
                confidence=float(parsed.get("confidence", 0.0) or 0.0),
            )
        except (TypeError, ValueError) as e:
            self.logger.warning(f"构造 DecisionPlan 失败（字段类型不匹配）: {e}")
            return None
        except Exception as e:
            # Pydantic ValidationError 等其他异常也走降级
            self.logger.warning(f"构造 DecisionPlan 失败: {e}")
            return None

    def _render_room_state(self, snapshot: RoomStateSnapshot) -> str:
        """将直播间态势快照渲染为人类可读的文本块（供 prompt 注入）。

        Args:
            snapshot: RoomState.get_snapshot() 返回的快照

        Returns:
            多行文本，包含热度 / 话题 / SC 队列 / 话题摘要
        """
        parts: List[str] = []

        # 热度
        heat_map = {"low": "冷场（弹幕稀少）", "medium": "正常节奏", "high": "高热（弹幕密集）"}
        heat_desc = heat_map.get(getattr(snapshot, "heat", "low"), snapshot.heat)
        parts.append(f"- 热度等级: {heat_desc}")

        # 话题关键词
        topics = getattr(snapshot, "topics", None) or []
        if topics:
            parts.append(f"- 话题关键词: {', '.join(topics)}")

        # SC 队列
        sc_queue = getattr(snapshot, "sc_queue", None) or []
        if sc_queue:
            parts.append(f"- 待处理 SC/礼物/上舰: {len(sc_queue)} 条")

        # 话题摘要（Task 8 低频 LLM 摘要填充，默认空）
        topic_summary = getattr(snapshot, "topic_summary", "") or ""
        if topic_summary:
            parts.append(f"- 话题摘要: {topic_summary}")

        return "\n".join(parts) if parts else "- （暂无态势数据）"

    def _render_batch(self, batch: List[Any]) -> str:
        """将一批弹幕渲染为文本块（供 prompt 注入）。

        复用 MessageBuffer.render_batch_text 的格式约定，但保持独立实现以避免
        对 MessageBuffer 的硬依赖（Planner 不关心缓冲逻辑）。

        Args:
            batch: 弹幕消息列表

        Returns:
            多行文本，每行一条弹幕；空批返回占位文本
        """
        if not batch:
            return "（本批无弹幕）"

        lines: List[str] = []
        for msg in batch:
            text = getattr(msg, "text", None) or str(msg)
            nickname = getattr(msg, "user_nickname", None) or getattr(msg, "user_id", None) or "观众"
            data_type = getattr(msg, "data_type", "text") or "text"

            type_tag = {
                "super_chat": "[醒目留言] ",
                "guard": "[上舰] ",
                "gift": "[礼物] ",
                "enter": "[入场] ",
            }.get(data_type, "")

            lines.append(f"{type_tag}{nickname}: {text}")
        return "\n".join(lines)

    def _get_action_list(self) -> str:
        """获取可用动作清单文本（供 prompt 注入）。

        从 capabilities_provider 惰性查询；无 provider 或查询失败时返回空串
        （prompt 模板中 ``$action_list`` 会被 ``render_safe`` 保留为字面子串，
        但 Planner 的模板 v2 未使用该变量，空串足够）。

        Returns:
            动作清单文本；无可用动作时返回空串
        """
        if self._capabilities_provider is None:
            return ""

        try:
            view = self._capabilities_provider.get_all_capabilities()
        except Exception as e:
            self.logger.warning(f"查询 Output 能力失败: {e}")
            return ""

        lines: List[str] = []
        for entry in getattr(view, "actions", []):
            desc = getattr(entry, "description", "") or ""
            lines.append(f"- {entry.name}: {desc}")
        return "\n".join(lines)

    @staticmethod
    def _clean_llm_json(raw_output: str) -> str:
        """清理 LLM 返回的 JSON 字符串。

        三步清理（与 AmaidesuDecider._clean_llm_json 一致）：
        1. 剥离 `````json`` / ``````` 代码块包裹
        2. 截取首个 ``{`` 到末个 ``}`` 之间的内容（去掉 JSON 前后的解释文字）
        3. 修复尾随逗号（``,}`` → ``}``，``,]`` → ``]``）

        Args:
            raw_output: LLM 原始返回文本

        Returns:
            清理后的 JSON 字符串
        """
        cleaned = raw_output.strip()
        # 剥离 markdown 代码块包裹
        cleaned = re.sub(r"^```json\s*", "", cleaned)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        # 截取最外层 { } 之间的内容
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]

        # 修复尾随逗号（LLM 常见错误）
        cleaned = re.sub(r",\s*}", "}", cleaned)
        cleaned = re.sub(r",\s*]", "]", cleaned)
        return cleaned
