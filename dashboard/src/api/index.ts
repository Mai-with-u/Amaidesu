/**
 * Dashboard API 客户端
 *
 * 模拟直播能力控制面：
 * - ``simulatorApi`` → ``/api/v1/simulator/*``：模拟器 SimulatorService
 *   （generate 生成 / replay 回放）
 */

import axios from 'axios';
import type {
  AgentControlActionType,
  AgentControlResponse,
  AgentListResponse,
  AgentState,
  SystemStatusResponse,
  ChangelogResponse,
  ComponentListResponse,
  ComponentControlRequest,
  ComponentControlResponse,
  InjectMessageRequest,
  InjectMessageResponse,
  EventBusStatsResponse,
  ConfigUpdateResponse,
  LLMUsageStats,
  LLMUsageSummary,
  LLMUsageTrendsResponse,
  LLMHistoryQueryParams,
  LLMHistoryResponse,
  LLMHistoryStatistics,
  LLMRequestHistory,
  ToolsView,
  ToolCategoriesView,
  ToolProviderControlAction,
  ToolControlResponse,
  ToolProviderControlResponse,
  ToolReconnectResponse,
  SimulatorStatus,
  SimPersona,
  SimGift,
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
  SessionTimelineResponse,
  ViewerListResponse,
  ViewerInsights,
  VisionMonitorsResponse,
  VisionPreviewResponse,
  ViewerDetail,
  ViewerDialogueResponse,
  ViewerContributions,
  ViewerSessionsResponse,
  WebSocketMessage,
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
  getHealth: () => api.get<{ status: string; timestamp_ms: number }>('/system/health'),
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
// 后端 `/api/v1/config` 返回六文件合并的扁平 dict；`/api/v1/config/schema`
// 返回按文件归组的 groups。读写由 stores/settings.ts 直连（裸 axios 实例），
// 此处仅暴露 AppLayout 顶栏使用的重启触发。
export const configApi = {
  restart: () => api.post<ConfigUpdateResponse>('/config/restart'),
};

// 调试注入
//
// `injectMessage` 发布 `room.message.danmaku` 走真实弹幕链路（消息写入
// `live` 会话，主播 Agent 决策历史可读）。
export const debugApi = {
  injectMessage: (request: InjectMessageRequest) =>
    api.post<InjectMessageResponse>('/debug/inject-message', request),
  getEventBusStats: () => api.get<EventBusStatsResponse>('/debug/event-bus/stats'),
};

// LLM
export const llmApi = {
  getUsage: () => api.get<Record<string, LLMUsageStats>>('/llm/usage'),
  getUsageSummary: () => api.get<LLMUsageSummary>('/llm/usage/summary'),
  getUsageTrends: (days: number) =>
    api.get<LLMUsageTrendsResponse>('/llm/usage/trends', { params: { days } }),
  getHistory: (params: LLMHistoryQueryParams) =>
    api.get<LLMHistoryResponse>('/llm/history', { params }),
  getStatistics: (params?: { start_time?: number; end_time?: number }) =>
    api.get<LLMHistoryStatistics>('/llm/history/statistics', { params }),
  getRequestById: (requestId: string) => api.get<LLMRequestHistory>(`/llm/history/${requestId}`),
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
  listReplayDates: () => api.get<{ dates: string[] }>('/simulator/replay/dates'),

  listPersonas: () =>
    api.get<{ personas: SimPersona[]; is_available: boolean }>('/simulator/personas'),
  createPersona: (payload: Partial<SimPersona>) =>
    api.post<{ success: boolean; message?: string; persona?: SimPersona }>(
      '/simulator/personas',
      payload,
    ),
  updatePersona: (userId: string, payload: Partial<SimPersona>) =>
    api.patch<{ success: boolean; message?: string }>(`/simulator/personas/${userId}`, payload),
  deletePersona: (userId: string) =>
    api.delete<{ success: boolean; message?: string }>(`/simulator/personas/${userId}`),

  listGifts: () => api.get<{ gifts: SimGift[]; is_available: boolean }>('/simulator/gifts'),
  createGift: (payload: Partial<SimGift>) =>
    api.post<{ success: boolean; message?: string; gift?: SimGift }>('/simulator/gifts', payload),
  updateGift: (giftId: string, payload: Partial<SimGift>) =>
    api.patch<{ success: boolean; message?: string }>(`/simulator/gifts/${giftId}`, payload),
  deleteGift: (giftId: string) =>
    api.delete<{ success: boolean; message?: string }>(`/simulator/gifts/${giftId}`),
};

// 直播场次（直播控制台）
//
// `GET /live-sessions`：场次列表（倒序 + 消息数）。
// `POST /live-sessions/open`：开启新场次（进行中场次自动结束）。
// `POST /live-sessions/{id}/close`：结束场次（空场次整行丢弃）。
// `DELETE /live-sessions/{id}`：删除场次（级联清明细）。
// `GET /live-sessions/{id}/timeline`：单场时间线回看（明细行 + 事件历史合并）。
export const liveSessionsApi = {
  list: (params?: { source?: string; q?: string; limit?: number }) =>
    api.get<LiveSessionListResponse>('/live-sessions', { params }),
  open: (payload?: { title?: string }) =>
    api.post<{ live_session_id: number }>('/live-sessions/open', payload ?? {}),
  close: (id: number) =>
    api.post<{ success: boolean; detail: string }>(`/live-sessions/${id}/close`),
  remove: (id: number) => api.delete<{ success: boolean; detail: string }>(`/live-sessions/${id}`),
  timeline: (id: number, limit = 500) =>
    api.get<SessionTimelineResponse>(`/live-sessions/${id}/timeline`, { params: { limit } }),
};

// 事件历史（游标续传）
//
// `GET /events?since_id=`：返回游标之后的事件缺口（断线/刷新后由 store 调用补齐）。
export const eventsApi = {
  list: (params?: { since_id?: string; limit?: number }) =>
    api.get<{ events: WebSocketMessage[]; total: number; has_more: boolean }>('/events', {
      params,
    }),
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

// Rundown（流程单编排页）
//
// `GET /rundown/state`：当前流程单运行时快照（available / snapshot / transitions /
// segments / config）。available=false 时 snapshot=null，前端按不可用态渲染。
// `POST /rundown/control`：手动控制（pause / resume / next / goto，by="human"），
// 返回最新 snapshot；前端只在收到响应后做错误提示，正常状态由后端通过
// `rundown.changed` 事件推上来。
export const rundownApi = {
  getState: () => api.get<RundownStateResponse>('/rundown/state'),
  control: (request: RundownControlRequest) =>
    api.post<RundownControlResponse>('/rundown/control', request),

  // 流程单库（列表 / 模板 / upsert / 删除 / 复制 / 设为当前）
  //
  // upsert 保存的流程单正是直播运行中的那份时，后端会写穿运行态
  // （进度按环节 id 对齐）；activate 只落盘配置，重启主播 Agent 后生效。
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

export default api;
