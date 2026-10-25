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
  SystemStatusResponse,
  SystemHealthResponse,
  SystemHealthWallResponse,
  ChangelogResponse,
  ConfigGetResponse,
  ConfigBatchSaveResponse,
  EventListResponse,
  ComponentListResponse,
  ComponentControlRequest,
  ComponentControlResponse,
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
  LLMRequestHistory,
  LLMRequestStatsBatch,
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
  GiftListResponse,
  GiftCreateResponse,
  GiftOperationResponse,
  SimGift,
  SessionOpenResponse,
  SessionActionResponse,
  TaskSnapshotResponse,
  SimulatorControlResponse,
  RundownStateResponse,
  RundownControlRequest,
  RundownControlResponse,
  RundownDefinition,
  RundownListResponse,
  RundownMutateResponse,
  RundownTemplateResponse,
  ProactiveToggleRequest,
  ProactiveToggleResponse,
  StreamerStatusResponse,
  StreamerTestDecisionRequest,
  StreamerTestDecisionResponse,
  TriggerProactiveRequest,
  TriggerProactiveResponse,
  LiveSessionListResponse,
  SessionRundownExecutionResponse,
  SessionTimelineResponse,
  SessionReviewResponse,
  ViewerListResponse,
  ViewerInsights,
  VisionMonitorsResponse,
  VisionPreviewResponse,
  ViewerDetail,
  ViewerDialogueResponse,
  ViewerContributions,
  ViewerSessionsResponse,
  ViewerProfileListResponse,
  ViewerProfileUpdatePayload,
  MemoryMutationResponse,
  MemoryStatsResponse,
  LogsQueryResponse,
  AvatarDebugStatusResponse,
  AvatarDebugTriggerRequest,
  AvatarDebugTriggerResponse,
} from '@/types';

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
  getUsageTrends: (days: number) =>
    api.get<LLMUsageTrendsResponse>('/llm/usage/trends', { params: { days } }),
  getHistory: (params: LLMHistoryQueryParams) =>
    api.get<LLMHistoryResponse>('/llm/history', { params }),
  getHistoryModels: () => api.get<string[]>('/llm/history/models'),
  getStatistics: (params?: { start_time?: number; end_time?: number }) =>
    api.get<LLMHistoryStatistics>('/llm/history/statistics', { params }),
  getRequestById: (requestId: string) => api.get<LLMRequestHistory>(`/llm/history/${requestId}`),
  /** 批量统计（仅 usage/model 列）：时间线统计胶囊合批懒取专用，避免逐卡取详情回传整段请求正文 */
  getRequestStats: (requestIds: string[]) =>
    api.post<LLMRequestStatsBatch>('/llm/history/stats', { request_ids: requestIds }),
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
// `GET /agents`：已注册 Agent 名册（state / heartbeat_ms / is_alive /
// restart_count / enabled）。`POST /agents/{name}/control`：pause / resume
// 即时生效；shutdown / restart 属高风险动作，须携带 confirm: true，
// 受理后返回 202（响应体与 200 同形）。
export const agentsApi = {
  listAgents: () => api.get<AgentListResponse>('/agents'),
  getAgentState: (name: string) => api.get<AgentState>(`/agents/${name}/state`),
  controlAgent: (name: string, action: AgentControlActionType, confirm?: boolean) =>
    api.post<AgentControlResponse>(`/agents/${name}/control`, { action, confirm }),
  /** 递话：纯文本留言（不派新任务；目标拒收 409，不在名册 404） */
  promptAgent: (name: string, content: string) =>
    api.post<AgentPromptResponse>(`/agents/${name}/prompt`, { content }),
  /** 直派：派新任务登记账本（任务卡可见；目标拒收 409） */
  delegateAgent: (name: string, instruction: string) =>
    api.post<AgentDelegateResponse>(`/agents/${name}/delegate`, { instruction }),
  /** 硬取消：清追踪 + 账面 cancelled + 通知停手（任务未知/已终态 404） */
  cancelAgentTask: (name: string, taskId: string) =>
    api.post<AgentCancelTaskResponse>(`/agents/${name}/tasks/${taskId}/cancel`),
  /** 备忘录/待办快照（只读观测：主播 = notebook；minecraft = notebook/todos/reports 三元组） */
  getAgentMemo: (name: string) => api.get<AgentMemoResponse>(`/agents/${name}/memo`),
};

// 任务卡快照（进行中账本 + 已完结事件聚合）
export const tasksApi = {
  listTasks: () => api.get<TaskSnapshotResponse>('/tasks'),
};

