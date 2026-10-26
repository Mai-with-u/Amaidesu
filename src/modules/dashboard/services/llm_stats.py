"""LLM 用量统计与请求历史装配

用量趋势的日期对齐 / 缺失日期补零 / 逐日与按模型聚合、缓存命中率口径，
以及请求历史记录 → 响应模型的组装（列表预览、详情正文回查补齐）。
纯数据变换与仓储读取编排，不含 HTTP 语义。
"""

import json
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from src.modules.dashboard.schemas.llm import (
    LLMContextBreakdownResponse,
    LLMRequestHistoryListItem,
    LLMRequestHistoryResponse,
    LLMUsageTrendModelPoint,
    LLMUsageTrendPoint,
    LLMUsageTrendsResponse,
    TokenUsageSchema,
)
from src.modules.diagnostics.readers import read_llm_request_payload
from src.modules.llm.request_history_manager import count_image_parts


def build_context_breakdown_response(
    breakdown: Dict[str, Any],
    *,
    model_name: str,
    row: Dict[str, Any],
    context_windows: Dict[str, int],
) -> LLMContextBreakdownResponse:
    """把落库的 breakdown JSON + 仓储行组装成响应模型（两条上下文水位端点共用）。

    ``context_windows`` 是 LLMManager 装配期的每模型窗口索引（未注入/未配置
    传空 dict，窗口按 0 处理，前端据此隐藏水位）。
    """
    window = int(context_windows.get(model_name, 0))
    used = int(breakdown.get("api_prompt_tokens", 0) or 0)
    return LLMContextBreakdownResponse(
        model_name=model_name,
        profile_name=str(breakdown.get("profile_name") or row.get("profile_name") or ""),
        request_id=str(breakdown.get("request_id") or row.get("request_id") or ""),
        timestamp_ms=row.get("timestamp_ms"),
        context_window=window,
        api_prompt_tokens=used,
        calibrated=bool(breakdown.get("calibrated", False)),
        sections=breakdown.get("sections") or [],
        free_tokens=max(0, window - used) if window > 0 else None,
    )


def cache_hit_rate(cache_hit_tokens: float, cache_miss_tokens: float) -> Optional[float]:
    """由缓存命中/未命中 token 计算命中率（0-1）。

    两者同时为 0 视为上游从未上报缓存用量，返回 None 而非 0——
    落库口径把未上报记 0，0/0 无法与真实零命中区分，交给前端显示"未上报"。
    """
    total = cache_hit_tokens + cache_miss_tokens
    if total <= 0:
        return None
    return cache_hit_tokens / total


async def build_usage_trends(llm_repo: Optional[Any], days: int) -> LLMUsageTrendsResponse:
    """获取近 N 天逐日用量趋势（图表数据源；缺失日期补零对齐时间轴）。

    起点取窗口首日本地零点，与仓储按本地日分组的口径一致。
    """
    points: List[LLMUsageTrendPoint] = []
    model_points: List[LLMUsageTrendModelPoint] = []

    if llm_repo is not None:
        first_day = date.today() - timedelta(days=days - 1)
        start_ms = int(datetime.combine(first_day, time.min).timestamp() * 1000)
        trends = await llm_repo.llm_usage_daily_trends(start_ms=start_ms)

        daily_by_date = {row["day"]: row for row in trends.get("daily", [])}
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            day_str = day.isoformat()
            row = daily_by_date.get(day_str, {})
            hit = int(row.get("cache_hit_tokens", 0))
            miss = int(row.get("cache_miss_tokens", 0))
            points.append(
                LLMUsageTrendPoint(
                    date=day_str,
                    timestamp_ms=int(datetime.combine(day, time.min).timestamp() * 1000),
                    total_calls=int(row.get("total_calls", 0)),
                    prompt_tokens=int(row.get("prompt_tokens", 0)),
                    completion_tokens=int(row.get("completion_tokens", 0)),
                    total_tokens=int(row.get("total_tokens", 0)),
                    cost=float(row.get("cost", 0.0)),
                    cache_hit_tokens=hit,
                    cache_miss_tokens=miss,
                    cache_hit_rate=cache_hit_rate(hit, miss),
                )
            )

        for row in trends.get("by_model", []):
            hit = int(row.get("cache_hit_tokens", 0))
            miss = int(row.get("cache_miss_tokens", 0))
            model_points.append(
                LLMUsageTrendModelPoint(
                    date=str(row.get("day", "")),
                    model_name=str(row.get("model_name") or "unknown"),
                    total_calls=int(row.get("total_calls", 0)),
                    total_tokens=int(row.get("total_tokens", 0)),
                    cost=float(row.get("cost", 0.0)),
                    cache_hit_tokens=hit,
                    cache_miss_tokens=miss,
                    cache_hit_rate=cache_hit_rate(hit, miss),
                )
            )

    return LLMUsageTrendsResponse(days=days, points=points, model_points=model_points)


# 请求历史记录 → 响应模型组装


def merge_payload_from_file(record: Dict[str, Any]) -> Dict[str, Any]:
    """正文出库后按 request_id 回查诊断文件补齐详情记录。

    新行库列正文一律 NULL（正文在 ``logs/llm_requests/`` 诊断文件），按库行
    ``timestamp_ms`` 推导小时文件回查；存量旧行正文仍在库列（判空不触发），
    文件未命中时原样返回（前端拿到无正文的统计行）。无库模式（内存缓存）
    记录自带全量正文，同样不触发。
    """
    if record.get("request_params") or record.get("reasoning_content") or record.get("tool_calls"):
        return record
    payload = read_llm_request_payload(str(record.get("request_id") or ""), int(record.get("timestamp") or 0))
    if payload is None:
        return record
    merged = dict(record)
    merged["request_params"] = payload.get("request_params") or {}
    merged["reasoning_content"] = payload.get("reasoning_content")
    merged["tool_calls"] = payload.get("tool_calls") or []
    merged["usage_raw_json"] = payload.get("usage_raw_json")
    return merged


