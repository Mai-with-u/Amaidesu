/**
 * Dashboard API 客户端
 *
 * 承载 Dashboard 消费的全部后端 ``/api/v1`` REST 接口：按业务域分组导出，
 * 每域一个 api 对象（端点行为语义见各导出处的注释），请求统一经文件内
 * 共享的 axios 实例发出。
 */

import axios from 'axios';
import type {
  AgentControlActionType,
  AgentMemoResponse,
  AgentControlResponse,
  AgentDelegateResponse,
  AgentListResponse,
  AgentPromptResponse,
  AgentState,
  AgentCancelTaskResponse,
  AgentStopResponse,
  ArchiveLineCreatePayload,
  ArchiveListResponse,
  CandidateApprovePayload,
  CandidateItem,
  CandidateListResponse,
  CandidateStatus,
  SystemStatusResponse,
  SystemHealthResponse,
  SystemHealthWallResponse,
  ChangelogResponse,
  CommitmentClosePayload,
  CommitmentCreatePayload,
  CommitmentItem,
  CommitmentListResponse,
  CommitmentStatus,
  ConfigGetResponse,
  ConfigBatchSaveResponse,
  EventListResponse,
  ComponentListResponse,
  ComponentControlRequest,
  ComponentControlResponse,
  ComponentRef,
  InjectMessageRequest,
  InjectMessageResponse,
  EventBusStatsResponse,
  SubtitleTestRequest,
  SubtitleTestResponse,
  SubtitleClearResponse,
  SubtitleStatusResponse,
  TtsTestRequest,
  TtsTestResponse,
  TtsSayRequest,
  TtsSayResponse,
  TtsStatusResponse,
  ConfigUpdateResponse,
  LLMContextBreakdown,
  LLMProviderQuota,
  LLMUsageStats,
  LLMUsageSummary,
  LLMUsageTrendsResponse,
  LLMHistoryQueryParams,
  LLMHistoryResponse,
  LLMHistoryStatistics,
  LLMModelTestRequest,
  LLMModelTestResponse,
  LLMRequestHistory,
  LLMRequestStatsBatch,
  LedgerMutationResponse,
  ToolsView,
  ToolCategoriesView,
  ToolInvokeResult,
  ToolProviderControlAction,
  ToolControlResponse,
  ToolProviderControlResponse,
  ToolReconnectResponse,
  SimulatorStatus,
  SimPersona,
  ReplayDatesResponse,
  PersonaListResponse,
  PersonaCreateResponse,
  PersonaOperationResponse,
  PlayerCandidateItem,
  PlayerCandidateListResponse,
  PlayerLinkPayload,
  PlayerListResponse,
  GiftListResponse,
  GiftCreateResponse,
  GiftOperationResponse,
  SimGift,
  SessionOpenResponse,
  SessionActionResponse,
  TaskSnapshotResponse,
  SimulatorControlResponse,
  SimulatorAssemblyResponse,
  RundownStateResponse,
  RundownControlRequest,
  RundownControlResponse,
  RundownDefinition,
  RundownListResponse,
  RundownMutateResponse,
  RundownTemplateResponse,
  ReviewSpeechDeleteResponse,
  ReviewCueListResponse,
  ReviewSpeechListResponse,
  ProactiveToggleRequest,
  ProactiveToggleResponse,
  StreamerStatusResponse,
  StreamerTestDecisionRequest,
  StreamerTestDecisionResponse,
  TriggerProactiveRequest,
  TriggerProactiveResponse,
  LiveSessionListResponse,
  SessionResidualResponse,
  SessionKind,
  SessionRundownExecutionResponse,
  SessionTimelineResponse,
  SessionReviewResponse,
  SettleListResponse,
  ViewerListResponse,
  ViewerInsights,
  VisionMonitorsResponse,
  VisionPreviewResponse,
  ViewerDetail,
  ViewerDialogueResponse,
  ViewerContributions,
  ViewerSessionsResponse,
  PendingViewersResponse,
  ViewerIdentityActionResponse,
  ViewerDeleteResponse,
  ForgetListCountResponse,
  ForgetListRemoveResponse,
  LogsQueryResponse,
  AvatarDebugStatusResponse,
  AvatarDebugTriggerRequest,
  AvatarDebugTriggerResponse,
  AvatarTuningProfile,
  AvatarTuningRecipe,
  AvatarTuningState,
  ExpressionSheetState,
  EmbeddingRuntimeStatus,
} from '@/types';
import type { BackgroundTask } from '@/types/events';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 系统

export const systemApi = {
  getStatus: () => api.get<SystemStatusResponse>('/system/status'),
  getHealth: () => api.get<SystemHealthResponse>('/system/health'),
  fetchSystemHealth: () => api.get<SystemHealthWallResponse>('/system/health'),
  getChangelog: () => api.get<ChangelogResponse>('/system/changelog'),
};

