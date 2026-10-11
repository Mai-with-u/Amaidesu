"""诊断记录读侧：按 request_id 回查单条正文、读每模型最新水位快照。

读模式与写侧形态对应：
- 单条正文：库行 ``timestamp_ms`` → 推导天+小时文件 → 逐行匹配 request_id
  （低频人工操作，整文件解析毫秒级，无需索引）
- 每模型最新水位：``logs/llm_context/latest.json`` 快照直读
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

_DEFAULT_BASE_DIR = Path("logs")


def _base_dir(base_dir: Optional[Union[str, Path]]) -> Path:
    return Path(base_dir) if base_dir is not None else _DEFAULT_BASE_DIR


def read_llm_request_payload(
    request_id: str,
    timestamp_ms: int,
    base_dir: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, Any]]:
    """按 request_id 回查单条请求正文（写入端 append 进 ``llm_requests`` 流的记录）。

    Args:
        request_id: 请求 ID（写入端行顶层键）
        timestamp_ms: 请求毫秒时刻（库统计行携带，用于推导天+小时文件）
        base_dir: 落盘根目录；缺省用项目根 ``logs/``

    Returns:
        记录 dict；文件缺失或未命中返回 None
    """
    if not request_id or timestamp_ms <= 0:
        return None
    dt = datetime.fromtimestamp(timestamp_ms / 1000)
    path = _base_dir(base_dir) / "llm_requests" / dt.strftime("%Y-%m-%d") / f"{dt.strftime('%H')}.jsonl"
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
    base_dir: Optional[Union[str, Path]] = None,
) -> Optional[Dict[str, Any]]:
    """回查单次调用的上下文分段占用（正文记录行内的 ``breakdown`` 字段）。"""
    record = read_llm_request_payload(request_id, timestamp_ms, base_dir)
    if record is None:
        return None
    breakdown = record.get("breakdown")
    return breakdown if isinstance(breakdown, dict) else None


def read_latest_breakdowns(base_dir: Optional[Union[str, Path]] = None) -> Dict[str, Dict[str, Any]]:
    """读取每模型最新上下文水位快照（``logs/llm_context/latest.json``）。

    Returns:
        模型名 → breakdown dict；快照缺失或损坏返回空对象
    """
    path = _base_dir(base_dir) / "llm_context" / "latest.json"
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    return {str(k): v for k, v in models.items() if isinstance(v, dict)} if isinstance(models, dict) else {}


__all__ = ["read_llm_request_payload", "read_request_breakdown", "read_latest_breakdowns"]
