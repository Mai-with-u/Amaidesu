"""文字冒险 Agent 专属工具（choose_option / get_story）

按架构 §1.5.1 / §1.49 第 2 面定案：
- 游戏专属推进工具 = 游戏 Agent 自己准备（list_tools 声明）
- provider="game"（来源溯源：玩家引擎 Agent 声明的工具）
- 这些工具**不是**公用感知/控制工具 —— 它们内含游戏域逻辑
  （option_id → content_engine 翻译）

落地两个工具：
- ``choose_option``：推进（核心推进工具）
- ``get_story``：读当前剧情段（只读）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from src.modules.logging import get_logger
from src.modules.tools.content_engine import ContentEngine, ContentInput
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider

from .state import TextAdvGameAgentState, TextAdvOption

logger = get_logger("text_adv_tools")


# ---------------------------------------------------------------------------
# ToolSpec 工厂
# ---------------------------------------------------------------------------


def build_choose_option_spec() -> ToolSpec:
    """``choose_option`` 工具规格——游戏推进核心入口"""
    return ToolSpec(
        name="choose_option",
        description=(
            "选择文字冒险游戏当前剧情段的某个选项（option_id）。"
            "内部把选项翻译为 content_engine 输入（点击/按键/命令）并执行。"
            "返回推进结果（accepted / echoed / error）。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "option_id": {
                    "type": "string",
                    "description": "目标选项 ID（须在当前 options 列表中）",
                },
            },
            "required": ["option_id"],
        },
        kind="sync",
        provider="game",
        output_schema={
            "type": "object",
            "properties": {
                "accepted": {"type": "boolean"},
                "echoed": {"type": "string"},
                "option_id": {"type": "string"},
            },
        },
    )


def build_get_story_spec() -> ToolSpec:
    """``get_story`` 工具规格——读取当前剧情段（只读）"""
    return ToolSpec(
        name="get_story",
        description=(
            "读取当前文字冒险游戏剧情段（scene_id / scene_text / options / history）。"
            "只读操作，不触发 content_engine 输入。"
        ),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider="game",
        output_schema={
            "type": "object",
            "properties": {
                "scene_id": {"type": "string"},
                "scene_text": {"type": "string"},
                "options": {"type": "array"},
                "history": {"type": "array"},
            },
        },
    )


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TextAdvToolProvider(ToolProvider):
    """文字冒险 Agent 的专属工具 Provider（provider="game"）

    持有 :class:`TextAdvGameAgentState` 和 :class:`ContentEngine`，
    把选项推进翻译为 content_engine 输入调用。

    注入模式（§1.49 继承 + 构造注入）：
        >>> provider = TextAdvToolProvider(state=state, engine=engine)
        >>> registry.register_provider(provider)
    """

    state: TextAdvGameAgentState
    engine: ContentEngine

    # 测试/统计辅助字段
    choose_option_calls: int = 0
    get_story_calls: int = 0

    @property
    def name(self) -> str:
        return "TextAdvToolProvider"

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_choose_option_spec(), build_get_story_spec()]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        name = invocation.tool_name
        args: Dict[str, Any] = invocation.arguments or {}
        started_ms = int(time.time() * 1000)

        try:
            if name == "choose_option":
                return await self._invoke_choose_option(args, started_ms)
            if name == "get_story":
                return await self._invoke_get_story(started_ms)
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"未知 TextAdv 工具 '{name}'",
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 边界处兜底
            logger.warning(f"TextAdv 工具 '{name}' 执行失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name=name,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )

    # ---- 内部 dispatch ----

    async def _invoke_choose_option(
        self,
        args: Dict[str, Any],
        started_ms: int,
    ) -> ToolExecutionResult:
        self.choose_option_calls += 1
        option_id = str(args.get("option_id", "") or "").strip()
        if not option_id:
            return ToolExecutionResult(
                tool_name="choose_option",
                success=False,
                error_message="缺少 option_id",
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        # 1. 查选项
        chosen: Optional[TextAdvOption] = None
        for opt in self.state.options:
            if opt.option_id == option_id:
                chosen = opt
                break
        if chosen is None:
            return ToolExecutionResult(
                tool_name="choose_option",
                success=False,
                error_message=f"选项 '{option_id}' 不在当前选项列表",
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        # 2. 翻译为 content_engine 输入
        content_input = ContentInput(
            kind=chosen.advance_kind,  # type: ignore[arg-type]
            x=chosen.advance_payload.get("x", 0) if isinstance(chosen.advance_payload, dict) else 0,
            y=chosen.advance_payload.get("y", 0) if isinstance(chosen.advance_payload, dict) else 0,
            button=chosen.advance_payload.get("button", "left") if isinstance(chosen.advance_payload, dict) else "left",
            key=chosen.advance_key,
            command=chosen.advance_payload.get("command", "") if isinstance(chosen.advance_payload, dict) else "",
            raw=chosen.advance_payload.get("raw", "") if isinstance(chosen.advance_payload, dict) else "",
            payload=dict(chosen.advance_payload or {}),
        )

        # 3. 通过 content_engine 执行
        result = await self.engine.send_input(content_input)
        if not result.accepted:
            return ToolExecutionResult(
                tool_name="choose_option",
                success=False,
                error_message=result.error_message or "引擎拒绝输入",
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )

        # 4. 记录决策（仅在 Agent 尚未记账时追加，避免与 pick_default_option 双重）
        if not self.state.history or self.state.history[-1] != chosen.option_id:
            self.state.last_decision = chosen.option_id
            self.state.history.append(chosen.option_id)

        return ToolExecutionResult(
            tool_name="choose_option",
            success=True,
            content=result.echoed or "accepted",
            structured_content={
                "accepted": True,
                "echoed": result.echoed,
                "option_id": chosen.option_id,
                "label": chosen.label,
            },
            timestamp_ms=int(time.time() * 1000),
            duration_ms=int(time.time() * 1000) - started_ms,
        )

    async def _invoke_get_story(self, started_ms: int) -> ToolExecutionResult:
        self.get_story_calls += 1
        snapshot = self.state.to_dict()
        return ToolExecutionResult(
            tool_name="get_story",
            success=True,
            content=str(snapshot),
            structured_content=snapshot,
            timestamp_ms=int(time.time() * 1000),
            duration_ms=int(time.time() * 1000) - started_ms,
        )


__all__ = [
    "TextAdvToolProvider",
    "build_choose_option_spec",
    "build_get_story_spec",
]
