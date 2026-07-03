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

export default api;
