/**
 * Trace 全链路追踪类型定义（v2.0）
 *
 * 对应后端 `/api/v1/traces` 接口返回的数据结构。
 *
 * v2 行为流（参考 `.omo/evidence/w8-api-contract.txt`）：
 * - 链路起点：采集器发布 `room.message.*` 事件，`EventRecord.data.message.message_id` 关联
 * - 链路上游：Planner 通过 `tool.result.*` 与 `agenda.*` 间接观测（不再写 decision.intent/output.render）
 * - 后端 `traces.py:107` 搜索策略：仅匹配 `room.message` 事件 + message_id 聚合
 *
 * v2 trace payload 仅返回 room.message 事件本体，不包含 decision/output 字段
 * （W8 evidence §3：当前 `_build_trace` 仅构造 `trace.message` + `trace.event`）。
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

/** v2 暂无 decision/output 字段；保留兼容旧后端，运行时为 null/undefined。 */
export interface TraceStageTiming {
  timestamp: number;
  elapsed_ms: number;
}

export interface TraceDecision extends TraceStageTiming {
  decider: string;
  speech: string;
  emotion?: TraceEmotion | null;
  action?: TraceAction | null;
}

export interface TraceEmotion {
  name: string;
  intensity: number;
}

export interface TraceAction {
  name: string;
  parameters: Record<string, unknown>;
}

export interface TraceOutput extends TraceStageTiming {
  handler: string;
  speech: string;
  action?: TraceAction | null;
}

/**
 * 单条消息的完整链路追踪（v2）。
 *
 * 必填：`message_id` + `message` + `event`。
 * 可选：`decision`/`outputs`/`total_elapsed_ms` —— 后端 v2 简化实现可能不返回，
 * 渲染时需要兜底（如 v2 当前仅返回 message + event）。
 */
export interface Trace {
  message_id: string;
  message: TraceMessage;
  /** 触发消息的原始事件记录（v2 新增） */
  event?: {
    name: string;
    timestamp: number;
  };
  /** v2 暂无；旧后端兼容保留 */
  decision?: TraceDecision | null;
  /** v2 暂无；旧后端兼容保留 */
  outputs?: TraceOutput[];
  /** v2 暂无；旧后端兼容保留 */
  total_elapsed_ms?: number | null;
  /** v2 新增：消息是否来自 mock 采集器（统计查询应排除） */
  simulated?: boolean;
}

export interface TraceListResponse {
  traces: Trace[];
  total: number;
}

export interface TraceDetailResponse {
  trace: Trace | null;
  error?: string;
}
