// v2 类型定义 — 对应后端新架构
// 历史事件名（input.*/decision.*/output.*）在 v2 已删除，请使用语义域事件名。

// ==================== 系统状态 ====================

export interface EventStats {
  total_emits: number;
  total_subscribers: number;
}

/**
 * 系统状态响应（v2.0）。
 *
 * W8 证据：`/api/v1/system/status` 当前返回 `input_phase/decision_phase/output_phase` 全为 `null`，
 * 因为 DashboardServer 的三个 Manager 未被 main.py 注入。前端应优雅降级：所有 phase 字段
 * 可选，缺失时视为"未启用"。
 */
export interface SystemStatusResponse {
  running: boolean;
  uptime_seconds: number;
  version: string;
  python_version: string;
  /** v2 已废弃（保留兼容）：始终为 null 或 undefined */
  input_phase?: PhaseStatus | null;
  decision_phase?: PhaseStatus | null;
  output_phase?: PhaseStatus | null;
}

export interface PhaseStatus {
  enabled: boolean;
  active_components: number;
  total_components: number;
  event_stats?: EventStats;
}

export interface SystemStatsResponse {
  total_messages: number;
  total_intents: number;
  event_bus_stats?: EventStats;
}

// ==================== 组件 ====================

/**
 * 组件摘要（v2）。
 *
 * 后端 `/api/v1/components` 当前返回 `{input: [], decision: [], output: []}`（W8 证据）；
 * 三个 Manager 未被注入，所以列表始终为空。v2 新架构使用 group 字段替代 phase：
  - `collectors`：采集器（原 input 阶段）
  - `agents`：业务 Agent（替代 decision 阶段语义）
  - `tools`：工具（替代 output 阶段语义）
 *
 * 前端保留 `phase` 字段以适配后端当前响应；视觉层重命名为"采集器 / Agent / 工具"。
 */
export interface ComponentSummary {
  name: string;
  /** @deprecated v2 已废弃，统一用 `group` */
  phase?: string;
  /** v2 新增：collectors / agents / tools */
  group?: string;
  type: string;
  is_started: boolean;
  is_enabled: boolean;
  /** v2 新增：组件所属种类（如 chat / expression / avatar / perception） */
  kind?: string;
}

export interface ComponentListResponse {
  /** v2 新增的分组字段（当前后端未填充，恒为空数组） */
  collectors?: ComponentSummary[];
  agents?: ComponentSummary[];
  tools?: ComponentSummary[];
  /** @deprecated v2 已废弃：保留兼容 */
  input?: ComponentSummary[];
  decision?: ComponentSummary[];
  output?: ComponentSummary[];
}

