"""OutlineLoader - 直播大纲 TOML 加载器 + 每环节动态 AI 扩展器。

职责
----
1. **TOML 加载**：读取主播预定义的直播大纲 TOML → 解析为 :class:`StreamOutline`。
2. **环节扩展**：环节进入时调用 LLM（独立 profile ``llm_outline``）生成开场白 / 话题引导 /
   讨论要点（``ExpandedSegment``），缓存注入提示词。

设计要点
--------
- **决策阶段内部组件**：与 :mod:`plan.py` 的 ``DecisionPlan`` 同类，置于
  ``src/stages/decision/deciders/amaidesu/`` 而非 ``src/modules/types/``。
- **独立 LLM profile**：使用 :data:`src.modules.llm.manager.ClientType.LLM_OUTLINE`
  ("llm_outline")，与 Planner(llm_fast) / Replyer(llm) 隔离连接池——仿
  ``RoomStateLoop`` 使用 ``llm_summary`` 的先例（Task 5 调研结论）。
- **绝不抛异常中断环节**：LLM 调用失败 / 脏 JSON / 解析异常 → 1 次重试 → 仍失败则
  fallback（``opening_line=""``、``topic_guidance=segment.task_description``、
  ``talking_points=[]``）。这是配置层硬约束（"失败 fallback 任务描述原文"）。
- **JSON 解析容错**：复用 ``Planner._clean_llm_json`` 的三步清理模式
  （剥离 ```json 包裹、截取首末 { }、修复尾随逗号）。
- **零事件发布**：Loader 纯组件，**不在 Decider 外发布任何事件**——调度通知由
  ``OutlineScheduler`` 通过 ``on_advance`` 回调驱动 Decider 走正常决策链。

不做什么
--------
- 不实现调度循环（Task 9）
- 不维护状态机（Task 6）
- 不实现 Planner/Replyer 注入（Task 7/10）
- 不写持久化
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from src.modules.logging import get_logger

from .outline import OutlineSegment, StreamOutline, parse_outline_toml

__all__ = ["ExpandedSegment", "OutlineLoader"]


# ---------------------------------------------------------------------------
# 扩展环节（数据契约）
# ---------------------------------------------------------------------------


class ExpandedSegment(BaseModel):
    """环节经 LLM 动态扩展后的内容，缓存注入提示词。

    字段说明：
    - ``segment_id``：回引到 :class:`OutlineSegment.id`，便于缓存键 / 反查
    - ``opening_line``：本环节开场白（首句话，1-2 句即可）
    - ``topic_guidance``：本环节话题引导（注入 Planner/Replyer 提示词的核心内容）
    - ``talking_points``：可讨论要点清单（注入提示词的辅助列表）

    失败兜底：LLM 不可用时 ``opening_line=""``、``topic_guidance=segment.task_description``、
    ``talking_points=[]``，保证环节仍能正常推进。
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(..., description="回引到 OutlineSegment.id，便于缓存键 / 反查")
    opening_line: str = Field(default="", description="本环节开场白（首句话，1-2 句）")
    topic_guidance: str = Field(
        default="",
        description="本环节话题引导（注入 Planner/Replyer 提示词的核心内容）",
    )
    talking_points: List[str] = Field(
        default_factory=list,
        description="可讨论要点清单（注入提示词的辅助列表）",
    )


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------


