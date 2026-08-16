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
  OutlineSegmentsResponse,
  OutlineFileWriteRequest,
  OutlineFileWriteResponse,
} from '@/types';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// System API
export const systemApi = {
  getStatus: () => api.get<SystemStatusResponse>('/system/status'),
  getStats: () => api.get<SystemStatsResponse>('/system/stats'),
  getHealth: () => api.get<{ status: string; timestamp: number }>('/system/health'),
};

// Component API
export const componentApi = {
  getAll: () => api.get<ComponentListResponse>('/components'),
  getOne: (phase: string, name: string) =>
    api.get<{ component: ComponentDetail }>(`/components/${phase}/${name}`),
  control: (phase: string, name: string, request: ComponentControlRequest) =>
    api.post<ComponentControlResponse>(`/components/${phase}/${name}/control`, request),
};

// Config API
export const configApi = {
  get: () => api.get<ConfigResponse>('/config'),
  getSchema: () => api.get<ConfigSchemaResponse>('/config/schema'),
  update: (request: ConfigUpdateRequest) => api.patch<ConfigUpdateResponse>('/config', request),
  restart: () => api.post<ConfigUpdateResponse>('/config/restart'),
};

// Debug API
export const debugApi = {
  injectMessage: (request: InjectMessageRequest) =>
    api.post<InjectMessageResponse>('/debug/inject-message', request),
  injectIntent: (request: InjectIntentRequest) =>
    api.post<InjectIntentResponse>('/debug/inject-intent', request),
  getEventBusStats: () => api.get<EventBusStatsResponse>('/debug/event-bus/stats'),
};

// MaiBot API
export interface MaibotActionRequest {
  text?: string;
  emotion?: { name: string; intensity: number };
  action?: { name: string; parameters: Record<string, unknown> };
}

export interface MaibotActionResponse {
  success: boolean;
  intent_id?: string;
  message?: string;
  error?: string;
}

export const maibotApi = {
  triggerAction: (request: MaibotActionRequest) =>
    api.post<MaibotActionResponse>('/maibot/action', request),
};

// Message API
export const messageApi = {
  getSessionMessages: (sessionId: string, limit: number = 100) =>
    api.get<MessageListResponse>(`/messages/sessions/${sessionId}/messages`, {
      params: { limit },
    }),
};

// LLM API
export const llmApi = {
  getUsage: () => api.get<Record<string, LLMUsageStats>>('/llm/usage'),
  getUsageSummary: () => api.get<LLMUsageSummary>('/llm/usage/summary'),
  getHistory: (params: LLMHistoryQueryParams) =>
    api.get<LLMHistoryResponse>('/llm/history', { params }),
  getRequestById: (requestId: string) => api.get<LLMRequestHistory>(`/llm/history/${requestId}`),
  getAvailableDates: () => api.get<string[]>('/llm/history/dates'),
};

// Capabilities API
export const capabilitiesApi = {
  list: () => api.get<UnifiedCapabilitiesView>('/capabilities'),
};

// Outline API — 直播大纲编辑（任务 11/13 配套）
export const outlineApi = {
  /** 获取当前大纲完整环节列表（供编辑页渲染） */
  getSegments: () => api.get<OutlineSegmentsResponse>('/outline/segments'),
  /** 把编辑后的大纲 TOML 写回磁盘（下一段生效） */
  saveFile: (request: OutlineFileWriteRequest) =>
    api.put<OutlineFileWriteResponse>('/outline/file', request),
};

// Trace API
export * from './traces';

// Simulator API
export interface SimulatorStatus {
  is_running: boolean;
  current_state: string;
  started_at_ms: number;
  config_snapshot: Record<string, unknown>;
  is_collector_available: boolean;
}

export interface SimulatorStats {
  total_messages: number;
  total_tokens: number;
  messages_by_type: Record<string, number>;
  messages_by_role: Record<string, number>;
}

export interface SimulatorPersona {
  user_id: string;
  user_nickname: string;
  role: string;
  personality: string;
  speaking_style: string;
  fans_medal_level: number;
  guard_level: number;
  messages_generated: number;
}

export interface PersonaUpdatePayload {
  user_nickname?: string;
  role?: string;
  personality?: string;
  speaking_style?: string;
  fans_medal_level?: number;
  guard_level?: number;
}

export const simulatorApi = {
  getStatus: () => api.get<SimulatorStatus>('/simulator/status'),
  getStats: () => api.get<SimulatorStats>('/simulator/stats'),
  getPersonas: () => api.get<SimulatorPersona[]>('/simulator/personas'),
  generatePersonas: (count: number, roles?: string[]) =>
    api.post<{ status: string; personas: SimulatorPersona[]; added: number; skipped: number }>(
      '/simulator/personas/generate',
      { count, roles },
      { timeout: 120000 },
    ),
  updatePersona: (userId: string, payload: PersonaUpdatePayload) =>
    api.put<{ status: string }>(`/simulator/personas/${userId}`, payload),
  deletePersona: (userId: string) =>
    api.delete<{ status: string }>(`/simulator/personas/${userId}`),
  start: () => api.post<{ status: string }>('/simulator/start'),
  stop: () => api.post<{ status: string }>('/simulator/stop'),
  updateParams: (params: Record<string, unknown>) =>
    api.post<{ status: string; params: Record<string, unknown> }>('/simulator/params', params),
  triggerGiftRain: (duration_s: number = 30) =>
    api.post<{ status: string; duration_s: number }>('/simulator/trigger/gift_rain', {
      duration_s,
    }),
  triggerTopicInjection: (topic: string) =>
    api.post<{ status: string; topic: string }>('/simulator/trigger/topic_injection', { topic }),
  resetTokenBudget: () => api.post<{ status: string }>('/simulator/reset_token_budget'),
};

export default api;