// 组件
//
// 后端 `/api/v1/components` 按分组 `collectors / agents / tools` 返回组件清单；
// 控制端点路径参数为 `group`。
export const componentApi = {
  getAll: () => api.get<ComponentListResponse>('/components'),
  control: (group: string, name: string, request: ComponentControlRequest) =>
    api.post<ComponentControlResponse>(`/components/${group}/${name}/control`, request),
};

// 配置
//
// 后端 `/api/v1/config` 返回各配置文件 scope 展平的合并视图（敏感字段以
// 占位文案遮蔽）；`/api/v1/config/schema` 返回按文件归组的 groups。设置页
// 的读写走 stores/settings.ts（pending 队列流）；此处暴露重启触发、合并
// 视图读取，以及小部件管理页等页面直接使用的批量原子写回入口。
export const configApi = {
  restart: () => api.post<ConfigUpdateResponse>('/config/restart'),
  getConfig: () => api.get<ConfigGetResponse>('/config'),
  // 批量原子写回：全成或整批回退（后端保证）；requires_restart 表示改动须重启生效
  saveBatch: (changes: { key: string; value: unknown }[]) =>
    api.post<ConfigBatchSaveResponse>('/config/batch', { changes }),
};

// 调试注入
//
// `injectMessage` 发布 `room.message.danmaku` 走真实弹幕链路（消息写入
// `live` 会话，主播 Agent 决策历史可读）。字幕 / TTS 直测端点不碰业务
// 链（无 LLM / 采集器），直接驱动基础设施；TTS 试说为同步长请求。
export const debugApi = {
  injectMessage: (request: InjectMessageRequest) =>
    api.post<InjectMessageResponse>('/debug/inject-message', request),
  getEventBusStats: () => api.get<EventBusStatsResponse>('/debug/event-bus/stats'),
  testSubtitle: (request: SubtitleTestRequest) =>
    api.post<SubtitleTestResponse>('/debug/subtitle/test', request),
  clearSubtitle: () => api.post<SubtitleClearResponse>('/debug/subtitle/clear'),
  getSubtitleStatus: () => api.get<SubtitleStatusResponse>('/debug/subtitle/status'),
  testTts: (request: TtsTestRequest) =>
    api.post<TtsTestResponse>('/debug/tts/test', request, { timeout: 60000 }),
  // 台前重播：同文本重合成（发言卡"重播"按钮数据面），走 handle_speech 完整生产链路
  sayTts: (request: TtsSayRequest) =>
    api.post<TtsSayResponse>('/debug/tts/say', request, { timeout: 60000 }),
  getTtsStatus: () => api.get<TtsStatusResponse>('/debug/tts/status'),
};

// LLM
export const llmApi = {
  getUsage: () => api.get<Record<string, LLMUsageStats>>('/llm/usage'),
  // 每模型最近一次调用的上下文分段占用（"上下文水位"悬停明细数据源）
  getContextBreakdown: () => api.get<Record<string, LLMContextBreakdown>>('/llm/context-breakdown'),
  // 按请求 ID 取单次调用的分段占用（时间线决策卡水位胶囊悬停明细）；无分段返回 null
  getContextBreakdownByRequest: (requestId: string) =>
    api.get<LLMContextBreakdown | null>(`/llm/context-breakdown/${encodeURIComponent(requestId)}`),
  // 模型所属平台的余额 / 套餐额度（上下文面板 Usage 区）；平台不支持或查询失败返回 null
  getProviderQuota: (modelName: string) =>
    api.get<LLMProviderQuota | null>('/llm/quota', { params: { model_name: modelName } }),
  getUsageSummary: () => api.get<LLMUsageSummary>('/llm/usage/summary'),
  // 语义检索眼下实际开没开（模型页语义检索区的状态标签）；改了配置要重启才变
  getEmbeddingStatus: () => api.get<EmbeddingRuntimeStatus>('/llm/embedding'),
  getUsageTrends: (days: number) =>
    api.get<LLMUsageTrendsResponse>('/llm/usage/trends', { params: { days } }),
  getHistory: (params: LLMHistoryQueryParams) =>
    api.get<LLMHistoryResponse>('/llm/history', { params }),
  getHistoryModels: () => api.get<string[]>('/llm/history/models'),
  /** 用途筛选候选：历史里出现过的用途名全量（去重升序） */
  getHistoryProfiles: () => api.get<string[]>('/llm/history/profiles'),
  getStatistics: (params?: { start_time?: number; end_time?: number }) =>
    api.get<LLMHistoryStatistics>('/llm/history/statistics', { params }),
  getRequestById: (requestId: string) => api.get<LLMRequestHistory>(`/llm/history/${requestId}`),
  /** 批量统计（仅 usage/model 列）：时间线统计胶囊合批懒取专用，避免逐卡取详情回传整段请求正文 */
  getRequestStats: (requestIds: string[]) =>
    api.post<LLMRequestStatsBatch>('/llm/history/stats', { request_ids: requestIds }),
  // 模型连通性测试（模型注册表"测试"按钮）：发一次最小聊天请求验证端点/密钥/模型，
  // 业务级失败以 ok=false 携原因返回；同步长请求，放宽 timeout 并由调用方 loading 防连点
  testModel: (request: LLMModelTestRequest) =>
    api.post<LLMModelTestResponse>('/llm/models/test', request, { timeout: 60000 }),
};

