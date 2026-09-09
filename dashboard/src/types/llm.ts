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
  model_count: number;
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
  timestamp: number;
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
  model_stats: Record<string, { count: number; total_tokens: number; total_cost: number }>;
  client_stats: Record<string, number>;
  /** 统计窗口起止；全量统计时两端均为 null */
  time_range: { start: number | null; end: number | null };
}
