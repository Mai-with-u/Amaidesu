// 后端 REST/WS 接口的类型定义；事件名以语义域事件名为准。

import type { WsKind, WsMessageType } from './events';

export type { WsKind, WsMessageType };

// 系统状态

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
 * EventBus 总吞吐统计（顶层暴露）。
 */
export interface EventBusStats {
  total_events: number;
}

/**
 * 系统状态响应。
 *
 * 字段说明（与后端 `/api/v1/system/status` 对齐）：
 * - `running` / `uptime_ms` / `version` / `python_version`：运行时元信息
 * - `groups`：三组组件运行统计（collectors / agents / tools）
 * - `event_bus`：EventBus 全局吞吐
 */
export interface SystemStatusResponse {
  running: boolean;
  /** 进程运行时长（毫秒） */
  uptime_ms: number;
  version: string;
  python_version: string;
  groups: {
    collectors: GroupStatus;
    agents: GroupStatus;
    tools: GroupStatus;
  };
  event_bus: EventBusStats;
}

/**
 * 更新日志响应：CHANGELOG.md 原文（markdown），前端渲染。
 */
export interface ChangelogResponse {
  content: string;
}

/** 健康检查响应（GET /api/v1/system/health） */
export interface SystemHealthResponse {
  status: string;
  timestamp_ms: number;
}

/** 连接健康墙单项：一个可观测组件的连接态快照（GET /system/health）。 */
export interface SystemHealthItem {
  name: string;
  state: 'ok' | 'degraded' | 'offline' | 'unknown';
  detail: string;
  last_error: string;
  supports_reconnect: boolean;
}

/** 连接健康墙响应：按域分组的组件连接态（domains 为空 = 聚合数据源缺失）。 */
export interface SystemHealthWallResponse {
  status: string;
  timestamp_ms: number;
  checked_at_ms: number | null;
  domains: Record<string, SystemHealthItem[]>;
}

// 组件
/**
 * 组件摘要。
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
  /** 附属采集器随哪个 Agent 开关；通用采集器与 Agent 为空 */
  owner?: string | null;
}

/** 工具不在组件清单：工具以"域开关单元"管理（见 ToolDomainUnit） */
export interface ComponentListResponse {
  collectors: ComponentSummary[];
  agents: ComponentSummary[];
}

export type ComponentControlAction = 'start' | 'stop' | 'restart';

export interface ComponentControlRequest {
  action: ComponentControlAction;
}

export interface ComponentControlResponse {
  success: boolean;
  message: string;
}

/** 开播意图清单条目：组件组 + 注册名（期望运行全集的一员） */
export interface ComponentRef {
  group: 'collectors' | 'agents';
  name: string;
}

/** 开播意图对齐的单组件回执 */
export interface ComponentPlanItem {
  group: string;
  name: string;
  action: 'start' | 'stop' | 'skip';
  result: 'started' | 'stopped' | 'failed' | 'skipped';
  detail: string;
}

/** 开播意图对齐逐项回执（开场请求带 component_plan 时随响应返回） */
export interface ComponentPlanReport {
  items: ComponentPlanItem[];
  /** enabled 名单写盘失败原因（空串=写盘成功） */
  config_error: string;
}

// Agent 控制面

/**
 * Agent 运行名册条目（`GET /api/v1/agents`）。
 *
 * - `state`：Agent 决策循环状态（如 running / paused），由后端派生
 * - `heartbeat_ms`：最近心跳距当前的毫秒数
 * - `is_alive`：心跳是否超时判定存活
 * - `restart_count`：重启计数（跨实例继承）
 * - `enabled`：agents.toml `[agents].enabled` 配置态标记
 */
export interface AgentInfo {
  name: string;
  description: string;
  state: string;
  heartbeat_ms: number;
  is_alive: boolean;
  restart_count: number;
  enabled: boolean;
}

export interface AgentListResponse {
  agents: AgentInfo[];
}

/** 单 Agent 运行状态（`GET /api/v1/agents/{name}/state`，无 enabled） */
export type AgentState = Omit<AgentInfo, 'enabled'>;

/** Agent 框架级控制动作（pause/resume 即时生效；shutdown/restart 高风险需 confirm） */
export type AgentControlActionType = 'pause' | 'resume' | 'shutdown' | 'restart';

