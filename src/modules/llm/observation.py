"""LLM 观测层 - 一次调用的出账单一入口与费用计算

单一费用口径：按 ``model.toml`` ``[[llm_models]]`` 的定价字段
（price_in / price_out，每百万 token）把一次调用的 token 消耗折算为费用。
引擎装配期从同一来源构建价格表，经 ``calculate_cost`` 取价。

本模块是 ``llm_usage`` 与 ``llm_requests`` 两表在 llm 模块内的**唯一写入者**：
- ``record_call_outcome``：一次调用（成功或失败）的出账单一入口——内部完成
  费用计算、列表摘要提取、两表同事务写入；失败行（success=False）与无
  usage 的成功行只写请求明细，走同一入口
- ``record_call``：两表同事务写入的低层面（存储层迁移测试直接消费）
调用方传 provider 归一化后的 usage 与请求参数快照，缓存列归零策略在此收敛。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.modules.storage.repos.llm import LLMRepo, LLMRequestInsert, LLMUsageInsert
from src.modules.utils.time_utils import now_ms

__all__ = [
    "build_prompt_preview",
    "calculate_cost",
    "count_image_parts",
    "get_model_price",
    "record_call",
    "record_call_outcome",
]


# 列表预览摘要长度（字符）：统计行的"一眼认出"口径，避免摘要列自身膨胀
_PREVIEW_MAX_CHARS = 50


def build_prompt_preview(request_params: Any) -> str:
    """从请求参数提取列表页 Prompt 预览摘要。

    取 ``messages`` 里最后一条有正文的消息（与触发语境最接近），兼容两种
    消息形态：OpenAI 原生（``content`` 字符串或多模态列表）与中立 payload
    契约（``parts`` 片段列表，图像片段以占位符呈现）。截取固定长度——正文
    已出库到诊断文件，摘要列只承担列表页辨识。
    """
    messages = request_params.get("messages") if isinstance(request_params, dict) else None
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        text = _message_body_text(message).strip()
        if text:
            return text[:_PREVIEW_MAX_CHARS]
    return ""


def _message_body_text(message: Any) -> str:
    """单条消息的正文文本（OpenAI content 形态与中立 parts 形态通吃）。"""
    if not isinstance(message, dict):
        return ""
    lines: List[str] = []
    content = message.get("content")
    if isinstance(content, str):
        lines.append(content)
    elif isinstance(content, list):
        for piece in content:
            if isinstance(piece, str):
                lines.append(piece)
            elif isinstance(piece, dict) and piece.get("type") == "text" and isinstance(piece.get("text"), str):
                lines.append(piece["text"])
    parts = message.get("parts")
    if isinstance(parts, list):
        for piece in parts:
            if isinstance(piece, str):
                lines.append(piece)
            elif isinstance(piece, dict):
                if isinstance(piece.get("text"), str):
                    lines.append(piece["text"])
                elif piece.get("type") == "image":
                    lines.append("[图片内容已省略]")
    return "\n".join(line for line in lines if line)


def count_image_parts(request_params: Any) -> int:
    """统计请求消息里的图像片段数量（中立契约 ``{type:"image"}`` part）。

    正文大字段已出库到诊断文件，列表行无消息体可查，计数必须在写侧随行
    落库（WebUI 历史列表"本次注入 N 张截图"徽标的数据源）。非列表形态的
    输入一律计 0。
    """
    messages = request_params.get("messages") if isinstance(request_params, dict) else None
    if not isinstance(messages, list):
        return 0
    count = 0
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("parts"), list):
            continue
        for piece in message["parts"]:
            if isinstance(piece, dict) and piece.get("type") == "image":
                count += 1
    return count


def get_model_price(model_prices: Dict[str, Dict[str, float]], model_name: str) -> Optional[Dict[str, float]]:
    """按模型标识精确匹配价格配置（不做模糊匹配——误匹配会算错费用）

    Args:
        model_prices: 价格表（``{model_identifier: {price_in, price_out, ...}}``）
        model_name: 模型标识（[[llm_models]].model_identifier）

    Returns:
        价格配置字典，未登记则返回 None
    """
    return model_prices.get(model_name)


def calculate_cost(
    model_prices: Dict[str, Dict[str, float]],
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
) -> Dict[str, Any]:
    """计算 token 使用费用。

    缓存命中按 ``cache_price_in`` 单独计费，未命中按 ``price_in`` 计费；
    仅当价格表条目声明分段口径（``cache != "none"``）且 ``cache_price_in > 0``
    时启用——显式双条件防呆，缺一即整单退回全价，不会静默半生效。
    分段模式下端点未上报缓存字段时未命中按 prompt − hit 反推兜底
    （退化为全价，不会按 0 记账）；OpenAI 风格单字段上报由 Client 层
    反推 miss 后传 ``cache_miss_tokens``，DeepSeek 双字段直取两个值；
    调用方负责从 usage dict 取值传入。

    Args:
        model_prices: 价格表（``{model_identifier: {price_in, price_out, ...}}``）
        model_name: 模型名称
        prompt_tokens: 输入token数量
        completion_tokens: 输出token数量
        cache_hit_tokens: 缓存命中输入 token 数量（未上报按 0）
        cache_miss_tokens: 缓存未命中输入 token 数量（未上报按 0）

    Returns:
        费用计算信息字典
    """
    price_config = get_model_price(model_prices, model_name)

    if not price_config:
        return {
            "has_price": False,
            "cost": 0.0,
            "cost_usd": 0.0,
            "price_in": 0.0,
            "price_out": 0.0,
            "message": f"模型 {model_name} 未找到价格配置",
        }

    # 价格单位：每1000000个token的价格（通常是美元）
    price_in = price_config.get("price_in", 0.0)
    price_out = price_config.get("price_out", 0.0)
    cache_price_in = float(price_config.get("cache_price_in", 0.0) or 0.0)
    cache_mode = str(price_config.get("cache", "none") or "none")
    if cache_mode != "none" and cache_price_in > 0:
        # 分段口径：命中走缓存价、未命中走全价；端点未上报缓存字段时未命中
        # 按 prompt − hit 反推兜底（上报数据不收缩，只向上补齐）
        miss_tokens = cache_miss_tokens
        if cache_hit_tokens + miss_tokens < prompt_tokens:
            miss_tokens = prompt_tokens - cache_hit_tokens
        cost_in = (cache_hit_tokens / 1000000.0) * cache_price_in + (miss_tokens / 1000000.0) * price_in
    else:
        cost_in = (prompt_tokens / 1000000.0) * price_in
    cost_out = (completion_tokens / 1000000.0) * price_out
    total_cost = cost_in + cost_out

    return {
        "has_price": True,
        "cost": total_cost,
        "cost_usd": total_cost,  # 假设价格单位为美元
        "price_in": price_in,
        "price_out": price_out,
        "cost_in": cost_in,
        "cost_out": cost_out,
        "message": "费用计算成功",
    }


def _cost_of(
    model_prices: Dict[str, Dict[str, float]],
    model_name: str,
    usage: Dict[str, Any],
) -> float:
    """按统一口径算一次调用的费用（usage 为 provider 归一化字典）。"""
    if not usage:
        return 0.0
    cost_info = calculate_cost(
        model_prices,
        model_name,
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
        cache_hit_tokens=int(usage.get("cache_hit_tokens") or 0),
        cache_miss_tokens=int(usage.get("cache_miss_tokens") or 0),
    )
    return float(cost_info.get("cost", 0.0))


async def record_call_outcome(
    repo: LLMRepo,
    *,
    request_id: str,
    profile_name: str,
    model_name: str,
    request_type: str,
    provider_name: str = "unknown",
    model_prices: Optional[Dict[str, Dict[str, float]]] = None,
    success: bool,
    error: Optional[str] = None,
    response_content: Optional[str] = None,
    latency_ms: int = 0,
    timestamp_ms: Optional[int] = None,
    request_params: Optional[Dict[str, Any]] = None,
    usage: Optional[Dict[str, Any]] = None,
    live_session_id: Optional[int] = None,
) -> None:
    """一次调用（成功或失败）的出账单一入口，``llm_requests`` 一调用一行。

    内部完成：费用计算（``calculate_cost`` 统一口径，成功与失败同价）、
    列表预览摘要与图像计数提取、落库。成功且带 usage 时聚合账
    （``llm_usage``）与请求明细在单个 SQLite 事务内原子写入；失败行
    （success=False）与未上报 usage 的成功调用只写请求明细——``llm_usage``
    只记产生 token 消耗的成功调用。任何异常向调用方传播，由调用方按
    "记账旁路"语义吸收（告警、不阻断调用链）。

    Args:
        repo: 存储仓储
        request_id: 请求标识（两表连接键）
        profile_name: LLM 用途 profile 名（planner / replyer / ...）
        model_name: 实际调用的 API 模型标识（费用计算键）
        request_type: 调用入口（generate / generate_vision / generate_audio）
        provider_name: provider 注册名（聚合账归属；未知填 unknown）
        model_prices: 价格表（{model_identifier: {price_in, price_out, ...}}）
        success: 本次调用是否成功（失败行 success=False，同路径落库）
        error: 失败原因（成功为 None）
        response_content: 响应正文（库行存档；请求参数正文出库到诊断文件）
        latency_ms: 调用耗时（毫秒）
        timestamp_ms: 请求时刻（毫秒；None 取当前时刻）
        request_params: 请求参数快照（用于摘要提取，本身不落库）
        usage: provider 归一化 usage 字典（未上报传 None）
        live_session_id: 直播场次主键（None = 场间消耗）
    """
    params = request_params or {}
    u = usage or {}
    hit = u.get("cache_hit_tokens")
    miss = u.get("cache_miss_tokens")
    cost = _cost_of(model_prices or {}, model_name, u)
    request_row = LLMRequestInsert(
        request_id=request_id,
        timestamp_ms=timestamp_ms if timestamp_ms is not None else now_ms(),
        profile_name=profile_name,
        model_name=model_name,
        response_content=response_content,
        prompt_tokens=int(u.get("prompt_tokens", 0)),
        completion_tokens=int(u.get("completion_tokens", 0)),
        total_tokens=int(u.get("total_tokens", 0)),
        cache_hit_tokens=int(hit) if hit is not None else 0,
        cache_miss_tokens=int(miss) if miss is not None else 0,
        reasoning_tokens=int(u.get("reasoning_tokens") or 0),
        cost=cost,
        success=success,
        error=error,
        latency_ms=latency_ms,
        preview_text=build_prompt_preview(params),
        image_count=count_image_parts(params),
    )
    if success and u:
        usage_row = LLMUsageInsert(
            model_name=model_name,
            provider_name=provider_name,
            request_type=request_type,
            prompt_tokens=int(u.get("prompt_tokens", 0)),
            completion_tokens=int(u.get("completion_tokens", 0)),
            total_tokens=int(u.get("total_tokens", 0)),
            cache_hit_tokens=int(hit) if hit is not None else 0,
            cache_miss_tokens=int(miss) if miss is not None else 0,
            reasoning_tokens=int(u.get("reasoning_tokens") or 0),
            cost=cost,
            duration_ms=latency_ms,
            profile_name=profile_name,
            live_session_id=live_session_id,
            request_id=request_id,
        )
        await repo.insert_llm_call(usage=usage_row, request=request_row)
    else:
        await repo.insert_llm_request(
            request_id=request_row.request_id,
            timestamp_ms=request_row.timestamp_ms,
            profile_name=request_row.profile_name,
            model_name=request_row.model_name,
            response_content=request_row.response_content,
            prompt_tokens=request_row.prompt_tokens,
            completion_tokens=request_row.completion_tokens,
            total_tokens=request_row.total_tokens,
            cache_hit_tokens=request_row.cache_hit_tokens,
            cache_miss_tokens=request_row.cache_miss_tokens,
            reasoning_tokens=request_row.reasoning_tokens,
            cost=request_row.cost,
            success=request_row.success,
            error=request_row.error,
            latency_ms=request_row.latency_ms,
            preview_text=request_row.preview_text,
            image_count=request_row.image_count,
        )


async def record_call(
    repo: LLMRepo,
    *,
    usage: LLMUsageInsert,
    request: LLMRequestInsert,
) -> int:
    """一次调用的两账同事务落库（``llm_usage`` + ``llm_requests`` 原子写入）。

    两载荷应填同一个 ``request_id`` 构成连接键；第二步写入失败整体回滚，
    两表均不产生新行。返回 usage 行 rowid。
    """
    return await repo.insert_llm_call(usage=usage, request=request)
