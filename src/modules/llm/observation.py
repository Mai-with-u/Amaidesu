"""LLM 观测层 - 用量记账的费用计算

单一费用口径：按 ``model.toml`` ``[[llm_models]]`` 的定价字段
（price_in / price_out，每百万 token）把一次调用的 token 消耗折算为费用。
引擎装配期从同一来源构建价格表，经 ``calculate_cost`` 取价。

本模块是 ``llm_usage`` 与 ``llm_requests`` 两表在 llm 模块内的**唯一写入者**：
- ``record_usage``：只写聚合账（既有调用方兼容路径）
- ``record_request``：只写请求明细（请求历史链路经此落库）
- ``record_call``：两表同事务写入（原子记账，连接键由同一 request_id 保证）
调用方传 provider 归一化后的 usage 与已算费用，缓存列归零策略在此收敛。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.modules.storage.repos.llm import LLMRepo, LLMRequestInsert, LLMUsageInsert

__all__ = [
    "calculate_cost",
    "get_model_price",
    "record_call",
    "record_request",
    "record_usage",
]


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
    仅当价格表条目声明 ``cache`` 类型且 ``cache_price_in > 0`` 时启用分段
    口径——这是修复"缓存命中 token 按全价记账"的既有缺陷。OpenAI 风格
    单字段上报由 Client 层反推 miss 后传 ``cache_miss_tokens``，DeepSeek
    双字段直取两个值；调用方负责从 usage dict 取值传入。

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
    cache_type = price_config.get("cache", "") or ""
    use_segmented = bool(cache_type) and cache_price_in > 0
    if use_segmented:
        cost_in = (cache_hit_tokens / 1000000.0) * cache_price_in + (cache_miss_tokens / 1000000.0) * price_in
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


async def record_usage(
    repo: LLMRepo,
    *,
    model_name: str,
    provider_name: str,
    request_type: str,
    usage: Optional[Dict[str, Any]] = None,
    cost: float = 0.0,
    duration_ms: int = 0,
    profile_name: Optional[str] = None,
    request_id: Optional[str] = None,
    request: Optional[LLMRequestInsert] = None,
) -> int:
    """把一次成功调用的消耗写入 ``llm_usage`` 表。

    usage 为 provider 上报的中立字典；缓存字段（cache_hit_tokens /
    cache_miss_tokens）缺省或 None 视为 provider 未上报，按计划 v1 约定落 0。
    cost 由调用方按统一口径（``calculate_cost``）预先算好传入，本函数不再
    二次计价，保证费用行为只随计算口径一处变化。
    ``request`` 携带请求明细载荷时走两账同事务（``llm_usage`` +
    ``llm_requests`` 原子写入，连接键取 ``request.request_id``）；不携带时
    只写聚合账，``request_id`` 可选用于补连接键。
    """
    u = usage or {}
    hit = u.get("cache_hit_tokens")
    miss = u.get("cache_miss_tokens")
    reasoning = u.get("reasoning_tokens")
    usage_row = LLMUsageInsert(
        model_name=model_name,
        provider_name=provider_name,
        request_type=request_type,
        prompt_tokens=int(u.get("prompt_tokens", 0)),
        completion_tokens=int(u.get("completion_tokens", 0)),
        total_tokens=int(u.get("total_tokens", 0)),
        cache_hit_tokens=int(hit) if hit is not None else 0,
        cache_miss_tokens=int(miss) if miss is not None else 0,
        reasoning_tokens=int(reasoning) if reasoning is not None else 0,
        cost=float(cost),
        duration_ms=duration_ms,
        profile_name=profile_name,
        request_id=request.request_id if request is not None else request_id,
    )
    if request is not None:
        return await repo.insert_llm_call(usage=usage_row, request=request)
    return await repo.insert_llm_usage_row(usage_row)


async def record_request(repo: LLMRepo, row: LLMRequestInsert) -> bool:
    """把一条请求明细写入 ``llm_requests`` 表（``request_id`` 冲突时忽略）。"""
    return await repo.insert_llm_request(
        request_id=row.request_id,
        timestamp_ms=row.timestamp_ms,
        profile_name=row.profile_name,
        model_name=row.model_name,
        request_params_json=row.request_params_json,
        response_content=row.response_content,
        reasoning_content=row.reasoning_content,
        tool_calls_json=row.tool_calls_json,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
        cache_hit_tokens=row.cache_hit_tokens,
        cache_miss_tokens=row.cache_miss_tokens,
        reasoning_tokens=row.reasoning_tokens,
        cost=row.cost,
        success=row.success,
        error=row.error,
        latency_ms=row.latency_ms,
        usage_raw_json=row.usage_raw_json,
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
