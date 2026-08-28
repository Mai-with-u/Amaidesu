/**
 * Trace 全链路追踪类型定义（v2.0）
 *
 * 对应后端 `/api/v1/traces` 与 `/api/v1/traces/{message_id}` 接口返回的数据结构。
 *
 * v2 行为流：
 * - 链路起点：采集器发布 `room.message.*` 事件，`EventRecord.data.message.message_id` 关联
 * - 链路上游：Planner 通过 `planner.checkpoint` 事件观测决策
 * - 链路下游：`tool.result.*` + `agenda.*` 间接观测执行
 *
 * 后端 `/traces/{message_id}` 聚合三段：
 * - `messages`：触发链路的所有 room.message 事件（数组，必填）
 * - `planning`：planner.checkpoint 事件数组（可为空）
 * - `execution`：tool.result.* + agenda.* 事件数组（可为空）
 *
 * 旧字段 `decision/outputs/total_elapsed_ms` 已删除（统一通过 `segments` 三段 + 时间戳推算耗时）。
 */

export interface TraceMessage {
  text: string;
  source: string;
  data_type: string;
  /** 毫秒时间戳（Unix epoch ms） */
  timestamp_ms: number;
  user_id?: string | null;
  user_nickname?: string | null;
}

/** trace segments 中的单条事件片段（消息/决策/执行三段共用） */
export interface TraceSegmentEntry {
  type: string;
  /** 毫秒时间戳（Unix epoch ms） */
  timestamp_ms: number;
  data: Record<string, unknown>;
}

/**
 * Trace 聚合响应（v2）。
 *
 * 必填：`message_id` + `segments.messages`。
 * 可选：`segments.planning` / `segments.execution` —— 后端实现可能为空数组。
 */
export interface Trace {
  message_id: string;
  /** 是否来自 mock 采集器（统计查询应排除） */
  simulated?: boolean;
  /** v2 实现可能不再计算总耗时（前端可自行推算）；保留字段以备后端补回 */
  total_elapsed_ms?: number | null;
  segments: {
    messages: TraceSegmentEntry[];
    planning: TraceSegmentEntry[];
    execution: TraceSegmentEntry[];
  };
}

export interface TraceListResponse {
  traces: Trace[];
  total: number;
}

export interface TraceDetailResponse {
  trace: Trace | null;
  error?: string;
}