export interface ComponentDetail {
  name: string;
  phase?: string;
  group?: string;
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

// ==================== 配置 ====================

/**
 * 配置响应（v2）。
 *
 * 后端 `/api/v1/config` 当前返回 `{config: {...}}`，顶层是合并后的字典（来自 core / model +
 * agents / tools / memory / storage / background 七个 TOML 文件）。前端应通过 key 前缀
 * 路由到对应文件（见 Settings.vue 的 _SECTION_TO_FILE 推断）。
 */
export interface ConfigResponse {
  config: Record<string, unknown>;
}

// ==================== 调试注入 ====================

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

// ==================== v2 消息与会话 ====================

/**
 * v2 统一消息载荷。W8 后端实现：
 * - 弹幕/进房等所有消息统一发布为 `room.message.*`（采集器归一化后）
 * - Planner 的发言通过 `tool.result.<tool_name>` 与 `agenda.*` 间接观测
 * - 历史 EventRecord.type 字段字面量必须与后端一致
 *
 * payload 结构：`data.message.message_id` 是消息唯一 ID（LiveObserver 链路分组依据）。
 */
export interface NormalizedMessageData {
  text: string;
  source: string;
  data_type: string;
  importance: number;
  /** 毫秒时间戳（Unix epoch ms） */
  timestamp_ms: number;
  user_id?: string;
  user_nickname?: string;
  platform?: string;
  room_id?: string;
  raw?: Record<string, unknown>;
  /** 消息唯一 ID，用于链路分组（LiveObserver 用） */
  message_id?: string;
  /** v2 新增：模拟数据溯源（true=mock 采集器；统计查询必须排除） */
  simulated?: boolean;
}

export interface IntentEmotionData {
  name: string;
  intensity: number;
}

export interface IntentActionData {
  name: string;
  parameters: Record<string, unknown>;
}

export interface IntentMetadataData {
  source_id: string;
  decision_time_ms: number;
  source_message_id?: string;
}

/** v2 直播间观察页链路状态 */
export type LiveChainStatus = 'pending' | 'deciding' | 'done';

/**
 * v2 调试会话事件：保留消息事件（room.message.*）；决策/输出事件在 v2 中已被工具结果
 * 取代，前端通过 `room.message` + `agenda.*` + `tool.result.*` 三族观测链路。
 *
 * 旧类型 `message.received | decision.intent | output.render` 仅在兼容旧后端时使用；
 * 新后端不再发布这三个事件名。
 */
export type DebugSessionEventType =
  | 'room.message' // 统一消息事件（v2 默认）
  | 'agenda.speech' // Agenda 推动的发言（v2 新增）
  | 'tool.result' // 工具执行结果（v2 新增）
  | 'message.received' // @deprecated v2 已删除
  | 'decision.intent' // @deprecated v2 已删除
  | 'output.render'; // @deprecated v2 已删除

export interface DebugSessionEvent {
  id: string;
  type: DebugSessionEventType | string;
  timestamp: number;
  // message.* / room.message 事件专有字段
  message?: NormalizedMessageData;
  source?: string;
  // agenda.speech / tool.result 事件专有字段
  intent?: IntentEventData;
  deciderName?: string;
}

export interface IntentEventData {
  speech?: string;
  emotion?: IntentEmotionData;
  action?: IntentActionData;
  metadata: IntentMetadataData;
}

/** @deprecated 旧 ChatMessage（保留兼容，新代码应使用 DebugSessionEvent） */
export interface ChatMessage {
  id: string;
  type: 'normalized_message' | 'intent';
  sender: string;
  content: string;
  timestamp: number;
  emotion?: string;
  priority?: number;
}

// ==================== ContextService / 会话历史 ====================

export interface MessageItem {
  id: string;
  session_id: string;
  role: string;
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

// ==================== WebSocket ====================

/**
 * WebSocket 消息（v2）。
 *
 * `type` 字段统一是 v2 语义域事件名前缀。常见：
 * - `room.message.danmaku` / `room.message.gift` / `room.message.super_chat` / `room.message.enter`
 * - `agenda.*` / `planner.*` / `tool.result.*` / `game.*` / `live.*`
 */
export interface WebSocketMessage {
  type: string;
  timestamp: number;
  data: Record<string, unknown>;
  /** 事件唯一 ID（前端幂等去重依据） */
  id?: string;
}

export interface SubscribeRequest {
  action: 'subscribe' | 'unsubscribe';
  events: string[];
}

/** 历史事件记录（由后端通过 events.history 消息推送） */
export interface EventRecord {
  id: string;
  type: string;
  timestamp: number;
  level: 'info' | 'warn' | 'error';
  source: string;
  summary: string;
  data: Record<string, unknown>;
}

// ==================== Capabilities ====================

export type ParameterType = 'string' | 'number' | 'integer' | 'boolean';

export interface ParameterSpec {
  type: ParameterType;
  required: boolean;
  default?: unknown;
  description?: string;
  minimum?: number;
  maximum?: number;
}

/** v2 工具 action（替代旧 MaibotAction）。 */
export interface UnifiedActionEntry {
  name: string;
  description?: string;
  parameters: Record<string, ParameterSpec>;
}

export interface UnifiedCapabilitiesView {
  actions: UnifiedActionEntry[];
}

// ==================== Mock 采集器控制面 ====================

/**
 * Mock 采集器状态（v2 模拟直播间能力由 `modules/collectors/mock/` 承载）。
 *
 * W8 证据：dashboard 已删除 `/api/v1/simulator/*` 子路由。当前前端调用 503；
 * 类型保留以便后端将来补齐 mock 控制面接口。
 */
export interface MockCollectorStatus {
  is_running: boolean;
  started_at_ms: number;
  config_snapshot: Record<string, unknown>;
  /** 后端是否注册了 mock 采集器；若为 false，前端显示"mock 采集器未启用" */
  is_collector_available: boolean;
}

export interface MockCollectorStats {
  total_messages: number;
  simulated_count: number; // v2 新增：模拟消息计数（独立于真实消息）
  total_tokens: number;
  messages_by_type: Record<string, number>;
}

export interface MockCollectorPersona {
  user_id: string;
  user_nickname: string;
  role: string;
  personality: string;
  speaking_style: string;
  fans_medal_level: number;
  guard_level: number;
  messages_generated: number;
}

export interface MockPersonaUpdatePayload {
  user_nickname?: string;
  role?: string;
  personality?: string;
  speaking_style?: string;
  fans_medal_level?: number;
  guard_level?: number;
}

// ==================== 导出 settings / llm / trace 子模块 ====================

export * from './settings';
export * from './llm';
export * from './trace';