export interface AgentControlResponse {
  success: boolean;
  action: string;
  name: string;
  message: string;
  state?: string | null;
}

/** Agent 递话响应（纯文本留言：不派新任务） */
export interface AgentPromptResponse {
  delivered: boolean;
}

/** 任务硬取消响应（任务未知/已终态 404） */
export interface AgentCancelTaskResponse {
  cancelled: boolean;
}

/** Agent 直派受理回执（派新任务，登记账本） */
export interface AgentDelegateResponse {
  accepted: boolean;
  task_id: string;
  executor: string;
}

/** minecraft 待办条目（minecraft_todo 文档元素） */
export interface MemoTodoItem {
  content: string;
  status: string;
}

/** Agent 备忘录/待办快照（`GET /api/v1/agents/{name}/memo`，纯只读观测）。
 *  `todos` / `reports` 仅 minecraft 返回（三元组）；主播只有 `notebook`，两者为 null */
export interface AgentMemoResponse {
  name: string;
  notebook: string;
  todos?: MemoTodoItem[] | null;
  reports?: Array<Record<string, string>> | null;
}

/** 任务卡（进行中 = 账本快照；已完结 = 事件环聚合的终态末次载荷） */
export interface TaskCard {
  task_id: string;
  instruction: string;
  status: string;
  initiator: string;
  executor: string;
  /** 任务归属 Agent（agent 型 = 执行 Agent；provider 型自驱 = 发起 Agent） */
  owner_agent: string;
  summary: string;
  created_at_ms: number;
  updated_at_ms: number;
}

/** 任务卡全量快照（GET /api/v1/tasks；已完结仅本次运行内） */
export interface TaskSnapshotResponse {
  running: TaskCard[];
  finished: TaskCard[];
}

// 配置

/** 合并视图响应（GET /api/v1/config）：顶层键为七个配置 scope 的展平字典 */
export interface ConfigGetResponse {
  config: Record<string, unknown>;
}

/** 批量写回中单条变更的处理结果 */
export interface ConfigBatchResultItem {
  key: string;
  success: boolean;
}

/** 批量写回中失败条目的错误明细 */
export interface ConfigBatchErrorItem {
  key: string;
  message: string;
}

/** 批量原子写回响应（POST /api/v1/config/batch）：全成或整批回退；
 *  requires_restart 表示改动须重启生效 */
export interface ConfigBatchSaveResponse {
  success: boolean;
  message: string;
  requires_restart?: boolean;
  results?: ConfigBatchResultItem[];
  errors?: ConfigBatchErrorItem[];
}

// 调试注入

export interface InjectMessageRequest {
  source?: string;
  text: string;
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

// 调试台：基础设施直测（字幕 / TTS）

export interface SubtitleTestRequest {
  text: string;
}

/** show 盲发：逐后端成败以 status 端点与目视为准 */
export interface SubtitleTestResponse {
  success: boolean;
  backend_count: number;
  error?: string;
}

export interface SubtitleClearResponse {
  success: boolean;
  error?: string;
}

/** 单个字幕后端诊断项：enabled=false 即"静默降级"（如 CustomTkinter 不可用） */
export interface SubtitleBackendInfo {
  name: string;
  enabled: boolean;
}

export interface SubtitleStatusResponse {
  available: boolean;
  backend_count: number;
  backends: SubtitleBackendInfo[];
}

export interface TtsTestRequest {
  text: string;
  /** true=试说（声卡播放+字幕联动）；false=生成试听音频（返回 wav 数据） */
  play_on_device?: boolean;
  /** 覆盖参数：仅生成试听模式生效，缺省用引擎配置值 */
  temperature?: number;
  speed_factor?: number;
  sample_steps?: number;
  top_k?: number;
  top_p?: number;
}

/** 同步长请求：等待合成与播放，前端需放宽超时 */
export interface TtsTestResponse {
  success: boolean;
  utterance_id?: string;
  error?: string;
  /** 生成试听模式返回的完整 wav 音频（base64） */
  audio_base64?: string;
}

/** 台前重播请求：同文本重合成（utterance 事件无服务端留档，前端回传原文本） */
export interface TtsSayRequest {
  text: string;
}

/** 台前重播响应（同步长请求；合成失败时引擎已发 tts.utterance.failed） */
export interface TtsSayResponse {
  success: boolean;
  utterance_id?: string;
  error?: string;
}

export interface TtsStatusResponse {
  available: boolean;
  stats: Record<string, unknown>;
}

// Streamer 测试台（主播发言调试）

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
}