// Simulator 控制面（模拟器：generate 生成 / replay 回放）
//
// 控制 SimulatorService 的启停与状态查询，以及运行时数据（常驻人设 /
// 礼物目录 CRUD、回放日期选择）。enabled=false 时 status 仍返回（不抛
// 404）；start 会拒绝并提示需要修改配置后重启。
export const simulatorApi = {
  getStatus: () => api.get<SimulatorStatus>('/simulator/status'),
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
// `DELETE /live-sessions/{id}`：删除场次（级联清明细）。
// `GET /live-sessions/{id}/timeline`：单场时间线回看（明细行 + 事件侧合并；
// before_timestamp_ms 游标向前翻页，响应 has_more / events_pruned 指示续翻与降级）。
export const liveSessionsApi = {
  list: (params?: { source?: string; q?: string; limit?: number }) =>
    api.get<LiveSessionListResponse>('/live-sessions', { params }),
  open: (payload?: { title?: string }) =>
    api.post<SessionOpenResponse>('/live-sessions/open', payload ?? {}),
  close: (id: number) => api.post<SessionActionResponse>(`/live-sessions/${id}/close`),
  rename: (id: number, title?: string) =>
    api.patch<SessionActionResponse>(`/live-sessions/${id}`, { title }),
  remove: (id: number) => api.delete<SessionActionResponse>(`/live-sessions/${id}`),
  timeline: (id: number, params?: { limit?: number; before_timestamp_ms?: number }) =>
    api.get<SessionTimelineResponse>(`/live-sessions/${id}/timeline`, { params }),
  // 单场流程单执行回看（rundown_transitions 事实行重放：各环节实际停留 vs 预计）
  rundownExecution: (id: number) =>
    api.get<SessionRundownExecutionResponse>(`/live-sessions/${id}/rundown-execution`),
};

// 事件历史（游标续传 + 落盘扩容）
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

// 观众（列表 / 分析 / 档案 / 对话 / 贡献 / 场次）
//
// `GET /viewers`：观众列表（搜索/排序/分页，total 为命中搜索的全计数，
// 首页"观众总数"也取 total）。
// `GET /viewers/insights`：活跃分桶 + 回复覆盖 + 按天弹幕量（互动分析页）。
// `GET /viewers/{userId}...`：单观众档案与明细聚合（详情页三 tab 的数据面）。
export const viewersApi = {
  list: (params?: { search?: string; order_by?: string; limit?: number; offset?: number }) =>
    api.get<ViewerListResponse>('/viewers', { params }),
  insights: (params?: { days?: number }) =>
    api.get<ViewerInsights>('/viewers/insights', { params }),
  detail: (userId: string) => api.get<ViewerDetail>(`/viewers/${encodeURIComponent(userId)}`),
  messages: (userId: string, params?: { before_timestamp_ms?: number; limit?: number }) =>
    api.get<ViewerDialogueResponse>(`/viewers/${encodeURIComponent(userId)}/messages`, { params }),
  contributions: (userId: string, params?: { limit?: number }) =>
    api.get<ViewerContributions>(`/viewers/${encodeURIComponent(userId)}/contributions`, {
      params,
    }),
  sessions: (userId: string) =>
    api.get<ViewerSessionsResponse>(`/viewers/${encodeURIComponent(userId)}/sessions`),
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

// 观众画像管理（viewer_profiles 的查看与纠正接口）
//
// 读写全部经后端 ViewerProfileRepo（常客印象读写）。
// `GET /memory/profiles`：画像列表（搜索 / 分页）。
// `PATCH / DELETE /memory/profiles/{viewerId}`：人工纠正 / 删除印象（按内部观众 ID）。
// `GET /memory/stats`：总量统计（常客数 / 最近更新）。
export const memoryApi = {
  listProfiles: (params?: { search?: string; limit?: number; offset?: number }) =>
    api.get<ViewerProfileListResponse>('/memory/profiles', { params }),
  updateProfile: (viewerId: number, payload: ViewerProfileUpdatePayload) =>
    api.patch<MemoryMutationResponse>(`/memory/profiles/${viewerId}`, payload),
  deleteProfile: (viewerId: number) =>
    api.delete<MemoryMutationResponse>(`/memory/profiles/${viewerId}`),
  getStats: () => api.get<MemoryStatsResponse>('/memory/stats'),
};

// 场次复盘（只读聚合 + 数据包导出）
//
// `GET /review?ids=`：单场或多选合并聚合（缺省=最新已结束场次，后端定）。
// `GET /review/export?ids=`：数据包 zip（db 快照 + 日志原样 + manifest），
// 浏览器下载走原生 <a> 直链（带凭证的 axios blob 需另行拼装，此处 cookie
// 同源场景直接跳转即可）。
export const reviewApi = {
  get: (ids?: number[]) =>
    api.get<SessionReviewResponse>('/review', {
      params: ids?.length ? { ids: ids.join(',') } : {},
    }),
  exportUrl: (ids: number[]): string => {
    const query = ids.length ? `?ids=${ids.join(',')}` : '';
    return `/api/v1/review/export${query}`;
  },
};

export default api;
