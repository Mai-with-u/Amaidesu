"""LLM 诊断记录读侧：按 request_id 回查单条正文、读每模型最新水位快照。

本模块是 llm 域写入诊断记录（``llm_requests`` 追加流、``llm_context`` 快照）
的读取出口；文件定位一律经 ``DiagnosticsWriter`` 的版式出口（``stream_path``
/``read_snapshot``），写读两侧不各推一遍目录版式。

读模式与写侧形态对应：
- 单条正文：库行 ``timestamp_ms`` → 推导天+小时文件 → 逐行匹配 request_id
  （低频人工操作，整文件解析毫秒级，无需索引）
- 每模型最新水位：``llm_context/latest.json`` 快照直读

各函数接受装配期传入的 writer 实例（与写侧同一实例，目录根一致）；
writer 未装配（极简启动，诊断出库整体未启用）时返回空结果而非猜测目录。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from src.modules.diagnostics.writer import DiagnosticsWriter


def read_llm_request_payload(
    request_id: str,
    timestamp_ms: int,
    writer: Optional["DiagnosticsWriter"],
) -> Optional[Dict[str, Any]]:
    """按 request_id 回查单条请求正文（写入端 append 进 ``llm_requests`` 流的记录）。

    Args:
        request_id: 请求 ID（写入端行顶层键）
        timestamp_ms: 请求毫秒时刻（库统计行携带，用于推导天+小时文件）
        writer: 诊断落盘器（装配期与写侧同一实例）；未装配返回 None

    Returns:
        记录 dict；文件缺失或未命中返回 None
    """
    if writer is None or not request_id or timestamp_ms <= 0:
        return None
    path = writer.stream_path("llm_requests", timestamp_ms)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("request_id") == request_id:
                    return record
    except OSError:
        return None
    return None


def read_request_breakdown(
    request_id: str,
    timestamp_ms: int,
    writer: Optional["DiagnosticsWriter"],
) -> Optional[Dict[str, Any]]:
    """回查单次调用的上下文分段占用（正文记录行内的 ``breakdown`` 字段）。"""
    record = read_llm_request_payload(request_id, timestamp_ms, writer)
    if record is None:
        return None
    breakdown = record.get("breakdown")
    return breakdown if isinstance(breakdown, dict) else None


def read_latest_breakdowns(writer: Optional["DiagnosticsWriter"]) -> Dict[str, Dict[str, Any]]:
    """读取每模型最新上下文水位快照（``llm_context/latest.json``，写侧经 writer 覆写）。

    Args:
        writer: 诊断落盘器（装配期与写侧同一实例）；未装配返回空对象

    Returns:
        模型名 → breakdown dict；快照缺失或损坏返回空对象
    """
    if writer is None:
        return {}
    data = writer.read_snapshot("llm_context", "latest.json")
    models = data.get("models") if isinstance(data, dict) else None
    return {str(k): v for k, v in models.items() if isinstance(v, dict)} if isinstance(models, dict) else {}


__all__ = ["read_llm_request_payload", "read_request_breakdown", "read_latest_breakdowns"]
