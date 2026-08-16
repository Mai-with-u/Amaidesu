// System types
export interface EventStats {
  total_emits: number;
  total_subscribers: number;
}

export interface PhaseStatus {
  enabled: boolean;
  active_components: number;
  total_components: number;
  event_stats?: EventStats;
}

export interface SystemStatusResponse {
  running: boolean;
  uptime_seconds: number;
  version: string;
  python_version: string;
  input_phase?: PhaseStatus;
  decision_phase?: PhaseStatus;
  output_phase?: PhaseStatus;
}

export interface SystemStatsResponse {
  total_messages: number;
  total_intents: number;
  event_bus_stats?: EventStats;
}

// Component types
export interface ComponentSummary {
  name: string;
  phase: string;
  type: string;
  is_started: boolean;
  is_enabled: boolean;
}

export interface ComponentListResponse {
  input: ComponentSummary[];
  decision: ComponentSummary[];
  output: ComponentSummary[];
}

export interface ComponentDetail {
  name: string;
  phase: string;
  type: string;
  is_started: boolean;
  is_enabled: boolean;
  config?: Record<string, unknown>;
  stats?: Record<string, unknown>;
}

export type ComponentControlAction = 'start' | 'stop' | 'restart';

export interface ComponentControlRequest {
  action: ComponentControlAction;
}

export interface ComponentControlResponse {
  success: boolean;
  message: string;
}

// Config types
export interface ConfigResponse {
  general: Record<string, unknown>;
  pipelines: Record<string, unknown>;
  logging: Record<string, unknown>;
  context: Record<string, unknown>;
  dashboard: Record<string, unknown>;
}

// Debug types
export interface InjectMessageRequest {
  source?: string;
  text: string;
  data_type?: string;
  importance?: number;
}

export interface InjectMessageResponse {
  success: boolean;
  message_id?: string;
  error?: string;
}

export interface EventBusStatsResponse {
  total_events: number;
  total_subscribers: number;
  events_by_name: Record<string, number>;
}

export interface InjectIntentRequest {
  text?: string;
  responseText?: string;
  emotion?: string;
  actions?: Record<string, any>[];
  source?: string;
}

export interface InjectIntentResponse {
  success: boolean;
  intent_id?: string;
  error?: string;
}

// ============================================================
// 调试会话类型 — 完整保留 NormalizedMessage 和 Intent 的结构
// ============================================================

/** 标准化消息（来自 WebSocket message.received 事件） */
export interface NormalizedMessageData {
  text: string;
  source: string;
  data_type: string;
  importance: number;
  timestamp_ms: number;
  user_id?: string;
  user_nickname?: string;
  platform?: string;
  room_id?: string;
  raw?: Record<string, unknown>;
  /** 消息唯一 ID，用于链路分组（LiveObserver 用）；旧后端可能缺失 */
  message_id?: string;
}

/** Intent 情绪 */
export interface IntentEmotionData {
  name: string;
  intensity: number;
}

/** Intent 动作 */
export interface IntentActionData {
  name: string;
  parameters: Record<string, unknown>;
}

/** Intent 元数据 */
export interface IntentMetadataData {
  source_id: string;
  decision_time_ms: number;
  /** 触发本 Intent 的源消息 ID，用于链路分组（LiveObserver 用）；旧后端可能缺失 */
  source_message_id?: string;
}

/** 直播间观察页链路状态 */
export type LiveChainStatus = 'pending' | 'deciding' | 'done';

/** 决策意图（来自 WebSocket decision.intent / output.render 事件） */
export interface IntentEventData {
  speech?: string;
  emotion?: IntentEmotionData;
  action?: IntentActionData;
  metadata: IntentMetadataData;
}

/** 调试会话统一事件类型 */
export interface DebugSessionEvent {
  id: string;
  type: 'message.received' | 'decision.intent' | 'output.render';
  timestamp: number; // WebSocket 消息时间戳
  // message.received 事件专有字段
  message?: NormalizedMessageData;
  source?: string;
  // decision.intent / output.render 事件专有字段
  intent?: IntentEventData;
  deciderName?: string;
}

// 旧版 ChatMessage（保留兼容，但新代码应使用 DebugSessionEvent）
export interface ChatMessage {
  id: string;
  type: 'normalized_message' | 'intent';
  sender: string;
  content: string;
  timestamp: number;
  emotion?: string;
  priority?: number;
}

// Message types from ContextService
export interface MessageItem {
  id: string;
  session_id: string;
  role: string; // user/assistant/system
  content: string;
  timestamp: number;
  metadata?: Record<string, unknown>;
}

export interface MessageListResponse {
  messages: MessageItem[];
  has_more: boolean;
  next_cursor?: number;
  limit: number;
}

// WebSocket types
export interface WebSocketMessage {
  type: string;
  timestamp: number;
  data: Record<string, unknown>;
  /** 事件唯一 ID（与历史 EventRecord.id 同源，前端幂等去重依据；旧后端可能缺失） */
  id?: string;
}