class OutlineLoader:
    """直播大纲加载器 + 环节 AI 扩展器。

    非线程安全；仅在 AmaidesuDecider 的单一 asyncio 事件循环内使用。

    注入：
    - ``llm_manager``：``LLMManager`` 实例，提供 ``async chat(prompt, *, client_type, ...)``
    - ``prompt_manager``：``PromptManager`` 实例，提供 ``render_safe(name, **vars) -> str``
    - ``config``：配置字典或对象；读取 ``outline_expand_client``（默认 ``llm_outline``）
    """

    #: AI 扩展专用提示词模板名
    TEMPLATE_NAME: str = "decision/outline_expand"

    #: 默认 LLM profile（独立连接池，仿 llm_summary 先例）
    DEFAULT_EXPAND_CLIENT: str = "llm_outline"

    def __init__(
        self,
        llm_manager: Any,
        prompt_manager: Any,
        config: Union[Dict[str, Any], Any, None] = None,
    ) -> None:
        """初始化 OutlineLoader。

        Args:
            llm_manager: LLM 管理器（``LLMManager`` 或鸭子类型），
                需提供 ``async chat(prompt, *, client_type, temperature, max_tokens) -> LLMResponse``。
                None 时 Loader 仍能 ``load()``，但 ``expand_segment()`` 必走 fallback。
            prompt_manager: 提示词管理器（``PromptManager`` 或鸭子类型），
                需提供 ``render_safe(template_name, **vars) -> str``。
                None 时 ``expand_segment()`` 必走 fallback。
            config: 配置字典或已解析对象；支持键 ``outline_expand_client``。
                None / 缺字段时使用默认 ``llm_outline``。
        """
        self._llm_manager = llm_manager
        self._prompt_manager = prompt_manager

        # 解析配置（容忍 dict / 已解析对象 / None）
        if config is None:
            self._expand_client: str = self.DEFAULT_EXPAND_CLIENT
        elif hasattr(config, "outline_expand_client"):
            # hasattr 已守住类型,此处 getattr 必然非空
            self._expand_client = str(getattr(config, "outline_expand_client") or self.DEFAULT_EXPAND_CLIENT)  # noqa: B009
        elif isinstance(config, dict):
            self._expand_client = str(config.get("outline_expand_client") or self.DEFAULT_EXPAND_CLIENT)
        else:
            # 兜底：尝试当作 dict-like 解析
            try:
                self._expand_client = str(dict(config).get("outline_expand_client") or self.DEFAULT_EXPAND_CLIENT)
            except Exception:
                self._expand_client = self.DEFAULT_EXPAND_CLIENT

        self.logger = get_logger("OutlineLoader")

    # ==================== TOML 加载 ====================

    async def load(self, path: Union[str, Path]) -> StreamOutline:
        """从 TOML 文件加载并校验大纲。

        复用 :func:`parse_outline_toml`（Task 1 实现，含完整 Pydantic 校验）。
        本方法仅是异步包装 + 异常友好化（异常向上传播，由 Decider 决定如何处理
        —— 计划：保持上一份有效配置，不在 Loader 层吞错）。

        Args:
            path: TOML 文件路径

        Returns:
            校验通过的 :class:`StreamOutline`

        Raises:
            FileNotFoundError: 文件不存在
            PermissionError: 无读取权限
            tomllib.TOMLDecodeError: TOML 语法错误
            pydantic.ValidationError: 结构 / 字段 / 跨字段校验失败
        """
        path = Path(path)
        # 同步 IO 在 asyncio 中用 to_thread 避免阻塞（即使 TOML 文件通常很小）
        # —— 与 Planner / Replyer 同步解析保持一致（plan.py 也未包 to_thread），
        # 这里同样保持简洁，TOML 加载是冷启动期单次操作，不进热路径。
        outline = parse_outline_toml(path)
        self.logger.info(
            f"OutlineLoader 加载大纲成功: {outline.outline_id!r} "
            f"({len(outline.segments)} 个环节, title={outline.title!r})"
        )
        return outline

    # ==================== 环节 AI 扩展 ====================

    async def expand_segment(
        self,
        segment: OutlineSegment,
        persona: Optional[Dict[str, Any]] = None,
        history: Optional[List[Any]] = None,
        *,
        prev_topic: str = "",
    ) -> ExpandedSegment:
        """环节进入时调用 LLM 生成开场白 / 话题引导 / 讨论要点。

        **绝不抛异常**：LLM 异常 / 脏 JSON / 解析失败 → 1 次重试 → 仍失败则 fallback。
        Fallback 内容：``opening_line=""``、``topic_guidance=segment.task_description``、
        ``talking_points=[]``，保证环节推进不中断。

        Args:
            segment: 要扩展的环节
            persona: 主播人设字典（含 ``bot_name`` / ``personality`` / ``style_constraints``），
                用于在 prompt 中注入人设；None 时使用占位文本
            history: 最近对话历史（鸭子类型列表，元素需有 ``role`` / ``content`` 属性）；
                None / 空时使用占位文本
            prev_topic: 上一环节的话题摘要（来自 OutlineState.expanded_cache[prev_id].topic_guidance），
                避免连续环节重复话题；空字符串时使用占位文本

        Returns:
            :class:`ExpandedSegment`；失败 fallback 时内容降级但实例本身仍正常返回
        """
        # 渲染 prompt（依赖缺失时直接走 fallback——不抛异常）
        prompt = self._render_prompt(segment, persona, history, prev_topic)
        if prompt is None:
            return self._fallback(segment)

        # 1 次重试 + 兜底
        for attempt in (1, 2):
            content = await self._call_llm(prompt)
            if content is None:
                # LLM 调用异常或返回空，尝试下一次
                if attempt == 1:
                    self.logger.debug(f"expand_segment 第一次尝试失败(LLM 调用),segment_id={segment.id!r},准备重试")
                    continue
                self.logger.warning(f"expand_segment 第二次仍失败,segment_id={segment.id!r},启用 fallback")
                return self._fallback(segment)

            parsed = self._parse_json(content)
            if parsed is None:
                if attempt == 1:
                    self.logger.debug(f"expand_segment 第一次尝试 JSON 解析失败,segment_id={segment.id!r},准备重试")
                    continue
                self.logger.warning(f"expand_segment 第二次 JSON 仍解析失败,segment_id={segment.id!r},启用 fallback")
                return self._fallback(segment)

            expanded = self._build_expanded(parsed, segment)
            if expanded is None:
                # 解析成功但字段类型不匹配——同样算脏 JSON
                if attempt == 1:
                    self.logger.debug(f"expand_segment 第一次尝试字段构建失败,segment_id={segment.id!r},准备重试")
                    continue
                self.logger.warning(f"expand_segment 第二次仍字段构建失败,segment_id={segment.id!r},启用 fallback")
                return self._fallback(segment)

            return expanded

        # 理论不可达（for 循环必然 return 或 continue），但保留兜底
        return self._fallback(segment)

    # ==================== 内部方法：渲染 prompt ====================

    def _render_prompt(
        self,
        segment: OutlineSegment,
        persona: Optional[Dict[str, Any]],
        history: Optional[List[Any]],
        prev_topic: str,
    ) -> Optional[str]:
        """渲染扩展器 prompt；服务缺失时返回 None（由调用方走 fallback）。

        使用 :meth:`PromptManager.render_safe`：缺失变量保留字面 ``$xxx`` 而非抛
        KeyError，保证模板 / 服务兼容性。
        """
        if self._prompt_manager is None:
            self.logger.debug("PromptManager 未注入,跳过 LLM 扩展走 fallback")
            return None

        try:
            return self._prompt_manager.render_safe(
                self.TEMPLATE_NAME,
                title=segment.title,
                task_description=segment.task_description,
                key_points=self._render_key_points(segment.key_points),
                personality=self._render_personality(persona),
                history=self._render_history(history),
                prev_topic=prev_topic or "（无上一环节,这是开场）",
            )
        except Exception as e:
            self.logger.warning(f"渲染 outline_expand prompt 失败,启用 fallback: {e}")
            return None

    @staticmethod
    def _render_key_points(key_points: List[str]) -> str:
        """渲染关键节点列表（每行一个，空时返回占位）。"""
        if not key_points:
            return "（无）"
        return "\n".join(f"- {kp}" for kp in key_points)

    @staticmethod
    def _render_personality(persona: Optional[Dict[str, Any]]) -> str:
        """渲染人设文本（personality 字段优先；缺则用 bot_name 兜底）。"""
        if not persona:
            return "（未提供人设）"
        personality = (persona.get("personality") or "").strip()
        if personality:
            return personality
        bot_name = (persona.get("bot_name") or "").strip()
        return f"主播: {bot_name}" if bot_name else "（未提供人设）"

    @staticmethod
    def _render_history(history: Optional[List[Any]]) -> str:
        """渲染会话历史（与 Planner / RoomStateLoop 同构）。"""
        if not history:
            return "（暂无对话历史）"
        lines: List[str] = []
        for msg in history:
            role = getattr(msg, "role", None)
            role_str = getattr(role, "value", str(role)) if role else "user"
            content = getattr(msg, "content", "") or ""
            lines.append(f"{role_str}: {content}")
        return "\n".join(lines) if lines else "（暂无对话历史）"

    # ==================== 内部方法：调用 LLM ====================

    async def _call_llm(self, prompt: str) -> Optional[str]:
        """调用 LLM 并提取文本内容；失败时返回 None。

        兼容两种返回形式（与 Planner / Replyer 一致）：
        - ``LLMResponse`` 对象（生产）：检查 ``.success``，取 ``.content``
        - 字符串（mock）：原样返回

        Args:
            prompt: 已渲染的扩展器 prompt

        Returns:
            LLM 文本内容；调用失败 / 返回空时返回 None
        """
        if self._llm_manager is None:
            self.logger.debug("LLMManager 未注入,跳过 LLM 调用")
            return None

        try:
            response = await self._llm_manager.chat(
                prompt=prompt,
                client_type=self._expand_client,
            )
        except Exception as e:
            self.logger.warning(f"OutlineLoader LLM 调用异常(client={self._expand_client!r}): {e}")
            return None

        return self._extract_content(response)

    @staticmethod
    def _extract_content(response: Any) -> Optional[str]:
        """从 LLM 响应提取文本（兼容 LLMResponse / str）。

        Args:
            response: LLM 调用返回值

        Returns:
            文本内容；失败（success=False / 无 content / 空）时返回 None
        """
        if isinstance(response, str):
            return response if response.strip() else None

        # 鸭子类型：检查 success 标志（LLMResponse 含 .success 字段）
        success = getattr(response, "success", True)
        if success is False:
            return None

        content = getattr(response, "content", None)
        if not content or not str(content).strip():
            return None
        return str(content)

    # ==================== 内部方法：JSON 解析 ====================

    @staticmethod
    def _clean_llm_json(raw_output: str) -> str:
        """清理 LLM 返回的 JSON（三步清理，与 Planner / Replyer 一致）。

        1. 剥离 ``\\`\\`\\`json`` / ``\\`\\`\\```` 代码块包裹
        2. 截取首个 ``{`` 到末个 ``}`` 之间的内容（去掉 JSON 前后的解释文字）
        3. 修复尾随逗号（``,\\}`` → ``}``，``,]`` → ``]``）

        Args:
            raw_output: LLM 原始返回文本

        Returns:
            清理后的 JSON 字符串
        """
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

    @classmethod
    def _parse_json(cls, content: str) -> Optional[Dict[str, Any]]:
        """清理 + 解析 LLM 输出 JSON。

        Args:
            content: LLM 原始文本

        Returns:
            解析后的 dict；解析失败返回 None
        """
        cleaned = cls._clean_llm_json(content)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            # 不在此层打印原文,留给调用方按需记录
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    @classmethod
    def _build_expanded(cls, parsed: Dict[str, Any], segment: OutlineSegment) -> Optional[ExpandedSegment]:
        """从解析后的 JSON 构造 :class:`ExpandedSegment`。

        字段类型不匹配 / Pydantic 校验失败返回 None（由调用方走 fallback / 重试）。

        Args:
            parsed: 解析后的 dict
            segment: 原始环节（用于回填 segment_id）

        Returns:
            :class:`ExpandedSegment`；失败返回 None
        """
        try:
            opening_line = str(parsed.get("opening_line", "") or "").strip()
            topic_guidance = str(parsed.get("topic_guidance", "") or "").strip()
            raw_points = parsed.get("talking_points") or []
            if not isinstance(raw_points, list):
                raw_points = []
            talking_points = [str(p).strip() for p in raw_points if str(p or "").strip()]

            return ExpandedSegment(
                segment_id=segment.id,
                opening_line=opening_line,
                topic_guidance=topic_guidance,
                talking_points=talking_points,
            )
        except (TypeError, ValueError):
            return None
        except Exception:
            # Pydantic ValidationError 等其他异常
            return None

    # ==================== 内部方法：Fallback ====================

    @staticmethod
    def _fallback(segment: OutlineSegment) -> ExpandedSegment:
        """失败兜底：用环节原始任务描述填充 topic_guidance，其他留空。

        这是计划明确的硬约束："失败 fallback 任务描述原文"。
        调用方（Decider / Scheduler）拿到这个 fallback 实例后仍能正常推进环节，
        仅失去了 LLM 生成的细节（开场白 / 讨论要点为空）。

        Args:
            segment: 原始环节

        Returns:
            fallback 内容的 :class:`ExpandedSegment`
        """
        return ExpandedSegment(
            segment_id=segment.id,
            opening_line="",
            topic_guidance=segment.task_description,
            talking_points=[],
        )
