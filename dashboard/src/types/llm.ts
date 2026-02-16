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