// Tools（工具清单 + 提供者分类面板）
// 提供者开关写回 tools.toml 后需重启应用生效（工具注册发生在组合根装配期）。
export const toolsApi = {
  list: () => api.get<ToolsView>('/tools'),
  listCategories: () => api.get<ToolCategoriesView>('/tools/categories'),
  controlProvider: (category: string, key: string, action: ToolProviderControlAction) =>
    api.post<ToolProviderControlResponse>(`/tools/categories/${category}/${key}/control`, {
      action,
    }),
  controlTool: (name: string, action: ToolProviderControlAction) =>
    api.post<ToolControlResponse>(`/tools/${name}/control`, { action }),
  // 调试调用：与 Agent 同路径经 registry 真实执行（full_name 为注册表调用键）
  invoke: (fullName: string, args: Record<string, unknown>) =>
    api.post<ToolInvokeResult>(`/tools/${encodeURIComponent(fullName)}/invoke`, {
      arguments: args,
    }),
  reconnectProvider: (providerId: string) =>
    api.post<ToolReconnectResponse>(`/tools/providers/${providerId}/reconnect`),
};

// Agent 控制面（运行态观测 + 框架级控制）
//
// `GET /agents`：已注册 Agent 的运行状态列表（state / heartbeat_ms / is_alive /
// restart_count / enabled）。`POST /agents/{name}/control`：pause / resume
// 即时生效；shutdown / restart 属高风险动作，须携带 confirm: true，
// 受理后返回 202（响应体与 200 同形）。
export const agentsApi = {
  listAgents: () => api.get<AgentListResponse>('/agents'),
  getAgentState: (name: string) => api.get<AgentState>(`/agents/${name}/state`),
  controlAgent: (name: string, action: AgentControlActionType, confirm?: boolean) =>
    api.post<AgentControlResponse>(`/agents/${name}/control`, { action, confirm }),
  /** 递话：纯文本留言（不派新任务；目标拒收 409，Agent 不存在 404） */
  promptAgent: (name: string, content: string) =>
    api.post<AgentPromptResponse>(`/agents/${name}/prompt`, { content }),
  /** 直派：派新任务并登记到任务列表（任务卡可见；目标拒收 409） */
  delegateAgent: (name: string, instruction: string) =>
    api.post<AgentDelegateResponse>(`/agents/${name}/delegate`, { instruction }),
  /** 硬取消：清除任务追踪、状态置为 cancelled、并通知 Agent 停止执行（任务未知/已终态 404） */
  cancelAgentTask: (name: string, taskId: string, reason?: string) =>
    api.post<AgentCancelTaskResponse>(
      `/agents/${name}/tasks/${taskId}/cancel`,
      reason ? { reason } : {},
    ),
  /** 急停：停掉该 Agent 当前所有在跑目标并注入叫停理由（默认不撤委派单；不支持急停 409） */
  stopAgent: (name: string, reason = '', cancelDelegations = false) =>
    api.post<AgentStopResponse>(`/agents/${name}/stop`, {
      reason,
      cancel_delegations: cancelDelegations,
    }),
  /** 备忘录/待办快照（只读观测：minecraft = notebook/todos/reports 三元组） */
  getAgentMemo: (name: string) => api.get<AgentMemoResponse>(`/agents/${name}/memo`),
};

// 任务卡快照（进行中任务列表 + 已完结事件聚合）
export const tasksApi = {
  listTasks: () => api.get<TaskSnapshotResponse>('/tasks'),
};

// 后台任务（底栏数据面：后端进程内在跑长任务的只读快照）
//
// `GET /background-tasks`：返回注册表当前全部任务行（终态任务已从注册表
// 移除，不会出现在响应里）；运行中变化经 `background.task.changed` WS 事件
// 增量推送，本查询口只在页面加载时恢复状态用。
export const backgroundTasksApi = {
  list: () => api.get<BackgroundTask[]>('/background-tasks'),
};