/**
 * `POST /api/v1/streamer/test-decision` 请求体。
 *
 * `batch` 与 `proactive` 互斥：主动发言由房间状态驱动，不接受弹幕批次；
 * `forced=true` 强制响应（SC/礼物/上舰）。
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

/** `POST /api/v1/streamer/proactive-toggle` 请求体。 */
export interface ProactiveToggleRequest {
  enabled: boolean;
}

/** `POST /api/v1/streamer/proactive-toggle` 响应。 */
export interface ProactiveToggleResponse {
  enabled: boolean;
  message: string;
}

/** `POST /api/v1/streamer/trigger-proactive` 请求体。 */
export interface TriggerProactiveRequest {
  topic_hint?: string;
}

/** `POST /api/v1/streamer/trigger-proactive` 响应（置位语义，非立即触发）。 */
export interface TriggerProactiveResponse {
  success: boolean;
  message: string;
}

// WebSocket

/**
 * WebSocket 消息。
 *
 * `type` 字段是 WS 广播类型，词汇与折叠规则见 `./events`（前端唯一事实源，
 * 与后端 broadcaster 订阅清单对账）：直通事件的 type = 事件名；room.message.*
 * 与 room.state.* 折叠为族类型（成员由载荷字段判别）；通配族的 type 为
 * 具体事件名（如 `tool.result.<name>`）。
 */
export interface WebSocketMessage {
  /** 消息类别："event"=事件广播（缺省，进事件缓冲）；"stream"=观测流；"log"=日志通道；"transport"=握手心跳（后两者不入事件通道） */
  kind?: WsKind;
  type: WsMessageType;
  /** 消息时刻（Unix 毫秒） */
  timestamp_ms: number;
  data: Record<string, unknown>;
  /** 事件唯一 ID（前端幂等去重依据） */
  id?: string;
}

/** 思考流单条增量（WS kind="stream" / type="thinking.delta" 的 data.deltas 元素） */
export interface ThinkingDelta {
  round_id: string;
  /** "planner"（主播 ReAct）| "replyer"（表达生成）| "minecraft"（游戏 Agent ReAct） */
  phase: string;
  step: number;
  seq: number;
  text_delta: string;
}

export interface SubscribeRequest {
  action: 'subscribe' | 'unsubscribe';
  events: string[];
}

// 事件历史（GET /events，游标续传 + 落盘扩容）

/** 事件历史查询响应：断线/刷新后补缺口（since_id）与时间窗查询共用 */
export interface EventListResponse {
  events: WebSocketMessage[];
  total: number;
  has_more: boolean;
  has_more_newer?: boolean;
}

// 日志历史（GET /logs，扫 logs/webui 按天落盘 JSONL）

export interface LogEntryItem {
  timestamp: string;
  level: string;
  module: string;
  message: string;
  exception?: string;
}

export interface LogsQueryResponse {
  entries: LogEntryItem[];
  total: number;
  has_more_older: boolean;
  has_more_newer: boolean;
  /** 落盘不可用（未启用/目录缺失）时历史降级为空 */
  persist_available: boolean;
  /** 实际生效的窗口起点（Unix 毫秒；缺省=本次启动时刻） */
  window_start_ms: number;
}

// Tools

/** json = array/object/未声明 type 的复杂参数（JSON 文本域承载，提交时解析） */
export type ParameterType = 'string' | 'number' | 'integer' | 'boolean' | 'json';

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
  /** 注册表调用键（`<provider>_<工具名>`）；停用开关与调试调用都以它为标识 */
  full_name: string;
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

/**
 * 工具调试调用响应（POST /tools/{full_name}/invoke）。
 *
 * ToolExecutionResult 的 HTTP 形态：sync 工具即最终结果；async 工具为受理
 * 回执，真实结果经 WS `tool.result.<full_name>` 事件回传。
 */
export interface ToolInvokeResult {
  success: boolean;
  content: string;
  blocks: { kind: 'text' | 'image'; text: string; data: string; mime_type: string }[];
  error_message: string;
  structured_content?: unknown;
  duration_ms: number;
  timestamp_ms: number;
}

