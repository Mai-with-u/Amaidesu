"""
W3 QA Scenario: ToolRegistry 失败兜底（§1.5）

本脚本直接执行 Acceptance Criteria 中描述的验证：
1. 创建一个无注册任何工具的 ToolRegistry 实例
2. invoke 一个不存在的工具名
3. 断言返回 ToolExecutionResult（成功=False，非异常）

输出写到 .omo/evidence/w3-tool-fallback.txt 供后续查阅。
"""

import asyncio
import os
from pathlib import Path

from src.modules.tools import ToolInvocation, ToolRegistry, ToolSpec
from src.modules.tools.models import (
    Kind,
    Provider,
    ToolExecutionResult,
)


async def main() -> None:
    evidence_path = Path(os.environ.get("EVIDENCE_PATH", ".omo/evidence/w3-tool-fallback.txt"))
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("W3 QA Scenario: ToolRegistry 失败兜底（未知工具不抛异常）")
    lines.append("=" * 70)
    lines.append("")

    # === 设置：空 ToolRegistry，只注册一个工具用作对照 ===
    registry = ToolRegistry()

    async def _known_impl(invocation: ToolInvocation) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_name="known_tool",
            success=True,
            content="known",
        )

    registry.register(
        ToolSpec(
            name="known_tool",
            description="已知工具用于对照",
            kind="sync",
            provider="builtin",
        ),
        _known_impl,
    )

    lines.append("[Setup] 注册一个已知工具 'known_tool'（builtin / sync）")
    lines.append(f"  当前工具数 = {len(registry)}")
    lines.append("")

    # === Step 1: invoke 不存在的工具（核心契约） ===
    lines.append("[Step 1] invoke 不存在的工具 'definitely_not_a_tool'")
    lines.append("-" * 70)
    unknown_inv = ToolInvocation(
        tool_name="definitely_not_a_tool",
        source="w3_qa_test",
    )
    lines.append(f"  ToolInvocation(tool_name={unknown_inv.tool_name!r}, source={unknown_inv.source!r})")
    lines.append("  调用 registry.invoke(unknown_inv)...")
    try:
        result = await registry.invoke(unknown_inv)
        lines.append("  [PASS] 未抛异常；registry.invoke 已正常 await 返回")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  [FAIL] 抛出异常: {type(exc).__name__}: {exc}")
        result = None
    lines.append("")

    # === Step 2: 断言返回 ToolExecutionResult ===
    lines.append("[Step 2] 断言返回 ToolExecutionResult（成功=False）")
    lines.append("-" * 70)
    if result is None:
        lines.append("  [FAIL] result 为 None（invoke 抛异常或未返回）")
    else:
        is_typed = isinstance(result, ToolExecutionResult)
        lines.append(f"  isinstance(result, ToolExecutionResult) = {is_typed}")
        lines.append(f"  result.tool_name     = {result.tool_name!r}")
        lines.append(f"  result.success       = {result.success}")
        lines.append(f"  result.error_message = {result.error_message!r}")
        lines.append(f"  result.content       = {result.content!r}")
        lines.append(f"  result.timestamp_ms  = {result.timestamp_ms}")
        lines.append("")
        lines.append(
            "  [PASS] 成功=False + error_message 非空（满足 §1.5 '未知工具返回失败 result，不抛异常'）"
            if (not result.success and result.error_message)
            else "  [FAIL] 失败 result 字段不完整"
        )

    lines.append("")

    # === Step 3: 对照：已知工具正常路径 ===
    lines.append("[Step 3] 对照：invoke 'known_tool' 走正常路径")
    lines.append("-" * 70)
    known_inv = ToolInvocation(tool_name="known_tool", source="w3_qa_test")
    known_result = await registry.invoke(known_inv)
    lines.append(f"  known_result.success = {known_result.success}")
    lines.append(f"  known_result.content = {known_result.content!r}")
    lines.append(
        "  [PASS] 已知工具正常返回成功 result"
        if (known_result.success and known_result.content == "known")
        else "  [FAIL] 已知工具路径异常"
    )
    lines.append("")

    # === Step 4: invoke_many 批量未知工具 ===
    lines.append("[Step 4] invoke_many 5 个未知工具名")
    lines.append("-" * 70)
    many_results = await registry.invoke_many(ToolInvocation(tool_name=f"unknown_{i}") for i in range(5))
    lines.append(f"  返回 {len(many_results)} 个 result")
    all_failed = all(not r.success and r.error_message for r in many_results)
    lines.append(f"  全部为失败 result（含 error_message）：{all_failed}")
    lines.append("  [PASS] 批量场景无异常" if all_failed else "  [FAIL] 部分 result 缺 error_message")
    lines.append("")

    # === Step 5: 实现抛异常兜底 ===
    lines.append("[Step 5] 实现异常 → 失败 result（不抛）")
    lines.append("-" * 70)

    async def _bad_impl(invocation: ToolInvocation) -> ToolExecutionResult:
        raise RuntimeError("内层实现异常")

    registry.register(
        ToolSpec(name="bad_tool", description="故意抛异常的 impl", kind="sync"),
        _bad_impl,
    )
    bad_result = await registry.invoke(ToolInvocation(tool_name="bad_tool"))
    lines.append(f"  bad_result.success       = {bad_result.success}")
    lines.append(f"  bad_result.error_message = {bad_result.error_message!r}")
    exc_isolated = not bad_result.success and "内层实现异常" in bad_result.error_message
    lines.append("  [PASS] 实现抛异常被兜底为失败 result" if exc_isolated else "  [FAIL] 实现异常未隔离")
    lines.append("")

    lines.append("=" * 70)
    summary_pass = result is not None and not result.success and known_result.success and all_failed and exc_isolated
    lines.append(f"综合结果：{'ALL PASS' if summary_pass else 'FAIL'}")
    lines.append("=" * 70)

    evidence = "\n".join(lines) + "\n"
    evidence_path.write_text(evidence, encoding="utf-8")
    print(evidence)
    print(f"[QA] wrote evidence to: {evidence_path}")


# 抑制 Pylance 对 Kind/Provider 未使用的提示（仅供类型文档展示）
_ = Kind
_ = Provider


if __name__ == "__main__":
    asyncio.run(main())
