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
  /** 本场累计观看人次（UV 口径；无推送的场次为 0） */
  audience_total: number;
  /** 收播总结全文（未生成/功能前老场次为 null） */
  recap_text: string | null;
  /** 收播总结的生成时刻（Unix 毫秒） */
  recap_created_at_ms: number | null;
}

/** 决策→发言链路时延分位（口径：planner.verdict → streamer.speech 按 round_id 配对） */
export interface DecisionLatency {
  sample_count: number;
  p50_ms: number | null;
  p90_ms: number | null;
  max_ms: number | null;
  /** 最慢一轮的开口时刻（供回看定位到时刻） */
  max_at_ms: number | null;
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

/** 单条游戏事件（复盘页明细列表） */
export interface ReviewGameEventItem {
  game: string;
  event_type: string;
  message: string;
  timestamp_ms: number;
}

/** 场内观众侧聚合（单场视图；多选合并视图为 null） */
export interface ReviewViewers {
  /** 场内发过言的观众数（去重） */
  active_total: number;
  /** 新观众数（历史首条发言落在本场窗口内） */
  new_viewers: number;
  /** 回头观众数（场内发言但历史首条发言早于本场） */
  returning_viewers: number;
  top_speakers: Array<{
    /** 内部观众 ID（同一位观众换了数据源也只算一人） */
    viewer_id: number;
    platform: string;
    user_name: string;
    message_count: number;
  }>;
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
    /** 付费礼物折算人民币合计（免费币种不计入；无可折算金额为 null） */
    gift_total_cny: number | null;
    /** SC 金额折算人民币合计（无可折算金额为 null） */
    super_chat_total_cny: number | null;
  };
  speech: {
    count: number;
    rate_per_min: number | null;
    longest_silence_ms: number | null;
    /** 最长沉默区间起点（=沉默前最后一条发言；零/单点发言外的锚定跳转目标） */
    longest_silence_start_ms: number | null;
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
    /** 明细列表（单场视图填充，时间正序；合并视图为空） */
    items: ReviewGameEventItem[];
    /** 明细超出返回上限被截断 */
    truncated: boolean;
  };
  /** 观众侧聚合（单场视图填充；多选合并视图为 null） */
  viewers: ReviewViewers | null;
  degraded: boolean;
}