// Simulator 控制面（观众模拟器：generate 生成 / replay 回放）
//
// 控制 SimulatorService 的装配/启停与状态查询，以及运行时数据（常驻人设 /
// 礼物目录 CRUD、回放日期选择）。enabled=false 时 status 仍返回（不抛
// 404）；enable/disable 运行期装配与拆卸（免重启，配置写盘持久化）。
export const simulatorApi = {
  getStatus: () => api.get<SimulatorStatus>('/simulator/status'),
  enable: () => api.post<SimulatorAssemblyResponse>('/simulator/enable'),
  disable: () => api.post<SimulatorAssemblyResponse>('/simulator/disable'),
  start: (replayDate?: string) =>
    api.post<SimulatorControlResponse>(
      '/simulator/start',
      replayDate ? { replay_date: replayDate } : {},
    ),
  stop: () => api.post<SimulatorControlResponse>('/simulator/stop'),
  listReplayDates: () => api.get<ReplayDatesResponse>('/simulator/replay/dates'),

  listPersonas: () => api.get<PersonaListResponse>('/simulator/personas'),
  createPersona: (payload: Partial<SimPersona>) =>
    api.post<PersonaCreateResponse>('/simulator/personas', payload),
  updatePersona: (userId: string, payload: Partial<SimPersona>) =>
    api.patch<PersonaOperationResponse>(`/simulator/personas/${userId}`, payload),
  deletePersona: (userId: string) =>
    api.delete<PersonaOperationResponse>(`/simulator/personas/${userId}`),

  listGifts: () => api.get<GiftListResponse>('/simulator/gifts'),
  createGift: (payload: Partial<SimGift>) =>
    api.post<GiftCreateResponse>('/simulator/gifts', payload),
  updateGift: (giftId: string, payload: Partial<SimGift>) =>
    api.patch<GiftOperationResponse>(`/simulator/gifts/${giftId}`, payload),
  deleteGift: (giftId: string) => api.delete<GiftOperationResponse>(`/simulator/gifts/${giftId}`),
};

// 直播场次（直播控制台）
//
// `GET /live-sessions`：场次列表（倒序 + 消息数）。
// `POST /live-sessions/open`：开启新场次（进行中场次自动结束）。
// `POST /live-sessions/{id}/close`：结束场次（空场次整行丢弃）。
// `PATCH /live-sessions/{id}`：重命名场次（title 空=清除标题，列表回落"场次 #id"）。
// `PATCH /live-sessions/{id}/kind`：更正已结束场次的类型（正式 / 彩排；进行中 409）。
// `DELETE /live-sessions/{id}`：删除场次（级联清明细）。
// `GET /live-sessions/{id}/timeline`：单场时间线回看（明细行 + 事件侧合并；
// before_timestamp_ms 游标向前翻页，响应 has_more / events_pruned 指示续翻与降级）。
// `POST /live-sessions/open`：带 component_plan（期望运行全集）时，开场后把组件
// 运行态与配置态对齐到该清单（开播引导弹窗），逐项结果随 plan_report 回传。
export const liveSessionsApi = {
  list: (params?: { source?: string; kind?: SessionKind; q?: string; limit?: number }) =>
    api.get<LiveSessionListResponse>('/live-sessions', { params }),
  // 开场必须声明类型（正式 / 彩排）
  open: (payload: { kind: SessionKind; title?: string; component_plan?: ComponentRef[] }) =>
    api.post<SessionOpenResponse>('/live-sessions/open', payload),
  // 待决残留场次（上次进程未正常退出留下的未结账场次，等用户续播或收尾）
  listResidual: () => api.get<SessionResidualResponse>('/live-sessions/residual'),
  resume: (id: number) => api.post<SessionActionResponse>(`/live-sessions/${id}/resume`),
  discardResidual: (id: number) => api.post<SessionActionResponse>(`/live-sessions/${id}/discard`),
  close: (id: number) => api.post<SessionActionResponse>(`/live-sessions/${id}/close`),
  rename: (id: number, title?: string) =>
    api.patch<SessionActionResponse>(`/live-sessions/${id}`, { title }),
  setKind: (id: number, kind: SessionKind) =>
    api.patch<SessionActionResponse>(`/live-sessions/${id}/kind`, { kind }),
  remove: (id: number) => api.delete<SessionActionResponse>(`/live-sessions/${id}`),
  timeline: (id: number, params?: { limit?: number; before_timestamp_ms?: number }) =>
    api.get<SessionTimelineResponse>(`/live-sessions/${id}/timeline`, { params }),
  // 单场流程单执行回看（rundown_transitions 事实行重放：各环节实际停留 vs 预计）
  rundownExecution: (id: number) =>
    api.get<SessionRundownExecutionResponse>(`/live-sessions/${id}/rundown-execution`),
};

