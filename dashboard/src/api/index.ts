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
  SystemStatsResponse,
  ComponentListResponse,
  ComponentDetail,
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
  MessageListResponse,
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
  getStats: () => api.get<SystemStatsResponse>('/system/stats'),
  getHealth: () => api.get<{ status: string; timestamp: number }>('/system/health'),
};

// ===== 组件 =====
//
// 后端 `/api/v1/components` 当前返回 `{input:[], decision:[], output:[]}`（W8 证据）：
// 三个 Manager 未被注入，所以列表恒空。v2 新架构分组键为 `collectors / agents / tools`，
// 但当前端点尚未更新；前端按现状保留 phase 字段，新增 group 字段待后端补齐。
export const componentApi = {
  getAll: () => api.get<ComponentListResponse>('/components'),
  getOne: (phase: string, name: string) =>
    api.get<{ component: ComponentDetail }>(`/components/${phase}/${name}`),
  control: (phase: string, name: string, request: ComponentControlRequest) =>
    api.post<ComponentControlResponse>(`/components/${phase}/${name}/control`, request),
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

// ===== 会话消息 =====
export const messageApi = {
  getSessionMessages: (sessionId: string, limit: number = 100) =>
    api.get<MessageListResponse>(`/messages/sessions/${sessionId}/messages`, {
      params: { limit },
    }),
};

// ===== LLM =====
export const llmApi = {
  getUsage: () => api.get<Record<string, LLMUsageStats>>('/llm/usage'),
  getUsageSummary: () => api.get<LLMUsageSummary>('/llm/usage/summary'),
  getHistory: (params: LLMHistoryQueryParams) =>
    api.get<LLMHistoryResponse>('/llm/history', { params }),
  getRequestById: (requestId: string) => api.get<LLMRequestHistory>(`/llm/history/${requestId}`),
  getAvailableDates: () => api.get<string[]>('/llm/history/dates'),
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
// `traces.ts` 当前实现：GET /traces（按 message_id 聚合 room.message 事件） +
// GET /traces/{message_id}。v2 实现仅返回 message + event，decision/outputs 字段为空。
export * from './traces';

export default api;
