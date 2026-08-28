/**
 * Dashboard API 客户端（v2.0）
 *
 * W8 证据：maibot / outline / proactive / simulator 子路由已删除。模拟直播间能力由
 * `modules/collectors/mock/` 承载，WebUI 管理页后续波次补齐 mock 控制面。
 *
 * 仍在使用的端点：system / components / messages / config / debug / llm /
 * capabilities / events / traces
 */

import axios from 'axios';
import type {
  SystemStatusResponse,
  ComponentListResponse,
  ComponentControlRequest,
  ComponentControlResponse,
  ConfigResponse,
  InjectMessageRequest,
  InjectMessageResponse,
  InjectIntentRequest,
  InjectIntentResponse,
  EventBusStatsResponse,
  ConfigSchemaResponse,
  ConfigUpdateRequest,
  ConfigUpdateResponse,
  LLMUsageStats,
  LLMUsageSummary,
  LLMHistoryQueryParams,
  LLMHistoryResponse,
  LLMRequestHistory,
  UnifiedCapabilitiesView,
  MockCollectorStatus,
  MockCollectorStats,
  MockCollectorPersona,
  MockPersonaUpdatePayload,
} from '@/types';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// ===== 系统 =====

export const systemApi = {
  getStatus: () => api.get<SystemStatusResponse>('/system/status'),
  getHealth: () => api.get<{ status: string; timestamp: number }>('/system/health'),
};

// ===== 组件 =====
//
// 后端 `/api/v1/components` 按 v2 分组 `collectors / agents / tools` 返回组件清单；
// 控制端点路径参数为 `group`（旧后端为 `phase`，迁移完成后无须兼容）。
export const componentApi = {
  getAll: () => api.get<ComponentListResponse>('/components'),
  control: (group: string, name: string, request: ComponentControlRequest) =>
    api.post<ComponentControlResponse>(`/components/${group}/${name}/control`, request),
};

// ===== 配置 =====
//
// 后端 `/api/v1/config` 返回 7 文件合并的扁平 dict（core / model / agents / tools /
// memory / storage / background）；`/api/v1/config/schema` 返回按文件归类的 groups。
export const configApi = {
  get: () => api.get<ConfigResponse>('/config'),
  getSchema: () => api.get<ConfigSchemaResponse>('/config/schema'),
  update: (request: ConfigUpdateRequest) => api.patch<ConfigUpdateResponse>('/config', request),
  restart: () => api.post<ConfigUpdateResponse>('/config/restart'),
};

// ===== 调试注入 =====
export const debugApi = {
  injectMessage: (request: InjectMessageRequest) =>
    api.post<InjectMessageResponse>('/debug/inject-message', request),
  injectIntent: (request: InjectIntentRequest) =>
    api.post<InjectIntentResponse>('/debug/inject-intent', request),
  getEventBusStats: () => api.get<EventBusStatsResponse>('/debug/event-bus/stats'),
};

// ===== LLM =====
export const llmApi = {
  getUsage: () => api.get<Record<string, LLMUsageStats>>('/llm/usage'),
  getUsageSummary: () => api.get<LLMUsageSummary>('/llm/usage/summary'),
  getHistory: (params: LLMHistoryQueryParams) =>
    api.get<LLMHistoryResponse>('/llm/history', { params }),
  getRequestById: (requestId: string) => api.get<LLMRequestHistory>(`/llm/history/${requestId}`),
};

// ===== Capabilities（v2 工具 action 清单） =====
export const capabilitiesApi = {
  list: () => api.get<UnifiedCapabilitiesView>('/capabilities'),
};

// ===== Mock 采集器控制面（v2 新增） =====
//
// W8 路由：`/api/v1/simulator/*` 已删除。下列方法当前调用 404/503；前端调用时
// 应 try/catch 并显示"mock 采集器未启用"提示。后续波次后端会补齐 mock 控制面端点
// （挂载在 `/api/v1/mock/*` 或新增独立 router），此处 API 形态保留以便平滑切换。
export const mockCollectorApi = {
  getStatus: () => api.get<MockCollectorStatus>('/mock/status'),
  getStats: () => api.get<MockCollectorStats>('/mock/stats'),
  getPersonas: () => api.get<MockCollectorPersona[]>('/mock/personas'),
  generatePersonas: (count: number) =>
    api.post<{ status: string; personas: MockCollectorPersona[]; added: number; skipped: number }>(
      '/mock/personas/generate',
      { count },
      { timeout: 120000 },
    ),
  updatePersona: (userId: string, payload: MockPersonaUpdatePayload) =>
    api.put<{ status: string }>(`/mock/personas/${userId}`, payload),
  deletePersona: (userId: string) => api.delete<{ status: string }>(`/mock/personas/${userId}`),
  start: () => api.post<{ status: string }>('/mock/start'),
  stop: () => api.post<{ status: string }>('/mock/stop'),
  updateParams: (params: Record<string, unknown>) =>
    api.post<{ status: string; params: Record<string, unknown> }>('/mock/params', params),
  triggerGiftRain: (durationS: number = 30) =>
    api.post<{ status: string; duration_s: number }>('/mock/trigger/gift_rain', {
      duration_s: durationS,
    }),
  triggerTopicInjection: (topic: string) =>
    api.post<{ status: string; topic: string }>('/mock/trigger/topic_injection', { topic }),
  resetTokenBudget: () => api.post<{ status: string }>('/mock/reset_token_budget'),
};

// ===== Trace =====
//
// `traces.ts`：GET /traces（最近链路列表）+ GET /traces/{message_id}（按 message_id
// 聚合 messages/planning/execution 三段事件；planning/execution 可能为空数组）。
export * from './traces';

export default api;