export interface SubscribeRequest {
  action: 'subscribe' | 'unsubscribe';
  events: string[];
}

// 历史事件记录（由后端通过 events.history 消息推送）
export interface EventRecord {
  id: string;
  type: string;
  timestamp: number;
  level: 'info' | 'warn' | 'error';
  source: string;
  summary: string;
  data: Record<string, unknown>;
}

// Capabilities types (mapped from src/modules/types/capabilities.py)
export type ParameterType = 'string' | 'number' | 'integer' | 'boolean';

export interface ParameterSpec {
  type: ParameterType;
  required: boolean;
  default?: unknown;
  description?: string;
  minimum?: number;
  maximum?: number;
}

export interface UnifiedActionEntry {
  name: string; // 全限定名 <handler>.<action>
  description?: string;
  parameters: Record<string, ParameterSpec>;
}

export interface UnifiedCapabilitiesView {
  actions: UnifiedActionEntry[];
}

// Re-export settings types
export * from './settings';

// Re-export LLM types
export * from './llm';

// Re-export Trace types
export * from './trace';

// ============================================================
// 直播大纲 (Outline) 类型 — 对应 src/stages/decision/deciders/amaidesu/outline.py
// 任务 11: Dashboard 在线编辑页 (Task 13)
// ============================================================

/** 大纲分支（环节内可选跳转） */
export interface OutlineBranchData {
  branch_id: string;
  description: string;
  target_segment_id: string;
}

/** 环节的 AI 扩展内容（由 outline_loader 动态生成并缓存） */
export interface ExpandedSegmentData {
  opening_line: string;
  topic_guidance: string;
  talking_points: string[];
}

/** 大纲单个环节
 *
 * `expanded` 为可选：编辑页新建环节时不带该字段，运行态接口才会返回。 */
export interface OutlineSegmentData {
  id: string;
  title: string;
  task_description: string;
  duration_ms: number;
  min_duration_ms?: number | null;
  key_points: string[];
  branches: OutlineBranchData[];
  expanded?: ExpandedSegmentData | null;
}

/** `GET /api/v1/outline/segments` 响应 */
export interface OutlineSegmentsResponse {
  loaded: boolean;
  outline_id?: string | null;
  title?: string | null;
  fallback_segment_id?: string | null;
  path?: string | null;
  segments: OutlineSegmentData[];
}

/** `PUT /api/v1/outline/file` 请求体 */
export interface OutlineFileWriteRequest {
  path: string;
  content: string;
}

/** `PUT /api/v1/outline/file` 响应 */
export interface OutlineFileWriteResponse {
  status: string;
  path: string;
  bytes_written?: number;
  note?: string;
}

/** 大纲运行时状态枚举（对应 outline_state.py 的 `OutlineStatus`） */
export type OutlineRunStatus = 'inactive' | 'loading' | 'running' | 'completed' | 'unloaded';

/** 快照中的当前环节（含运行时计时与扩展就绪标记） */
export interface OutlineCurrentSegmentState {
  id: string;
  title: string;
  duration_ms: number;
  elapsed_ms: number;
  remaining_ms: number;
  expanded: boolean;
  needs_expansion: boolean;
}

/** 快照中的下一环节预览 */
export interface OutlineNextSegmentState {
  id: string;
  title: string;
}

/** `GET /api/v1/outline/state` 响应（对应 `OutlineState.get_snapshot()` 全字段） */
export interface OutlineStateSnapshot {
  status: OutlineRunStatus;
  current_segment: OutlineCurrentSegmentState | null;
  next_segment: OutlineNextSegmentState | null;
  completed_count: number;
  total_count: number;
  is_paused: boolean;
  elapsed_live_ms: number | null;
  total_planned_ms: number | null;
  progress_percent: number | null;
  outline_id: string | null;
  outline_title: string | null;
  manually_overridden: boolean;
}

/** 单条环节推进记录 */
export interface OutlineTransition {
  segment_id: string;
  title: string;
  reason: string;
  at_ms: number;
  stayed_ms: number | null;
}

/** `GET /api/v1/outline/transitions` 响应 */
export interface OutlineTransitionsResponse {
  loaded: boolean;
  transitions: OutlineTransition[];
}

/** 手动控制动作 */
export type OutlineControlAction = 'skip' | 'pause' | 'resume' | 'rewind' | 'jump';

/** `POST /api/v1/outline/control` 请求体 */
export interface OutlineControlRequest {
  action: OutlineControlAction;
  segment_id?: string;
}

/** `POST /api/v1/outline/control` 响应 */
export interface OutlineControlResponse {
  status: string;
  action: OutlineControlAction;
  segment_id?: string | null;
  current_segment_id?: string | null;
}

/** `POST /api/v1/outline/load` 响应 */
export interface OutlineLoadResponse {
  status: string;
  path: string;
  outline_id?: string | null;
}
