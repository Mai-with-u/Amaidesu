"""AgendaLoader - 节目单 TOML 加载器 + 每环节动态 AI 扩展器（Wave 6 / §1.7）

职责
----
1. **TOML 加载**：读取主播预定义的节目单 TOML → 解析为 :class:`Agenda`。
2. **环节扩展**：环节进入时调用 LLM（独立 profile ``llm_agenda``，Wave 6 由
   ``llm_outline`` 重命名）生成开场白 / 话题引导 / 讨论要点（``ExpandedSegment``），
   缓存注入提示词。

设计要点
--------
- **主播 Agent 内部组件**：与 :mod:`plan.py` 的 ``DecisionPlan`` 同类，置于
  ``src/agents/streamer/`` 而非 ``src/modules/types/``。
- **独立 LLM profile**：使用 ``llm_agenda``（v2 重命名）与 Planner(llm_fast) /
  Replyer(llm) 隔离连接池——仿 ``RoomStateLoop`` 使用 ``llm_summary`` 的先例。
- **绝不抛异常中断环节**：LLM 调用失败 / 脏 JSON / 解析异常 → 1 次重试 → 仍失败则
  fallback（``opening_line=""``、``topic_guidance=segment.task_description``、
  ``talking_points=[]``）。
- **JSON 解析容错**：复用 ``Planner._clean_llm_json`` 的三步清理模式
  （剥离 ```json 包裹、截取首末 { }、修复尾随逗号）。
- **零事件发布**：Loader 纯组件，**不**直接 emit 任何事件——调度通知由
  ``agenda_idle`` 通过 ``on_advance`` 回调驱动 Agent 走正常决策链。

不做什么
--------
- 不实现调度循环（agenda_idle）
- 不维护状态机（agenda_state）
- 不实现 Planner/Replyer 注入（planner.py / replyer.py）
- 不写持久化（delegated to agenda_store via AgendaState）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.modules.logging import get_logger

from .agenda import AgendaSegment, Agenda, parse_agenda_toml

__all__ = ["ExpandedSegment", "AgendaLoader"]


# ---------------------------------------------------------------------------
# 扩展环节（数据契约）
# ---------------------------------------------------------------------------


class ExpandedSegment(BaseModel):
    """节目单中单个环节的扩展内容（数据契约）。

    字段说明：
    - ``segment_id``：回引到 :class:`AgendaSegment.id`，便于缓存键 / 反查
    - ``opening_line``：本环节开场白（首句话，1-2 句即可）
    - ``topic_guidance``：本环节话题引导（注入 Planner/Replyer 提示词的核心内容）
    - ``talking_points``：可讨论要点清单（注入提示词的辅助列表）

    失败兜底：LLM 不可用时 ``opening_line=""``、``topic_guidance=segment.task_description``、
    ``talking_points=[]``，保证环节仍能正常推进。
    """

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(..., description="回引到 AgendaSegment.id，便于缓存键 / 反查")
    opening_line: str = Field(default="", description="本环节开场白(首句话，1-2 句)")
    topic_guidance: str = Field(
        default="",
        description="本环节话题引导（注入 Planner/Replyer 提示词的核心内容）",
    )
    talking_points: List[str] = Field(
        default_factory=list,
        description="可讨论要点列表（注入提示词的辅助列表）",
    )


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------


class AgendaLoader:
    """节目单加载器 + 环节 AI 扩展器。

    非线程安全；仅在 StreamerAgent 的单一 asyncio 事件循环内使用。

    注入：
    - ``llm_manager``：``LLMManager`` 实例，提供 ``async chat(prompt, *, client_type, ...)``
    - ``prompt_manager``：``PromptManager`` 实例，提供 ``render_safe(name, **vars) -> str``
    - ``config``：配置字典或对象；读取 ``agenda_expand_client``（默认 ``llm_agenda``）
    """

    #: AI 扩展专用提示词模板名（Wave 6 重命名，原 decision/outline_expand）
    TEMPLATE_NAME: str = "decision/agenda_expand"

    #: 默认 LLM profile（独立连接池，仿 llm_summary 先例）
    DEFAULT_EXPAND_CLIENT: str = "llm_agenda"

    def __init__(
        self,
        *,
        llm_manager: Any,
        prompt_manager: Any,
        config: Any,
    ) -> None:
        self._llm = llm_manager
        self._prompt = prompt_manager
        self._logger = get_logger("AgendaLoader")

        if config is None:
            self._expand_client = self.DEFAULT_EXPAND_CLIENT
        elif isinstance(config, dict):
            # 同时支持 agenda_expand_client（新命名）+ outline_expand_client（向后兼容）
            self._expand_client = config.get(
                "agenda_expand_client",
                config.get("outline_expand_client", self.DEFAULT_EXPAND_CLIENT),
            )
        else:
            self._expand_client = getattr(
                config,
                "agenda_expand_client",
                getattr(config, "outline_expand_client", self.DEFAULT_EXPAND_CLIENT),
            )

    # -------- TOML 加载 --------

    async def load(self, path: str | Path) -> Agenda:
        """加载节目单 TOML 文件并解析为 Agenda。

        Args:
            path: TOML 文件路径

        Returns:
            校验通过的 Agenda 实例
        """
        # 同步解析；Wave 6 不引入异步 IO（TOML 加载是轻量操作）
        return parse_agenda_toml(Path(path))

    # -------- 环节 AI 扩展 --------

    async def expand_segment(self, segment: AgendaSegment) -> ExpandedSegment:
        """对单个环节做 AI 扩展。

        流程：
        1. 渲染 prompt（``decision/agenda_expand``）
        2. 调 LLM（独立 profile ``llm_agenda``）
        3. 解析 JSON（含三步清理 + 重试）
        4. 失败 fallback 到 ``segment.task_description``

        Args:
            segment: 待扩展的 AgendaSegment

        Returns:
            ExpandedSegment 实例（LLM 失败时使用 fallback 字段）
        """
        last_error: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                prompt = self._prompt.render_safe(
                    self.TEMPLATE_NAME,
                    segment_id=segment.id,
                    segment_title=segment.title,
                    task_description=segment.task_description,
                    duration_ms=segment.duration_ms,
                    key_points="\n".join(segment.key_points) if segment.key_points else "（无）",
                )
                response = await self._llm.chat(
                    prompt=prompt,
                    client_type=self._expand_client,
                )
                content = getattr(response, "content", None)
                if not content:
                    raise RuntimeError("LLM 返回空内容")
                cleaned = _clean_llm_json(content)
                data = json.loads(cleaned)
                if not isinstance(data, dict):
                    raise RuntimeError("LLM 返回非 dict JSON")

                return ExpandedSegment(
                    segment_id=segment.id,
                    opening_line=str(data.get("opening_line", "") or ""),
                    topic_guidance=str(data.get("topic_guidance", "") or segment.task_description),
                    talking_points=[str(p) for p in (data.get("talking_points", []) or []) if p],
                )
            except Exception as e:
                last_error = e
                self._logger.warning(f"AgendaLoader.expand_segment 第 {attempt} 次失败: {e}")
                continue

        # 全部失败 → fallback 到任务描述
        self._logger.warning(
            f"AgendaLoader.expand_segment 彻底失败，使用 fallback: segment_id={segment.id}, error={last_error}"
        )
        return ExpandedSegment(
            segment_id=segment.id,
            opening_line="",
            topic_guidance=segment.task_description,
            talking_points=[],
        )


def _clean_llm_json(raw_output: str) -> str:
    """清理 LLM 返回的 JSON 字符串（与 Planner._clean_llm_json 同构三步清理）。"""
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
