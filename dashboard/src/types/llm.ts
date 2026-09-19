/**
 * LLM 相关类型定义
 */

// 模型用量统计
export interface LLMUsageStats {
  model_name: string;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_calls: number;
  total_cost: number;
  /** 缓存命中 token；0 可能代表"未上报"而非真实零命中（落库口径把未上报记 0） */
  cache_hit_tokens: number;
  /** 缓存未命中 token；0 可能代表"未上报"而非真实零命中 */
  cache_miss_tokens: number;
  /** 缓存命中率（0-1，hit/(hit+miss)）；null 表示上游从未上报缓存用量 */
  cache_hit_rate: number | null;
  first_call_time: number | null;
  last_call_time: number | null;
  last_updated: number | null;
}

// 总费用摘要
export interface LLMUsageSummary {
  total_cost: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_calls: number;
  /** 缓存命中 token；0 可能代表"未上报"而非真实零命中 */
  cache_hit_tokens: number;
  /** 缓存未命中 token；0 可能代表"未上报"而非真实零命中 */
  cache_miss_tokens: number;
  /** 缓存命中率（0-1）；null 表示上游从未上报缓存用量 */
  cache_hit_rate: number | null;
  model_count: number;
}

// 用量趋势：单日聚合点（后端已补零对齐连续时间轴）
export interface LLMUsageTrendPoint {
  /** 本地日期 YYYY-MM-DD */
  date: string;
  /** 当日本地零点的毫秒时间戳 */
  timestamp_ms: number;
  total_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  /** 缓存命中率（0-1）；null 表示当日无缓存用量上报 */
  cache_hit_rate: number | null;
}

// 用量趋势：单日单模型聚合点（仅有数据的日子）
export interface LLMUsageTrendModelPoint {
  date: string;
  model_name: string;
  total_calls: number;
  total_tokens: number;
  cost: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  cache_hit_rate: number | null;
}

// 用量趋势响应（GET /llm/usage/trends?days=N）
export interface LLMUsageTrendsResponse {
  days: number;
  points: LLMUsageTrendPoint[];
  model_points: LLMUsageTrendModelPoint[];
}

// Token 用量详情
export interface LLMTokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

// 请求历史记录
export interface LLMRequestHistory {
  request_id: string;
  /** 请求时刻（Unix 毫秒） */
  timestamp_ms: number;
  client_type: string;
  model_name: string;
  request_params: Record<string, unknown>;
  response_content: string | null;
  reasoning_content: string | null;
  tool_calls: Array<Record<string, unknown>> | null;
  usage: LLMTokenUsage | null;
  cost: number;
  success: boolean;
  error: string | null;
  latency_ms: number;
}

// 历史查询参数
export interface LLMHistoryQueryParams {
  page?: number;
  page_size?: number;
  model_name?: string;
  client_type?: string;
  start_time?: number;
  end_time?: number;
  success_only?: boolean;
}

// 历史列表响应
export interface LLMHistoryResponse {
  items: LLMRequestHistory[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/**
 * LLM 用量聚合统计（GET /api/v1/llm/history/statistics）。
 *
 * 可按时间窗口过滤：start_time/end_time 为 Unix epoch 毫秒（缺省 = 全量）。
 * 用于首页"今日成本"卡片——只统计当天 00:00 本地以来的调用与花费。
 */
export interface LLMHistoryStatistics {
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  /** 0-1（成功请求 / 总请求）；首页渲染为百分比 */
  success_rate: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_cost: number;
  avg_latency_ms: number;
  model_stats: Record<
    string,
    {
      count: number;
      total_tokens: number;
      total_cost: number;
      /** 0 可能代表"未上报"而非真实零命中（落库口径把未上报记 0） */
      cache_hit_tokens: number;
      cache_miss_tokens: number;
      /** 缓存命中率（0-1）；null 表示窗口内无缓存用量上报 */
      cache_hit_rate: number | null;
    }
  >;
  /** 缓存命中 token 总量；0 可能代表"未上报"而非真实零命中 */
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  /** 缓存命中率（0-1）；null 表示窗口内无缓存用量上报 */
  cache_hit_rate: number | null;
  client_stats: Record<string, number>;
  /** 统计窗口起止；全量统计时两端均为 null */
  time_range: { start: number | null; end: number | null };
}