/** 工具提供者（GET /api/v1/tools/categories 中分类下的成员） */
export interface ToolProviderUnit {
  /** 提供者配置键（vts / vrchat / warudo / obs；mcp 下为 server 名） */
  key: string;
  /** 工具 provider 标识（通常与 key 相同，例外：obs → obs_control） */
  provider_name: string;
  description: string;
  /** 配置态：开关状态（位置由后端路由——tools.toml 段或提供者声明的 agents.toml 键） */
  enabled: boolean;
  /** 该提供者是否在配置中声明过（false = 已知成员但配置未写，可首次开启） */
  in_config: boolean;
  /** 随 Agent 启用的分类（game / framework）不可开关 */
  switchable: boolean;
  /** 运行态：registry 中该提供者已注册的工具数（含停用） */
  tool_count: number;
  /** 其中停用的工具数 */
  disabled_count: number;
  /** registry 中有该 Provider 的登记记录（false = 仅配置声明，待重启装配） */
  registered?: boolean;
  /** 已登记但 0 工具（通常连接失败降级登记；配合 last_error 展示原因） */
  degraded?: boolean;
  /** Provider 侧最近一次连接失败摘要（无失败历史为空串） */
  last_error?: string;
  /** Provider 级手动重连按钮可见性（无连接语义的 Provider 为 false） */
  supports_reconnect?: boolean;
  /** 随卡片展示的管理提示（如 Agent 私有 MCP 停用后采集器仍会连接） */
  notice?: string;
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
  /** 工具集刷新报告（降级登记补注册 / server 清单换血）；刷新异常时为 null */
  refreshed?: { added: string[]; removed: string[]; count: number } | null;
}

// Simulator 控制面

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
  /** 生成循环最后一次致命故障描述（LLM 不可恢复错误等，无则 null） */
  last_error: string | null;
  message: string;
  config: Record<string, unknown>;
}

/** replay 模式回放进度 */
export interface SimulatorReplayProgress {
  date: string | null;
  total: number;
  remaining: number;
}

