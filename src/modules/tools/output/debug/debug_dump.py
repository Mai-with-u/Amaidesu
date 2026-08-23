"""
dump_intent - 调试用 Intent 打印函数（Wave 4 替换 DebugConsoleHandler）

迁移自 ``src.stages.output.handlers.debug_console.DebugConsoleHandler``：

- **删**：@handler 装饰器 / handle(intent) 接口 / OutputHandlerManager 派发
- **替换为**：``dump_intent(intent)`` 纯函数 + 可选 ``DebugConfig`` 控制开关
- 调用方直接 ``import dump_intent``，传 ``Intent`` 即可格式化打印

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- 字段遍历顺序 verbatim（speech / identity / source_context / emotion / action / metadata）
- ``DebugConfig`` 字段与原 handler.ConfigSchema 一一对应
- 配置可作为参数传入，不强制注入 ConfigService（避免依赖耦合）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.modules.types import Intent


@dataclass(slots=True)
class DebugConfig:
    """调试打印配置

    字段与旧 ``DebugConsoleHandler.ConfigSchema`` 一一对应。
    默认值与原 schema 保持一致。
    """

    print_source_context: bool = True
    print_actions: bool = True
    print_metadata: bool = False
    prefix: str = "[DEBUG]"


def dump_intent(intent: "Intent", config: DebugConfig | None = None) -> None:
    """格式化打印 Intent 到 stdout（对应旧 DebugConsoleHandler.handle()）

    Args:
        intent: 待打印的 Intent
        config: 调试配置（不传则使用默认 DebugConfig）
    """
    cfg = config or DebugConfig()

    print(f"\n{'=' * 60}")
    print(f"{cfg.prefix} Debug Console Output - Intent Received")
    print(f"{'=' * 60}")

    print("\n[Speech]")
    if intent.speech is not None:
        print(f"  {intent.speech}")
    else:
        print("  (no speech)")

    print("\n[Identity]")
    print(f"  Intent ID:  {intent.metadata.intent_id}")
    print(f"  Source ID:  {intent.metadata.source_id}")
    from src.modules.time_utils import ms_to_datetime

    decision_dt = ms_to_datetime(intent.metadata.decision_time_ms)
    print(f"  Decision:   {decision_dt} ({intent.metadata.decision_time_ms} ms)")

    if cfg.print_source_context:
        print("\n[Source Context]")
        print(f"  Source ID:       {intent.metadata.source_id}")
        print(f"  Source Msg ID:   {intent.metadata.source_message_id or 'N/A'}")

    if intent.emotion is not None:
        print("\n[Emotion]")
        print(f"  Name:      {intent.emotion.name}")
        print(f"  Intensity: {intent.emotion.intensity}")

    if cfg.print_actions and intent.action is not None:
        print("\n[Action]")
        print(f"  Name:       {intent.action.name}")
        if intent.action.parameters:
            print(f"  Parameters: {intent.action.parameters}")
        else:
            print("  Parameters: (none)")

    if cfg.print_metadata:
        print("\n[Metadata]")
        print(f"  source_id:         {intent.metadata.source_id}")
        print(f"  decision_time_ms:  {intent.metadata.decision_time_ms}")
        print(f"  source_message_id: {intent.metadata.source_message_id or 'N/A'}")
        print(f"  intent_id:         {intent.metadata.intent_id}")

    print(f"{'=' * 60}\n")


__all__ = ["dump_intent", "DebugConfig"]