def _convert_usage(usage_data: Any) -> Optional[TokenUsageSchema]:
    """记录内的 usage 字典转 TokenUsage，缺省字段补 0"""
    if not usage_data:
        return None
    return TokenUsageSchema(
        prompt_tokens=usage_data.get("prompt_tokens", 0),
        completion_tokens=usage_data.get("completion_tokens", 0),
        total_tokens=usage_data.get("total_tokens", 0),
    )


def _extract_reasoning_tokens(usage_data: Any) -> Optional[int]:
    """从已解析的 usage 字典中取 reasoning_tokens；上游未上报则 None。"""
    if not isinstance(usage_data, dict):
        return None
    value = usage_data.get("reasoning_tokens")
    return int(value) if value is not None else None


def _preview(text: Any) -> str:
    """完整提供列表文本，避免列表接口丢失请求或响应尾部。"""
    if not isinstance(text, str):
        return ""
    return text.strip()


def _tool_calls_preview(tool_calls: Any) -> str:
    """工具调用型响应的列表预览：首个调用的「[调用 函数名] 参数摘要」。

    Planner/Replyer 的响应以 tool_calls 承载（response_content 为空），
    只看 response_content 会整列显示为空。
    """
    if not isinstance(tool_calls, list):
        return ""
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        func = call.get("function")
        if not isinstance(func, dict):
            continue
        name = str(func.get("name") or "").strip()
        args = func.get("arguments")
        if isinstance(args, str):
            args_text = args
        elif args is None:
            args_text = ""
        else:
            try:
                args_text = json.dumps(args, ensure_ascii=False)
            except TypeError:
                args_text = str(args)
        combined = f"[调用 {name}] {args_text}".strip()
        if combined != "[调用]" and combined:
            return _preview(combined)
    return ""


def _message_text(message: Any) -> str:
    """中立契约消息（{role, parts, ...}）的正文文本：拼接 parts 片段，图像片段以占位符呈现"""
    if not isinstance(message, dict) or not isinstance(message.get("parts"), list):
        return ""
    lines: List[str] = []
    for piece in message["parts"]:
        if isinstance(piece, str):
            lines.append(piece)
        elif isinstance(piece, dict):
            if isinstance(piece.get("text"), str):
                lines.append(piece["text"])
            elif piece.get("type") == "image":
                lines.append("[图片内容已省略]")
    return "\n".join(lines)


def _extract_image_count(record: Dict[str, Any]) -> int:
    """注入截图数：优先取写侧落库列；无库模式（内存缓存行自带正文）现场计数。"""
    stored = int(record.get("image_count") or 0)
    if stored:
        return stored
    return count_image_parts(record.get("request_params"))


def build_history_list_item(record: Dict[str, Any]) -> LLMRequestHistoryListItem:
    """列表行保留所选消息和首个工具调用的完整文本；详情接口提供完整请求。

    ``preview_text`` 列（v14 写侧摘要）优先；存量旧行无摘要，回退从
    request_params 解析——messages 里最后一条有正文的消息即列表展示的
    Prompt 预览。
    """
    prompt_preview = str(record.get("preview_text") or "")
    if not prompt_preview:
        params = record.get("request_params")
        messages = params.get("messages") if isinstance(params, dict) else None
        if isinstance(messages, list):
            for message in reversed(messages):
                text = _message_text(message).strip()
                if text:
                    prompt_preview = _preview(text)
                    break

    response_preview = _preview(record.get("response_content"))
    if not response_preview:
        response_preview = _tool_calls_preview(record.get("tool_calls"))
    return LLMRequestHistoryListItem(
        request_id=record.get("request_id", ""),
        timestamp_ms=record.get("timestamp", 0),
        profile_name=record.get("profile_name", ""),
        model_name=record.get("model_name", ""),
        prompt_preview=prompt_preview,
        response_preview=response_preview,
        usage=_convert_usage(record.get("usage")),
        cost=record.get("cost", 0.0),
        success=record.get("success", True),
        error=record.get("error"),
        latency_ms=record.get("latency_ms", 0),
        cache_hit_tokens=int(record.get("cache_hit_tokens") or 0),
        cache_miss_tokens=int(record.get("cache_miss_tokens") or 0),
        reasoning_tokens=_extract_reasoning_tokens(record.get("usage")),
        usage_raw_json=record.get("usage_raw_json"),
        image_count=_extract_image_count(record),
    )


def convert_record_to_response(record: Dict[str, Any]) -> LLMRequestHistoryResponse:
    """将请求记录字典转换为响应模型"""
    return LLMRequestHistoryResponse(
        request_id=record.get("request_id", ""),
        timestamp_ms=record.get("timestamp", 0),
        profile_name=record.get("profile_name", ""),
        model_name=record.get("model_name", ""),
        request_params=record.get("request_params", {}),
        response_content=record.get("response_content"),
        reasoning_content=record.get("reasoning_content"),
        tool_calls=record.get("tool_calls"),
        usage=_convert_usage(record.get("usage")),
        cost=record.get("cost", 0.0),
        success=record.get("success", True),
        error=record.get("error"),
        latency_ms=record.get("latency_ms", 0),
        cache_hit_tokens=int(record.get("cache_hit_tokens") or 0),
        cache_miss_tokens=int(record.get("cache_miss_tokens") or 0),
        reasoning_tokens=_extract_reasoning_tokens(record.get("usage")),
        usage_raw_json=record.get("usage_raw_json"),
    )