// 事件历史（游标续传；历史范围由内存缓冲扩展到落盘文件）
//
// `GET /events?since_id=`：返回游标之后的事件缺口（断线/刷新后由 store 调用补齐，
// 只读内存环形缓冲）。
// `GET /events?start_ms=&before_timestamp_ms=&take=`：时间窗查询（内存环形 ∪
// logs/events 落盘文件），供事件页"加载更早/时间跳转"；窗口起点缺省=本次启动。
export const eventsApi = {
  list: (params?: {
    since_id?: string;
    limit?: number;
    start_ms?: number;
    before_timestamp_ms?: number;
    take?: 'newest' | 'oldest';
  }) => api.get<EventListResponse>('/events', { params }),
};

// 日志落盘历史（日志查看器"加载更早/时间跳转/服务端搜索"数据源）
//
// `GET /logs`：扫 logs/webui 按天 JSONL（默认仅 INFO 及以上，DEBUG 只在实时
// 尾部缓冲）；窗口起点缺省=本次启动时刻，显式传 start_ms 可跨启动跳转（复盘）。
export const logsApi = {
  list: (params?: {
    start_ms?: number;
    end_ms?: number;
    before_timestamp_ms?: number;
    levels?: string;
    modules?: string;
    q?: string;
    take?: 'newest' | 'oldest';
    limit?: number;
  }) => api.get<LogsQueryResponse>('/logs', { params }),
};

// 观众（列表 / 分析 / 档案 / 对话 / 贡献 / 场次 / 身份人工处理）
//
// `GET /viewers`：观众列表（搜索/排序/分页，total 为命中搜索的全计数，
// 首页"观众总数"也取 total）。
// `GET /viewers/insights`：活跃分桶 + 回复覆盖 + 按天弹幕量（互动分析页）。
// `GET /viewers/{viewerId}...`：单观众档案与明细聚合（详情页三 tab 的数据面），
// 按内部观众 ID 定位——同一位观众可能有账号 ID 与开放平台 ID 两种外部 ID。
// `GET /viewers/pending` + `POST /viewers/{viewerId}/merge|keep|unlink`：
// 与老观众同名、待人工确认的观众，及合并 / 保持新建 / 取消关联。
export const viewersApi = {
  list: (params?: { search?: string; order_by?: string; limit?: number; offset?: number }) =>
    api.get<ViewerListResponse>('/viewers', { params }),
  insights: (params?: { days?: number }) =>
    api.get<ViewerInsights>('/viewers/insights', { params }),
  detail: (viewerId: number) => api.get<ViewerDetail>(`/viewers/${viewerId}`),
  messages: (viewerId: number, params?: { before_timestamp_ms?: number; limit?: number }) =>
    api.get<ViewerDialogueResponse>(`/viewers/${viewerId}/messages`, { params }),
  contributions: (viewerId: number, params?: { limit?: number }) =>
    api.get<ViewerContributions>(`/viewers/${viewerId}/contributions`, { params }),
  sessions: (viewerId: number) => api.get<ViewerSessionsResponse>(`/viewers/${viewerId}/sessions`),
  pending: (params?: { limit?: number; offset?: number }) =>
    api.get<PendingViewersResponse>('/viewers/pending', { params }),
  merge: (viewerId: number, targetViewerId: number) =>
    api.post<ViewerIdentityActionResponse>(`/viewers/${viewerId}/merge`, {
      target_viewer_id: targetViewerId,
    }),
  keepSeparate: (viewerId: number) =>
    api.post<ViewerIdentityActionResponse>(`/viewers/${viewerId}/keep`),
  unlink: (viewerId: number, idKind: 'account' | 'open') =>
    api.post<ViewerIdentityActionResponse>(`/viewers/${viewerId}/unlink`, { id_kind: idKind }),
  retractForget: (viewerId: number) =>
    api.post<ViewerIdentityActionResponse>(`/viewers/${viewerId}/retract-forget`),
  remove: (viewerId: number, addToForgetList = false) =>
    api.delete<ViewerDeleteResponse>(`/viewers/${viewerId}`, {
      params: { add_to_forget_list: addToForgetList },
    }),
  getForgetListCount: () => api.get<ForgetListCountResponse>('/viewers/forget-list'),
  removeFromForgetList: (idKind: 'account' | 'open', externalId: string) =>
    api.post<ForgetListRemoveResponse>('/viewers/forget-list/remove', {
      id_kind: idKind,
      external_id: externalId,
    }),
};

