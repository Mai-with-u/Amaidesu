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
}

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
