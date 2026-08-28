// v2 类型定义 — 对应后端新架构
// 历史事件名（input.*/decision.*/output.*）在 v2 已删除，请使用语义域事件名。

// ==================== 系统状态 ====================

/**
 * 单个组件分组（collectors / agents / tools）的运行统计。
 *
 * 后端 `/api/v1/system/status` 中的 `groups.<name>` 字段：组件注册表派生。
 */
export interface GroupStatus {
  enabled: boolean;
  /** 已启动（运行时状态） */
  started: number;
  /** 已启用（配置）总数 */
  total: number;
}

/**
 * EventBus 总吞吐统计（顶层暴露，不再走 phase）。
 */
export interface EventBusStats {
  total_events: number;
}

/**
 * 系统状态响应（v2.0）。
 *
 * 字段说明（与后端 `/api/v1/system/status` 对齐）：
 * - `running` / `uptime_seconds` / `version` / `python_version`：运行时元信息
 * - `groups`：三组组件运行统计（collectors / agents / tools）
 * - `event_bus`：EventBus 全局吞吐
 *
 * 旧 input/decision/output 三阶段字段已删除，前端读 `groups.<name>`。
 */
export interface SystemStatusResponse {
  running: boolean;
  uptime_seconds: number;
  version: string;
  python_version: string;
  groups: {
    collectors: GroupStatus;
    agents: GroupStatus;
    tools: GroupStatus;
  };
  event_bus: EventBusStats;
}

// ==================== 组件 ====================

/**
 * 组件摘要（v2）。
 *
 * 字段：
 * - `name`：组件名（控制路径用）
 * - `group`：v2 唯一分组键 `collectors | agents | tools`
 * - `type`：组件类名或类型标识
 * - `kind`：组件语义种类（如 chat / expression / avatar / perception）
 * - `description`：人类可读描述（来自 ConfigSchema，缺失时为空串）
 * - `is_started`：当前是否已启动（运行时状态）
 * - `is_enabled`：是否在配置中启用
 */
export interface ComponentSummary {
  name: string;
  group: 'collectors' | 'agents' | 'tools';
  type: string;
  kind?: string;
  description?: string;
  is_started: boolean;
  is_enabled: boolean;
}

export interface ComponentListResponse {
  collectors: ComponentSummary[];
  agents: ComponentSummary[];
  tools: ComponentSummary[];
}

export interface ComponentDetail {
  name: string;
  group: 'collectors' | 'agents' | 'tools';
  type: string;
  description?: string;
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
 * 后端 `/api/v1/config` 返回 7 文件合并的扁平 dict（core / model / agents / tools /
 * memory / storage / background）。
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
 * v2 统一消息载荷。后端实现：
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
export type LiveChainStatus = 'pending' | 'planning' | 'done';

/**
 * v2 调试会话事件：仅保留语义域家族。
 *
 * 旧类型 `message.received | decision.intent | output.render` 已删除（v2 后端不再发布）；
 * 新链路通过 `room.message.*` + `agenda.*` + `tool.result.*` 三族观测。
 */
export type DebugSessionEventType =
  | 'room.message' // 统一消息事件（v2 默认）
  | 'agenda.update' // Agenda 节目单更新（v2）
  | 'agenda.speech' // Agenda 推动的发言（v2）
  | 'planner.checkpoint' // Planner 决策检查点（v2）
  | 'tool.result'; // 工具执行结果（v2）

export interface DebugSessionEvent {
  id: string;
  type: DebugSessionEventType | string;
  timestamp: number;
  // room.message 事件专有字段
  message?: NormalizedMessageData;
  source?: string;
  // agenda / planner / tool.result 事件专有字段
  intent?: IntentEventData;
  deciderName?: string;
}

export interface IntentEventData {
  speech?: string;
  emotion?: IntentEmotionData;
  action?: IntentActionData;
  metadata: IntentMetadataData;
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
 * - `agenda.*` / `planner.*` / `tool.result.<name>` / `game.*` / `live.*`
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

/** v2 工具 action（来自真实 ToolRegistry）。name 形如 `<provider>.<tool>`。 */
export interface UnifiedActionEntry {
  name: string;
  description?: string;
  parameters: Record<string, ParameterSpec>;
}

export interface UnifiedCapabilitiesView {
  actions: UnifiedActionEntry[];
}

// ==================== Simulator 控制面（ADR-006） ====================

/**
 * SimulatorService 实时状态（`/api/v1/simulator/status` 响应）。
 *
 * 字段语义：
 * - ``enabled``：当前进程在组合根加载时 ``[simulator].enabled`` 的值；
 *   即便 ``enabled=false`` 端点仍返回（不抛 404），前端据此渲染"未启用"空态
 *   并引导用户去 `Settings` 打开开关。
 * - ``is_available``：当前进程内存里是否持有 SimulatorService 实例（用于
 *   区分"配置启用但 LLMManager 未注入导致 setup 提前返回"与"完全没装配"）。
 * - ``is_running``：当前是否在生成循环里。
 * - ``message``：后端的状态说明文案（直接展示）。
 * - ``config``：只读配置摘要（见 backend `simulator._CONFIG_SUMMARY_KEYS`）。
 */
export interface SimulatorStatus {
  enabled: boolean;
  is_available: boolean;
  is_running: boolean;
  message: string;
  config: Record<string, unknown>;
}

/**
 * Simulator 启停响应（`/api/v1/simulator/start` 与 `…/stop` 共用）。
 */
export interface SimulatorControlResponse {
  success: boolean;
  message: string;
  is_running?: boolean;
}

// ==================== Mock 采集器控制面（ADR-006 收敛后） ====================

/**
 * Mock 采集器状态（`/api/v1/mock/status` 响应）。
 *
 * ADR-006 收敛后 mock 采集器只承担确定性 JSONL 回放；其暴露字段
 * （name/description/config）与 CollectorManager 注册实例一致。控制面
 * 不再承载旧"模拟器半吊子模式"的人设 / 礼物雨 / 话题注入 / token 预算
 * 等端点——对应类型 ``MockCollectorStats`` / ``MockCollectorPersona`` /
 * ``MockPersonaUpdatePayload`` 已删除。
 */
export interface MockCollectorStatus {
  is_available: boolean;
  is_running: boolean;
  name: string;
  description: string;
  /** 只读配置摘要（log_file_path / send_interval / loop_playback / emit_semantic_events） */
  config: Record<string, unknown>;
  message: string;
}

// ==================== 导出 settings / llm / trace 子模块 ====================

export * from './settings';
export * from './llm';
export * from './trace';