// Streamer 测试台（主播发言调试）
//
// `GET /streamer/status`：主播 Agent 状态 + 运行统计 + 配置摘要（agent 未注册时
// available=false，前端按空态渲染）。
// `POST /streamer/test-decision`：手动驱动一次两阶段决策（Planner → Replyer），
// 同步长请求（两段 LLM，典型 10~30s），单独放宽 timeout 至 120s。
// `POST /streamer/trigger-proactive`：真实限流链路的外部主动发言触发（置位语义，
// 下个 flush tick 经限流判定后才可能开口）。
export const streamerApi = {
  getStatus: () => api.get<StreamerStatusResponse>('/streamer/status'),
  toggleProactive: (request: ProactiveToggleRequest) =>
    api.post<ProactiveToggleResponse>('/streamer/proactive-toggle', request),
  testDecision: (request: StreamerTestDecisionRequest) =>
    api.post<StreamerTestDecisionResponse>('/streamer/test-decision', request, {
      timeout: 120000,
    }),
  triggerProactive: (request: TriggerProactiveRequest) =>
    api.post<TriggerProactiveResponse>('/streamer/trigger-proactive', request),
};

// Vision（视觉捕获：显示器枚举 + 预览叠框）
//
// `GET /vision/monitors` 列显示器（mss 枚举；含 index/left/top/width/height/
// is_primary）；`GET /vision/preview` 抓一帧并按 region 在图上叠红框。
// 后端不调 VLM、不缓存、不轮询、不视频流——纯抓帧。
export const visionApi = {
  listMonitors: () => api.get<VisionMonitorsResponse>('/vision/monitors'),
  preview: (params: { monitor_index: number; region?: string; max_width?: number }) =>
    api.get<VisionPreviewResponse>('/vision/preview', { params }),
};

// 虚拟形象调试台（形象表情调试）
//
// `GET /avatar/debug/status`：四平台连接态 / 当前表情 / 动作目录 / 统计摘要，
// 全部走平台无关契约面（同语义跨平台同名工具）；未装配平台按 connected=false 降级列出。
// `POST /avatar/debug/trigger`：转发触发表情（17 情绪 + 强度）或参数直调
// （仅 VTS）；前置不满足返回 404/409/400，业务拒绝以 success=false 的 200 回传。
export const avatarApi = {
  getDebugStatus: () => api.get<AvatarDebugStatusResponse>('/avatar/debug/status'),
  triggerDebug: (request: AvatarDebugTriggerRequest) =>
    api.post<AvatarDebugTriggerResponse>('/avatar/debug/trigger', request),
  // 表情差分小部件页轮询：未启用 / 未配置目录时 404
  getExpressionSheetState: () => api.get<ExpressionSheetState>('/avatar/expression_sheet/state'),
};

// 形象调校（看着形象实时调面捕合成器）
//
// 除 `save` 外的改动都只在运行中生效、不写文件：调的时候可以放心乱拖，重启即回到
// avatar.toml 里的值；`save` 把当前生效的调参、配方覆盖与平台校准项一次写回。
// 面捕合成器没装配时所有端点 503。
export const avatarTuningApi = {
  getProfile: () => api.get<AvatarTuningProfile>('/avatar/tuning'),
  getState: () => api.get<AvatarTuningState>('/avatar/tuning/state'),
  patchConfig: (changes: Record<string, unknown>) =>
    api.patch<{ config: Record<string, unknown> }>('/avatar/tuning/config', { changes }),
  putRecipe: (emotion: string, offsets: Record<string, number>, motion: number | null) =>
    api.put<AvatarTuningRecipe>(`/avatar/tuning/recipes/${encodeURIComponent(emotion)}`, {
      offsets,
      motion,
    }),
  resetRecipe: (emotion: string) =>
    api.delete<AvatarTuningRecipe>(`/avatar/tuning/recipes/${encodeURIComponent(emotion)}`),
  preview: (emotion: string, intensity: number, seconds: number) =>
    api.post<{ outcome: string }>('/avatar/tuning/preview', { emotion, intensity, seconds }),
  look: (x: number, y: number, durationMs = 1500) =>
    api.post('/avatar/tuning/look', { x, y, duration_ms: durationMs }),
  act: (action: string) => api.post<{ success: boolean }>('/avatar/tuning/act', { action }),
  setManual: (values: Record<string, number>, holdMs: number) =>
    api.put('/avatar/tuning/manual', { values, hold_ms: holdMs }),
  clearManual: () => api.delete('/avatar/tuning/manual'),
  calibrate: (driver: string, values: Record<string, number>) =>
    api.patch<{ values: Record<string, number> }>(
      `/avatar/tuning/calibration/${encodeURIComponent(driver)}`,
      { values },
    ),
  save: () =>
    api.post<{ success: boolean; saved: number; target_file: string | null }>(
      '/avatar/tuning/save',
    ),
};

