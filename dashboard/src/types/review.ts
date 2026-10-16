/**
 * 场次复盘页类型（GET /api/v1/review*，与后端 schemas/review.py 对应）
 */

/** 单 profile（或总体）的 LLM 用量聚合 */
export interface ReviewLLMDomainStats {
  profile_name: string;
  calls: number;
  failed_calls: number;
  slow_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  cache_hit_rate: number | null;
  cost: number;
  max_prompt_tokens: number;
}

/** 聚合范围内的单场基本信息 */
export interface ReviewSessionBrief {
  live_session_id: number;
  title: string | null;
  source: string;
  platform: string;
  started_at_ms: number;
  ended_at_ms: number | null;
  duration_ms: number;
  heat: number;
  viewer_count: number;
}

/** 决策→发言链路时延分位（口径：planner.decision 自带 total_duration_ms） */
export interface DecisionLatency {
  sample_count: number;
  p50_ms: number | null;
  p90_ms: number | null;
  max_ms: number | null;
}

export interface ReviewDecisions {
  total: number;
  distribution: Record<string, number>;
  silent_count: number;
  failed_count: number;
  latency: DecisionLatency;
}

export interface WaterlinePoint {
  timestamp_ms: number;
  prompt_tokens: number;
  session_id: number;
}

export interface WaterlineSeries {
  profile_name: string;
  points: WaterlinePoint[];
}

export interface SessionReviewResponse {
  sessions: ReviewSessionBrief[];
  window_start_ms: number;
  window_end_ms: number;
  chips: {
    danmaku_count: number;
    enter_count: number;
    gift_count: number;
    super_chat_count: number;
    gift_total_price: number;
    super_chat_total_price: number;
  };
  speech: {
    count: number;
    rate_per_min: number | null;
    longest_silence_ms: number | null;
  };
  decisions: ReviewDecisions;
  llm: {
    overall: ReviewLLMDomainStats;
    by_profile: ReviewLLMDomainStats[];
  };
  waterline: WaterlineSeries[];
  game: {
    total: number;
    by_type: Record<string, number>;
  };
  degraded: boolean;
}
