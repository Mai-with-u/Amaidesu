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
  group: 'collectors' | 'agents';
  type: string;
  kind?: string;
  description?: string;
  is_started: boolean;
  is_enabled: boolean;
}

/** 工具不在组件清单：工具以"域开关单元"管理（见 ToolDomainUnit） */
export interface ComponentListResponse {
  collectors: ComponentSummary[];
  agents: ComponentSummary[];
}

export interface ComponentDetail {
  name: string;
  group: 'collectors' | 'agents';
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

// ==================== Streamer 测试台（主播发言调试） ====================

/**
 * 模拟弹幕（单条）。nickname 空时后端用「测试观众」占位。
 */
export interface StreamerTestDanmaku {
  nickname?: string;
  text: string;
}

/**
 * 两阶段决策的 Stage 1 产物（DecisionPlan 五字段透传）。
 * `should_reply=false` 时 speech 为 null——"没回复"是有效测试结果。
 */
export interface StreamerDecisionPlan {
  should_reply: boolean;
  target?: string | null;
  topic_summary?: string;
  reply_guidance?: string;
  confidence?: number;
}

/**
 * `POST /api/v1/streamer/test-decision` 请求体。
 *
 * `batch` 与 `proactive` 互斥：主动发言由房间状态驱动，不接受弹幕批次；
 * `forced=true` 豁免 Planner 低置信度降级（与 SC/礼物强制响应同语义）。
 */
export interface StreamerTestDecisionRequest {
  batch?: StreamerTestDanmaku[];
  forced?: boolean;
  proactive?: boolean;
}

/**
 * `POST /api/v1/streamer/test-decision` 响应（同步长请求，典型 10~30s）。
 *
 * `success=true` 表示门面调用成功；决策本身的失败（Planner 拒绝/LLM 异常）
 * 由 `error` / `plan` 如实表达，不算 API 失败。
 */
export interface StreamerTestDecisionResponse {
  success: boolean;
  message?: string | null;
  error?: string | null;
  trigger_reason?: string | null;
  proactive?: boolean;
  forced?: boolean;
  elapsed_ms?: number | null;
  plan?: StreamerDecisionPlan | null;
  speech?: string | null;
  emotion?: string | null;
  utterance_id?: string | null;
}

/** `GET /api/v1/streamer/status` 响应（agent 未注册时 available=false）。 */
export interface StreamerStatusResponse {
  available: boolean;
  message?: string | null;
  config: {
    proactive_enabled?: boolean;
    rundown_id?: string;
    batch_window_ms?: number;
    planner_llm?: string;
    replyer_llm?: string;
  };
  statistics: Record<string, number>;
}

/** `POST /api/v1/streamer/trigger-proactive` 请求体。 */
/** `POST /api/v1/streamer/proactive-toggle` 请求体。 */
export interface ProactiveToggleRequest {
  enabled: boolean;
}

/** `POST /api/v1/streamer/proactive-toggle` 响应。 */
export interface ProactiveToggleResponse {
  enabled: boolean;
  message: string;
}

export interface TriggerProactiveRequest {
  topic_hint?: string;
}

/** `POST /api/v1/streamer/trigger-proactive` 响应（置位语义，非立即触发）。 */
export interface TriggerProactiveResponse {
  success: boolean;
  message: string;
}

/** WS `streamer.speech` 事件 payload（StreamerSpeechPayload.model_dump）。 */
export interface StreamerSpeechEventData {
  utterance_id: string;
  text: string;
  emotion?: string | null;
  timestamp_ms?: number;
}

// ==================== v2 消息与会话 ====================

/**
 * v2 房间消息载荷（RoomMessagePayload.model_dump 的扁平结构）。
 *
 * 后端把 4 种 room.message.* EventBus 事件统一以 WS 类型 "room.message" 广播，
 * 消息种类由 `message_type` 判别；payload 本身无 source / message_id 字段。
 */
export interface RoomMessageEventData {
  live_session_id?: string;
  message_type: 'danmaku' | 'gift' | 'super_chat' | 'enter' | string;
  user?: { id?: string; name?: string } | null;
  content?: string;
  gift?: { name?: string; count?: number } | null;
  sc?: { amount?: number } | null;
  /** 弹幕 / SC / 礼物 message_id（来源：RoomMessagePayload.message_id；用于发言/决策卡回复引用反查）。
   * enter 类无关联消息，该字段为空。 */
  message_id?: string;
  /** 模拟数据溯源（true=模拟器生成/回放；统计查询必须排除） */
  simulated?: boolean;
  /** Unix 毫秒 */
  timestamp_ms?: number;
}

/**
 * v2 调试会话事件（会话调试页数据轴）——v2 对话闭环的三类观测点：
 * - 观众消息：room.message（WS 统一类型）
 * - 主播发言：streamer.speech
 * - 决策/编排/工具：rundown.changed / planner.decision / tool.result.*（摘要行）
 */
export interface DebugSessionEvent {
  id: string;
  type: string;
  /** Unix 秒（继承 WebSocketMessage / EventRecord 的 timestamp） */
  timestamp: number;
  kind: 'message' | 'speech' | 'system';
  /** kind === 'message' 时的房间消息载荷 */
  message?: RoomMessageEventData;
  /** kind === 'speech' 时的主播发言载荷 */
  speech?: StreamerSpeechEventData;
  /** 原始载荷（详情展开用） */
  data?: Record<string, unknown>;
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
 * `type` 字段是 WS 广播类型：4 种 room.message.* EventBus 事件统一广播为
 * `room.message`（消息种类由 payload.message_type 判别）；其余沿用事件名
 * （`streamer.speech` / `rundown.changed` / `planner.decision` /
 * `tool.result.<name>` / `game.*` / `live.*` / `system.*`）。
 */
export interface WebSocketMessage {
  /** 消息类别："event"=事件广播（缺省，进事件缓冲）；"stream"=观测流（独立缓冲，不入事件通道） */
  kind?: string;
  type: string;
  timestamp: number;
  data: Record<string, unknown>;
  /** 事件唯一 ID（前端幂等去重依据） */
  id?: string;
}

/** 思考流单条增量（WS kind="stream" / type="thinking.delta" 的 data.deltas 元素） */
export interface ThinkingDelta {
  round_id: string;
  /** "planner" | "replyer" */
  phase: string;
  step: number;
  seq: number;
  text_delta: string;
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

// ==================== Tools ====================

export type ParameterType = 'string' | 'number' | 'integer' | 'boolean';

export interface ParameterSpec {
  type: ParameterType;
  required: boolean;
  default?: unknown;
  description?: string;
  minimum?: number;
  maximum?: number;
}

/**
 * 工具熔断状态（GET /api/v1/tools 中每条工具的可选 `health` 字段）。
 *
 * 后端在工具有失败历史（熔断中或曾失败已恢复）时返回非 null；无任何失败历史时为 null 或不存在。
 */
export interface ToolHealth {
  state: 'tripped' | 'healthy';
  /** 触发熔断时的连续失败次数 */
  failure_count: number;
  /** 最后一次失败的错误文本 */
  last_error: string;
  /** 熔断时刻（Unix epoch 毫秒；未熔断的失败历史为 0） */
  tripped_at_ms: number;
}

/**
 * WS `tool.health.<tool_name>` 消息的 `data` 载荷。
 *
 * `state === 'open'` 表示该工具刚刚触发熔断；`state === 'closed'` 表示熔断恢复。
 * `tool_name` 与 `GET /api/v1/tools` 返回的 `ToolEntry.name` 等价。
 */
export interface ToolHealthEventData {
  tool_name: string;
  provider: string;
  state: 'open' | 'closed';
  failure_count: number;
  last_error: string;
  /** Unix epoch 毫秒 */
  timestamp_ms: number;
}

/**
 * 已注册工具条目（GET /api/v1/tools）。
 *
 * `name` 为完整工具名（`<provider>_<动词>_<对象>`，前缀内嵌）；`provider` /
 * `kind` / `category` 来自 ToolSpec 与注册时声明的提供者分类，不再从名字推断。
 */
export interface ToolEntry {
  name: string;
  description?: string;
  parameters: Record<string, ParameterSpec>;
  /** 工具提供者标识（vts / warudo / obs / vision / memory / text_adv / framework / <mcp server 名>） */
  provider?: string;
  /** 同步（gather 等齐）/ 异步（fire-and-forget + 事件回传） */
  kind?: 'sync' | 'async';
  /** 提供者分类（avatar / studio / vision / memory / mcp / game / framework） */
  category?: string;
  /** 是否已停用（对 LLM 不可见且不可调用；[tools].disabled_tools 驱动） */
  disabled?: boolean;
  /** 异步工具的结果回传事件名（仅 kind === 'async' 时存在） */
  result_event?: string;
  /** 熔断状态快照（仅在被熔断时为非 null；REST 初值 + WS 实时覆盖） */
  health?: ToolHealth | null;
  /**
   * 工具所属 Provider 是否支持手动重连（`POST /tools/providers/{provider_id}/reconnect`）。
   *
   * 仅当为 true 时前端才在工具条目上渲染重连按钮；该字段由后端 `/api/v1/tools`
   * 按所属 Provider 派生填充——前端不二次推断。
   */
  supports_reconnect?: boolean;
}

export interface ToolsView {
  tools: ToolEntry[];
}

/** 工具提供者（GET /api/v1/tools/categories 中分类下的成员） */
export interface ToolProviderUnit {
  /** 提供者配置键（vts / vrchat / warudo / obs；mcp 下为 server 名） */
  key: string;
  /** 工具 provider 标识（通常与 key 相同，例外：obs → obs_control） */
  provider_name: string;
  description: string;
  /** 配置态：[tools.<分类>.<键>].enabled */
  enabled: boolean;
  /** 该提供者是否在配置中声明过（false = 已知成员但配置未写，可首次开启） */
  in_config: boolean;
  /** Agent 自声明分类（game）不可开关 */
  switchable: boolean;
  /** 运行态：registry 中该提供者已注册的工具数（含停用） */
  tool_count: number;
  /** 其中停用的工具数 */
  disabled_count: number;
}

/** 工具提供者分类（GET /api/v1/tools/categories） */
export interface ToolCategoryView {
  category: string;
  providers: ToolProviderUnit[];
}

export interface ToolCategoriesView {
  categories: ToolCategoryView[];
}

export type ToolProviderControlAction = 'enable' | 'disable';

export interface ToolControlResponse {
  success: boolean;
  enabled: boolean;
  message: string;
}

export interface ToolProviderControlResponse {
  success: boolean;
  enabled: boolean;
  message: string;
}

/**
 * `POST /api/v1/tools/providers/{provider_id}/reconnect` 响应。
 *
 * - `recovered`：探活通过、熔断被清除的工具名列表。
 * - `still_tripped`：重连后探活仍失败、继续保持熔断的工具名列表；非空时
 *   前端按 warning 反馈（重连本身成功，但部分工具未恢复）。
 */
export interface ToolReconnectResponse {
  ok: boolean;
  provider_id: string;
  recovered: string[];
  still_tripped: string[];
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
  /** 当前运行模式（off/generate/replay） */
  mode: string;
  replay_progress: SimulatorReplayProgress | null;
  message: string;
  config: Record<string, unknown>;
}

/** replay 模式回放进度 */
export interface SimulatorReplayProgress {
  date: string | null;
  total: number;
  remaining: number;
}

/** 模拟器常驻人设（sim_personas 表的运行时视图） */
export interface SimPersona {
  user_id: string;
  user_nickname: string;
  role: string;
  personality: string;
  speaking_style: string;
  fans_medal_level: number;
  guard_level: number;
  context_window_size: number | null;
  is_temporary: boolean;
  is_active: boolean;
  messages_generated: number;
}

/** 模拟器礼物目录条目（sim_gifts 表的运行时视图） */
export interface SimGift {
  gift_id: string;
  gift_name: string;
  category: string;
  weight: number;
  data_type: string;
  sc_amount_rmb: number | null;
}

/**
 * Simulator 启停响应（`/api/v1/simulator/start` 与 `…/stop` 共用）。
 */
export interface SimulatorControlResponse {
  success: boolean;
  message: string;
  is_running?: boolean;
}

// ==================== Rundown（流程单编排页） ====================

/**
 * 流程单运行时快照（`/api/v1/agenda/state` 的 `snapshot` 字段）。
 *
 * - `status` 为后端派生值：`idle`（未加载）/ `running` / `paused` / `done`；
 *   前端据此切换"未加载"/"运行中"/"已暂停"/"已结束"布局。
 * - `current` 为 null 表示当前无环节（idle 或 done）。
 * - `paused`：暂停中时为 true；前端据此 toggle 暂停/继续按钮与本地 tick。
 * - `current.elapsed_ms` 为后端扣除暂停后的累计已播毫秒；
 *   `remaining_ms = expected_ms - elapsed_ms`，前端用 1s 本地 tick 重算显示。
 * - `progress_percent` 可能为 null（快照缺失），前端按 fallback 处理。
 */
export interface RundownSnapshot {
  status: 'idle' | 'running' | 'paused' | 'done';
  rundown_id: string;
  title: string;
  current: RundownCurrentSegment | null;
  index: number;
  total: number;
  progress_percent: number | null;
  paused: boolean;
}

/** 当前环节视图（快照嵌入对象；计时已扣除暂停时长）。 */
export interface RundownCurrentSegment {
  id: string;
  title: string;
  expected_ms: number;
  elapsed_ms: number;
  remaining_ms: number;
}

/** 流程单环节完整定义（`/api/v1/agenda/state` 的 `segments` 字段）。 */
export interface RundownSegmentView {
  id: string;
  title: string;
  task_description: string;
  key_points: string[];
  expected_ms: number;
  min_duration_ms: number | null;
  notes: string | null;
}

/** 流程单变更历史（最近 50 条，旧→新由后端保证）。 */
export interface RundownTransitionEntry {
  action: string;
  segment_id: string;
  by: 'agent' | 'human' | 'system';
  at_ms: number;
}

/** 流程单配置摘要（只读；写需走 Settings 页）。 */
export interface RundownConfig {
  rundown_id: string;
}

/**
 * `GET /api/v1/agenda/state` 完整响应。
 *
 * - `available=false` 表示后端未加载流程单（agent 未启动等），
 *   前端按"不可用态"渲染引导用户去编排页新建。
 * - `message` 在 available=false 时填入原因文案，available=true 时为 null。
 * - `transitions` / `segments` 仅在 available=true 时非空。
 */
export interface RundownStateResponse {
  available: boolean;
  message: string | null;
  snapshot: RundownSnapshot | null;
  transitions: RundownTransitionEntry[];
  segments: RundownSegmentView[];
  config: RundownConfig;
}

/**
 * 流程单控制动作枚举（与后端 control 端点对齐；后端固定 by="human"）。
 *
 * - `pause` / `resume` 仅在对应状态合法；
 * - `goto` 需要 `segment_id`；
 * - `next` 在末段时结束整场流程单。
 */
export type RundownControlAction = 'pause' | 'resume' | 'next' | 'goto';

/** `POST /api/v1/agenda/control` 请求体。 */
export interface RundownControlRequest {
  action: RundownControlAction;
  segment_id?: string;
}

/**
 * `POST /api/v1/agenda/control` 响应。
 *
 * `success=false` 时 `message` 填拒绝/错误原因（前端用 ElMessage 弹窗）；成功时
 * `snapshot` 是控制后最新快照（前端用其刷新展示，避免 WS 抖动期的闪烁）。
 */
export interface RundownControlResponse {
  success: boolean;
  message: string;
  snapshot: RundownSnapshot | null;
}

// ==================== 导出 settings / llm / trace 子模块 ====================

export * from './settings';
export * from './llm';
export * from './trace';

// ===== 直播场次（直播控制台） =====

/** 场次列表条目（GET /api/v1/live-sessions） */
export interface LiveSessionItem {
  live_session_id: number;
  /** 场次来源：manual=手动 / replay=模拟器回放 / legacy=历史遗留 */
  source: string;
  title: string | null;
  room_id: string;
  platform: string;
  started_at_ms: number;
  /** NULL = 进行中 */
  ended_at_ms: number | null;
  message_count: number;
  /** 是否为当前进行中的显式场次 */
  is_active: boolean;
}

export interface LiveSessionListResponse {
  items: LiveSessionItem[];
  active_session_id: number | null;
}

/** 单场时间线条目（GET /api/v1/live-sessions/{id}/timeline） */
export interface SessionTimelineItem {
  /** danmaku / gift / super_chat / speech / event */
  kind: string;
  ts_ms: number;
  /** kind=event 时的事件类型（planner.decision / streamer.stage / live.* 等） */
  event_type?: string;
  /** kind=event 时的事件负载 */
  data?: Record<string, unknown>;
  /** 弹幕（live_chat）的 message_id；用于发言/决策卡回复引用反查。
   * speech / gift / super_chat / enter 在回看 API 中不带此字段。 */
  message_id?: string;
  [key: string]: unknown;
}

export interface SessionTimelineResponse {
  live_session_id: number;
  items: SessionTimelineItem[];
}