// Rundown（流程单编排页）
//
// `GET /rundown/state`：当前流程单运行时快照（available / snapshot / transitions /
// segments / config）。available=false 时 snapshot=null，前端按不可用态渲染。
// `POST /rundown/control`：手动控制（pause / resume / next / goto / restart，by="human"），
// 返回最新 snapshot；前端只在收到响应后做错误提示，正常状态由后端通过
// `rundown.changed` 事件推上来。
export const rundownApi = {
  getState: () => api.get<RundownStateResponse>('/rundown/state'),
  control: (request: RundownControlRequest) =>
    api.post<RundownControlResponse>('/rundown/control', request),

  // 流程单库（列表 / 模板 / upsert / 删除 / 复制 / 设为当前）
  //
  // upsert 保存的流程单正是直播运行中的那份时，后端会写穿运行态
  // （进度按环节 id 对齐）；新建不带 id（服务端生成）。activate 落盘配置
  // 并对在线主播 Agent 热切运行态，成功文案以后端 message 为准。
  listRundowns: () => api.get<RundownListResponse>('/rundowns'),
  getTemplate: () => api.get<RundownTemplateResponse>('/rundowns/template'),
  upsert: (definition: RundownDefinition) =>
    api.post<RundownMutateResponse>('/rundowns', definition),
  remove: (rundownId: string) => api.delete<RundownMutateResponse>(`/rundowns/${rundownId}`),
  duplicate: (rundownId: string) =>
    api.post<RundownMutateResponse>(`/rundowns/${rundownId}/duplicate`),
  activate: (rundownId: string) =>
    api.post<RundownMutateResponse>(`/rundowns/${rundownId}/activate`),
};

// 记忆账本（承诺 / 场次档案 / 下播整理状态）
//
// 每场正式场收播后，主播 Agent 把这一场整理成承诺账本与场次档案；本组接口只做
// 查看与人工纠错。承诺按状态（open / closed / void，空或 all 不过滤）、观众、游戏
// 筛选，total / open_count / void_count 随列表返回；手建、标了结、判误记、删除各
// 一个端点。档案按场次读取，可删单行。整理状态返回最近正式场（失败原因与未登记
// 清单在这里给页面看），resettle 把重试次数清零让下一次调度重新整理该场（重跑会替换该场
// 档案行与本场未了结的承诺，运营者已了结或判误记的保留）。
export const ledgerApi = {
  listCommitments: (params?: {
    status?: CommitmentStatus | 'all';
    viewer_id?: number;
    game?: string;
    limit?: number;
    offset?: number;
  }) => api.get<CommitmentListResponse>('/memory/commitments', { params }),
  createCommitment: (payload: CommitmentCreatePayload) =>
    api.post<CommitmentItem>('/memory/commitments', payload),
  // 标了结：已了结或判误记的承诺返回 409（行内提示，不影响其余列表）
  closeCommitment: (id: number, payload: CommitmentClosePayload = {}) =>
    api.post<LedgerMutationResponse>(`/memory/commitments/${id}/close`, payload),
  // 判误记：留一行墓碑，之后整理再遇到同一件事不会重新登记
  voidCommitment: (id: number) =>
    api.post<LedgerMutationResponse>(`/memory/commitments/${id}/void`),
  deleteCommitment: (id: number) => api.delete<LedgerMutationResponse>(`/memory/commitments/${id}`),
  listArchive: (liveSessionId: number) =>
    api.get<ArchiveListResponse>(`/memory/archive/${liveSessionId}`),
  deleteArchiveLine: (lineId: number) =>
    api.delete<LedgerMutationResponse>(`/memory/archive/lines/${lineId}`),
  // 记忆页审核：改标题、标稳定会锁住这一行（之后的整理只能保留）；放回、新增进最新一份快照
  editArchiveTitle: (lineId: number, text: string) =>
    api.put<LedgerMutationResponse>(`/memory/archive/lines/${lineId}/title`, { text }),
  setArchiveLasting: (lineId: number, lasting: boolean) =>
    api.put<LedgerMutationResponse>(`/memory/archive/lines/${lineId}/lasting`, { lasting }),
  pinArchiveLine: (lineId: number) =>
    api.post<LedgerMutationResponse>(`/memory/archive/lines/${lineId}/pin`),
  // 取消放回：改回普通行，还在快照里，之后按规则取舍
  unpinArchiveLine: (lineId: number) =>
    api.post<LedgerMutationResponse>(`/memory/archive/lines/${lineId}/unpin`),
  createArchiveLine: (liveSessionId: number, payload: ArchiveLineCreatePayload) =>
    api.post<LedgerMutationResponse>(`/memory/archive/${liveSessionId}/lines`, payload),
  markReviewed: (liveSessionId: number) =>
    api.post<LedgerMutationResponse>(`/memory/settle/${liveSessionId}/reviewed`),
  // 观众候选审核区：批准前对她不可见；批准观众定下的写成档案行，她说欠着的登记成承诺
  listCandidates: (status: CandidateStatus | 'all' = 'pending') =>
    api.get<CandidateListResponse>('/memory/candidates', { params: { status } }),
  approveCandidate: (candidateId: number, payload: CandidateApprovePayload = {}) =>
    api.post<CandidateItem>(`/memory/candidates/${candidateId}/approve`, payload),
  rejectCandidate: (candidateId: number) =>
    api.post<LedgerMutationResponse>(`/memory/candidates/${candidateId}/reject`),
  getSettleState: () => api.get<SettleListResponse>('/memory/settle'),
  // 重新整理：重试次数清零并清掉上次失败原因，下一次调度会再挑到这一场
  resettle: (liveSessionId: number) =>
    api.post<LedgerMutationResponse>(`/memory/settle/${liveSessionId}/resettle`),
};