/** 可回放录制日期条目（带当日弹幕条数） */
export interface ReplayDateEntry {
  date: string;
  danmaku_count: number;
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
  unit_price: number;
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

/** 可回放录制日期列表响应（GET /api/v1/simulator/replay/dates） */
export interface ReplayDatesResponse {
  dates: ReplayDateEntry[];
}

/** 常驻人设列表响应（GET /api/v1/simulator/personas；pool 未装配时为空列表 + is_available=false） */
export interface PersonaListResponse {
  personas: SimPersona[];
  is_available: boolean;
}

/** 新增常驻人设响应（失败时 persona 为空） */
export interface PersonaCreateResponse {
  success: boolean;
  message?: string;
  persona?: SimPersona;
}

/** 常驻人设更新 / 删除的通用响应 */
export interface PersonaOperationResponse {
  success: boolean;
  message?: string;
}

/** 礼物目录列表响应（GET /api/v1/simulator/gifts；生成器未装配时为空列表 + is_available=false） */
export interface GiftListResponse {
  gifts: SimGift[];
  is_available: boolean;
}

/** 新增礼物响应（失败时 gift 为空） */
export interface GiftCreateResponse {
  success: boolean;
  message?: string;
  gift?: SimGift;
}

/** 礼物更新 / 删除的通用响应 */
export interface GiftOperationResponse {
  success: boolean;
  message?: string;
}

// Rundown（流程单编排页）

/**
 * 流程单运行时快照（`/api/v1/rundown/state` 的 `snapshot` 字段）。
 *
 * - `status` 为后端派生值：`idle`（未加载）/ `armed`（已装载待开播）/
 *   `running` / `paused` / `done`；
 *   前端据此切换"未加载"/"待开播"/"运行中"/"已暂停"/"已结束"布局。
 * - `current` 为 null 表示当前无环节（idle 或 done）。
 * - `paused`：暂停中时为 true；前端据此 toggle 暂停/继续按钮与本地 tick。
 * - `current.elapsed_ms` 为后端扣除暂停后的累计已播毫秒；
 *   `remaining_ms = expected_ms - elapsed_ms`，前端用 1s 本地 tick 重算显示。
 * - `progress_percent` 可能为 null（快照缺失），前端按 fallback 处理。
 */
export interface RundownSnapshot {
  status: 'idle' | 'armed' | 'running' | 'paused' | 'done';
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

/** 流程单环节完整定义（`/api/v1/rundown/state` 的 `segments` 字段）。 */
export interface RundownSegmentView {
  id: string;
  title: string;
  task_description: string;
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
 * `GET /api/v1/rundown/state` 完整响应。
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
 * - `next` 在末段时结束整场流程单；
 * - `restart` 整场重开（游标回首环节、计时清零；导演通道）；
 * - `insert` 需要 `payload`（应急插话题：当前环节后插一次性环节）。
 */
export type RundownControlAction = 'pause' | 'resume' | 'next' | 'goto' | 'restart' | 'insert';

/** `insert` 动作的一次性应急话题内容（标题必填，内容可选）。 */
export interface RundownInsertPayload {
  title: string;
  content?: string;
}

/** `POST /api/v1/rundown/control` 请求体。 */
export interface RundownControlRequest {
  action: RundownControlAction;
  segment_id?: string;
  payload?: RundownInsertPayload;
}

/**
 * `POST /api/v1/rundown/control` 响应。
 *
 * `success=false` 时 `message` 填拒绝/错误原因（前端用 ElMessage 弹窗）；成功时
 * `snapshot` 是控制后最新快照（前端用其刷新展示，避免 WS 抖动期的闪烁）。
 */
export interface RundownControlResponse {
  success: boolean;
  message: string;
  snapshot: RundownSnapshot | null;
}

// Rundown 流程单库（编辑器）

/**
 * 流程单完整定义——库列表项与保存请求体共用同一形状。
 *
 * `rundown_id` 为机器标识、不在用户契约面：保存请求省略/空串 = 新建（服务端
 * 生成）；带 id = 保存该份单（id 必须已存在）。库列表响应始终携带。
 *
 * 完整性校验（环节 id 唯一、时长下界、最短停留不超预期）由后端领域模型负责，
 * 前端仅做非空预检以省一次往返。
 */
export interface RundownDefinition {
  rundown_id?: string;
  title: string;
  segments: RundownSegmentView[];
}

/** `GET /api/v1/rundowns` 响应；`current_id` 为空表示配置未选单（走内置默认流程单）。 */
export interface RundownListResponse {
  success: boolean;
  message: string;
  rundowns: RundownDefinition[];
  current_id: string;
}

/** `GET /api/v1/rundowns/template` 响应（内置默认流程单，新建预填模板）。 */
export interface RundownTemplateResponse {
  success: boolean;
  message: string;
  definition: RundownDefinition | null;
}

/** 流程单库写操作统一响应（upsert / delete / duplicate / activate）。 */
export interface RundownMutateResponse {
  success: boolean;
  message: string;
  rundown_id: string | null;
}

// 导出 settings / llm 子模块

export * from './settings';
export * from './llm';
export * from './review';

// 直播场次（直播控制台）

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

/** 开启新场次响应（POST /api/v1/live-sessions/open） */
export interface SessionOpenResponse {
  live_session_id: number;
  /** 组件意图对齐回执（请求带 component_plan 时返回，否则为 null） */
  plan_report: ComponentPlanReport | null;
}

/** 场次动作通用响应（close / rename / remove 共用） */
export interface SessionActionResponse {
  success: boolean;
  detail: string;
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
  /** 是否还有更早条目（以最旧条目 ts_ms 为 before_timestamp_ms 游标续翻） */
  has_more: boolean;
  /** 场次时间范围内存在缺失的诊断天目录（被清理或未落盘），事件侧降级 */
  events_pruned: boolean;
}

/** 流程单单段执行事实（一次进入 = 一条；goto 回跳会再次出现） */
export interface RundownSegmentRun {
  seg_id: string;
  seg_title: string;
  seg_index: number;
  entered_at_ms: number;
  /** 空 = 直到场次结束/当前时刻仍停留在该环节（停留未封口） */
  left_at_ms: number | null;
  /** 墙钟停留（含暂停；未封口只含已确认部分，进行中场次恒 0） */
  dwell_ms: number;
  /** 进入时该环节的预计时长 */
  expected_ms: number | null;
  pause_ms: number;
}

/** 单场流程单执行回看（GET /api/v1/live-sessions/{id}/rundown-execution） */
export interface SessionRundownExecutionResponse {
  live_session_id: number;
  rundown_id: string;
  segments: RundownSegmentRun[];
  pause_total_ms: number;
  transitions_count: number;
}

// ==================== Vision（视觉捕获） ====================

/** 单台显示器（mss 枚举；index=0 为虚拟合屏，不参与选择） */
export interface VisionMonitor {
  index: number;
  left: number;
  top: number;
  width: number;
  height: number;
  is_primary: boolean;
}

/** `GET /vision/monitors` 响应 */
export interface VisionMonitorsResponse {
  count: number;
  monitors: VisionMonitor[];
}

/** `GET /vision/preview` 响应（抓帧 + region 叠框标注） */
export interface VisionPreviewResponse {
  image_b64: string;
  width: number;
  height: number;
  monitor_index: number;
  region: number[] | null;
}

/** 单平台虚拟形象调试状态（`GET /avatar/debug/status` 成员） */
export interface AvatarDebugPlatformStatus {
  platform: string;
  connected: boolean | null;
  /** 各平台均未暴露"当前表情"读取通道，恒为 null（预留字段） */
  current_expression: string | null;
  detail: string;
  actions: { name: string; type?: string }[];
  emotions: string[];
  stats: Record<string, unknown>;
}

/** `GET /avatar/debug/status` 响应 */
export interface AvatarDebugStatusResponse {
  platforms: AvatarDebugPlatformStatus[];
}

/** `POST /avatar/debug/trigger` 请求 */
export interface AvatarDebugTriggerRequest {
  platform: string;
  kind: 'expression' | 'parameter' | 'action';
  key: string;
  value: number;
}

/** `POST /avatar/debug/trigger` 响应（success=false 表示业务拒绝，原因在 detail） */
export interface AvatarDebugTriggerResponse {
  platform: string;
  kind: string;
  key: string;
  value: number;
  success: boolean;
  detail: string;
  structured_content: Record<string, unknown> | null;
}

/** 观众身份确认状态：confirmed=已确认 / pending=与老观众同名、待人工确认是不是同一个人 */
export type ViewerLinkStatus = 'confirmed' | 'pending';

/** 单行观众统计（viewers 表行投影） */
export interface ViewerListItem {
  /** 内部观众 ID：档案页与各单观众接口按它定位 */
  viewer_id: number;
  platform: string;
  /** 代表外部 ID（账号 ID 优先，其次开放平台 ID；没有真实 ID 时为空），只作展示 */
  user_id: string;
  user_name: string;
  link_status: ViewerLinkStatus;
  message_count: number;
  gift_count: number;
  replied_count: number;
  interaction_count: number;
  /** 付费次数 = 礼物 + SC + 上舰（B 站不计银瓜子免费礼物） */
  paid_count: number;
  /** 付费总额（金瓜子，展示层 ÷1000 = 元） */
  paid_amount: number;
  last_active_ms: number;
}

export interface ViewerListResponse {
  /** 命中搜索条件的全量行数（分页器 total） */
  total: number;
  items: ViewerListItem[];
}

/** 按天弹幕量点（本地日期 YYYY-MM-DD） */
export interface DailyDanmakuPoint {
  day: string;
  count: number;
}

/** 互动分析聚合（GET /api/v1/viewers/insights）；活跃分桶互斥 */
export interface ViewerInsights {
  total_viewers: number;
  active_today: number;
  active_week: number;
  active_month: number;
  active_older: number;
  never_replied: number;
  gift_viewers: number;
  daily_danmaku: DailyDanmakuPoint[];
}

/** 观众用过的一个昵称及其最早、最晚出现时刻 */
export interface ViewerNameItem {
  user_name: string;
  first_seen_ms: number;
  last_seen_ms: number;
}

/** 另一位观众的简要信息（并入建议等） */
export interface ViewerBrief {
  viewer_id: number;
  user_name: string;
}

/** 单观众档案（GET /api/v1/viewers/{viewerId}） */
export interface ViewerDetail {
  viewer_id: number;
  platform: string;
  /** 代表外部 ID（账号 ID 优先） */
  user_id: string;
  /** 平台账号 ID（B 站 uid） */
  account_id: string | null;
  /** 开放平台 ID（B 站 open_id） */
  open_id: string | null;
  /** 发出 open_id 的开放平台应用；空串 = 存量数据不明 */
  open_app_id: string | null;
  user_name: string;
  /** 用过的昵称（含当前昵称），最近在前 */
  names: ViewerNameItem[];
  link_status: ViewerLinkStatus;
  /** 待确认时建议并入的老观众 */
  link_candidate: ViewerBrief | null;
  message_count: number;
  gift_count: number;
  replied_count: number;
  interaction_count: number;
  /** 付费次数 = 礼物 + SC + 上舰 */
  paid_count: number;
  /** 付费总额（金瓜子，展示层 ÷1000 = 元） */
  paid_amount: number;
  last_active_ms: number;
  first_seen_ms: number | null;
  gift_total_count: number;
  sc_total_amount: number;
  sc_total_count: number;
  session_count: number;
}

/** 对话交织行：viewer=观众消息 / reply=主播对其的回复 */
export interface ViewerDialogueItem {
  id: number;
  kind: 'viewer' | 'reply';
  content: string;
  timestamp_ms: number;
  live_session_id: number | null;
  message_type: string | null;
  message_id: string | null;
  reply_to_message_id: string | null;
  simulated: boolean;
}

export interface ViewerDialogueResponse {
  items: ViewerDialogueItem[];
  /** 下一批游标（观众消息主轴），无更多为 null */
  next_before: number | null;
}

export interface ViewerGiftItem {
  timestamp_ms: number;
  live_session_id: number | null;
  gift_name: string;
  gift_count: number;
  /** 标价总额（金瓜子，展示层 ÷1000 = 元） */
  total_price: number;
  simulated: boolean;
}

export interface ViewerSuperChatItem {
  timestamp_ms: number;
  live_session_id: number | null;
  amount: number;
  message: string;
  simulated: boolean;
}

/** 观众贡献：汇总 + 明细（GET /api/v1/viewers/{viewerId}/contributions） */
export interface ViewerContributions {
  gift_total_count: number;
  sc_total_amount: number;
  sc_total_count: number;
  gifts: ViewerGiftItem[];
  super_chats: ViewerSuperChatItem[];
}

/** 观众参与的单个场次聚合行 */
export interface ViewerSessionItem {
  live_session_id: number;
  title: string | null;
  message_count: number;
  first_ms: number;
  last_ms: number;
}

export interface ViewerSessionsResponse {
  items: ViewerSessionItem[];
}

/** 一位待确认观众：新建的这一位 + 建议并入的老观众，供人工比对 */
export interface PendingViewerItem {
  viewer_id: number;
  platform: string;
  user_name: string;
  account_id: string | null;
  open_id: string | null;
  message_count: number;
  interaction_count: number;
  /** 付费总额（金瓜子） */
  paid_amount: number;
  last_active_ms: number;
  candidate: ViewerBrief | null;
  candidate_account_id: string | null;
  candidate_open_id: string | null;
  /** 老观众最近一次以该昵称出现的时刻 */
  candidate_name_seen_ms: number;
  candidate_message_count: number;
  candidate_interaction_count: number;
  candidate_paid_amount: number;
  candidate_last_active_ms: number;
  /** 老观众用过的昵称（最近在前） */
  candidate_names: string[];
}

export interface PendingViewersResponse {
  total: number;
  items: PendingViewerItem[];
}

/** 观众身份人工处理的结果：处理后应查看的观众（合并的目标 / 拆出的新观众 / 原观众） */
export interface ViewerIdentityActionResponse {
  viewer_id: number;
}

// ===== 观众画像管理（/api/v1/memory） =====

/** 一位观众的印象（viewer_profiles 单行 + 观众的平台与当前昵称） */
export interface ViewerProfileItem {
  viewer_id: number;
  platform: string;
  user_name: string;
  profile_text: string;
  /** 已整理到的时间点：该时刻前的弹幕与付费记录已融进印象 */
  last_compressed_at_ms: number;
  updated_at_ms: number;
}

export interface ViewerProfileListResponse {
  /** 命中搜索条件的全量行数（分页器用） */
  total: number;
  items: ViewerProfileItem[];
}

/** 画像纠正请求体 */
export interface ViewerProfileUpdatePayload {
  profile_text: string;
}

export interface MemoryMutationResponse {
  success: boolean;
}

export interface MemoryStatsResponse {
  profile_count: number;
  /** 最近一次印象更新时刻，空库为 0 */
  latest_updated_ms: number;
}
