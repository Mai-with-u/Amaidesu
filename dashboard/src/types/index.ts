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
  text: string;
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

// Re-export settings types
export * from './settings';

// Re-export LLM types
export * from './llm';