// 游戏里的人（记忆页）
//
// 她在游戏里遇到过的玩家：列表带曾用名、互动条数和运营者确认过的关联观众。对号只由运营者
// 确认，同名不会自动关联；关联只用来连带删除（删观众或 ta 发"忘了我"时一起删这位玩家），
// 不告诉她。删玩家时他的互动、公屏聊天、对他的承诺一起删，UUID 进不记录名单。
export const playersApi = {
  list: (params?: { game?: string }) => api.get<PlayerListResponse>('/memory/players', { params }),
  linkViewer: (playerId: number, payload: PlayerLinkPayload) =>
    api.post<LedgerMutationResponse>(`/memory/players/${playerId}/links`, payload),
  unlinkViewer: (playerId: number, viewerId: number) =>
    api.delete<LedgerMutationResponse>(`/memory/players/${playerId}/links/${viewerId}`),
  deletePlayer: (playerId: number) =>
    api.delete<LedgerMutationResponse>(`/memory/players/${playerId}`),
  // 玩家自述审核：批准前她看不到；批准后只进这位玩家的玩家卡，不进开播简报
  listCandidates: (status: CandidateStatus | 'all' = 'pending') =>
    api.get<PlayerCandidateListResponse>('/memory/players/candidates', { params: { status } }),
  approveCandidate: (candidateId: number, payload: { title?: string } = {}) =>
    api.post<PlayerCandidateItem>(`/memory/players/candidates/${candidateId}/approve`, payload),
  rejectCandidate: (candidateId: number) =>
    api.post<LedgerMutationResponse>(`/memory/players/candidates/${candidateId}/reject`),
  // 批错了：撤销批准回到待审，或整条删掉
  revokeCandidate: (candidateId: number) =>
    api.post<PlayerCandidateItem>(`/memory/players/candidates/${candidateId}/revoke`),
  deleteCandidate: (candidateId: number) =>
    api.delete<LedgerMutationResponse>(`/memory/players/candidates/${candidateId}`),
};

// 场次复盘（只读聚合 + 本场台词 + 数据包导出）
//
// `GET /review?ids=`：单场或多选合并聚合（缺省=最新已结束场次，后端定）。
// `GET /review/speech?session_id=`：本场台词（时间正序，带未送达标记与档案引用）。
// `DELETE /review/speech/{chatId}`：删一句台词——引用它的档案行一起删，对它的
// 承诺保留但清空原话；404 = 行不存在，503 = 仓储未装配。
// `GET /review/export?ids=`：数据包 zip（db 快照 + 日志原样 + manifest），
// 浏览器下载走原生 <a> 直链（带凭证的 axios blob 需另行拼装，此处 cookie
// 同源场景直接跳转即可）。
export const reviewApi = {
  get: (ids?: number[]) =>
    api.get<SessionReviewResponse>('/review', {
      params: ids?.length ? { ids: ids.join(',') } : {},
    }),
  listSpeech: (sessionId: number) =>
    api.get<ReviewSpeechListResponse>('/review/speech', {
      params: { session_id: sessionId },
    }),
  deleteSpeech: (chatId: number) =>
    api.delete<ReviewSpeechDeleteResponse>(`/review/speech/${chatId}`),
  listCues: (sessionId: number) =>
    api.get<ReviewCueListResponse>('/review/cues', {
      params: { session_id: sessionId },
    }),
  exportUrl: (ids: number[]): string => {
    const query = ids.length ? `?ids=${ids.join(',')}` : '';
    return `/api/v1/review/export${query}`;
  },
};

export default api;
