"""合并 Minecraft 工具的正文与结构化事实，避免把资源地址误当成已读内容。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult

logger = get_logger("MinecraftToolContent")


def _decode_text(text: str) -> Any:
    """JSON 文档恢复字段结构，Markdown 和其他文本保留原样供分页读取。"""
    if text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.debug("工具正文不是完整 JSON，保留原文而不猜测修补", exc=exc)
    return text


def failed_observation(result: ToolExecutionResult, name: str) -> dict[str, Any]:
    """SDK 将业务错误封装成异常文本时，恢复其中的原生诊断与结果确定性。"""
    details = result.structured_content
    if not isinstance(details, dict) and result.error_message.startswith("MCP 业务错误:"):
        details = _decode_text(result.error_message.split(":", 1)[1].strip())
    observation = deepcopy(details) if isinstance(details, dict) else {}
    observation.update(ok=False, tool=name)
    observation.setdefault("error", result.error_message or "工具执行失败")
    return observation


def successful_observation(result: ToolExecutionResult, arguments: dict[str, Any]) -> dict[str, Any]:
    """完整接收双通道回执后再交给观察呈现，正文只保存一份且保留来源。"""
    observation = deepcopy(result.structured_content) if isinstance(result.structured_content, dict) else {}
    texts = [block.text for block in result.blocks if block.kind == "text" and block.text.strip()]
    if not texts and result.content.strip():
        texts = [result.content]
    resources = observation.get("resources")
    reading = arguments.get("view") == "knowledge" and bool(arguments.get("resource_uri"))
    if reading and isinstance(resources, list):
        if len(texts) == len(resources) and texts and all(isinstance(item, dict) for item in resources):
            # MaiCraft 依同一顺序提供文档正文和 URI 元数据，逐份配对而不丢失多文档内容。
            observation["resources"] = [
                {**metadata, "content": _decode_text(text)} for metadata, text in zip(resources, texts, strict=True)
            ]
            observation["content_loaded"] = True
            return observation
        if not texts and not any(isinstance(item, dict) and "content" in item for item in resources):
            return {
                **observation,
                "ok": False,
                "content_loaded": False,
                "error": {
                    "code": "resource_body_missing",
                    "outcome_known": True,
                    "message": "资源读取只返回地址，未取得正文；这不是已读资料，不能靠重复读取同一历史观察补齐。",
                },
            }
    if texts:
        content = "\n".join(texts)
        decoded = _decode_text(content)
        # 普通状态工具常在两路重复返回同一 JSON；保留不同的说明，避免复制整份状态两次。
        if not observation and isinstance(decoded, dict):
            observation = decoded
        elif decoded != observation:
            observation["text"] = content
        if reading:
            observation["content_loaded"] = True
    return observation or {"ok": True}
