/**
 * Dashboard API 客户端（v2.0）
 *
 * 模拟直播能力控制面：
 * - ``simulatorApi`` → ``/api/v1/simulator/*``：LLM 驱动的 SimulatorService（ADR-006）
 * - ``mockCollectorApi`` → ``/api/v1/mock/*``：确定性 JSONL 回放 MockCollector
 *
 * 历史 AD-008 半吊子模式（人设 / 礼物雨 / 话题注入 / token 预算）已被 ADR-006
 * 推翻并移除；旧 mockCollectorApi 的对应方法同步下线（前端调用会触发 404，由
 * 调用方 try/catch 兜底）。
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
  SimulatorStatus,
  SimulatorControlResponse,
  AgendaStateResponse,
  AgendaControlRequest,
  AgendaControlResponse,
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

// ===== Simulator 控制面（ADR-006：LLM 驱动生成式虚拟直播间） =====
//
// 控制 SimulatorService 的启停与状态查询。enabled=false 时 status 仍返回
// （不抛 404）；start 会拒绝并提示需要修改配置后重启。
export const simulatorApi = {
  getStatus: () => api.get<SimulatorStatus>('/simulator/status'),
  start: () => api.post<SimulatorControlResponse>('/simulator/start'),
  stop: () => api.post<SimulatorControlResponse>('/simulator/stop'),
};

// ===== Mock 采集器控制面（ADR-006：确定性 JSONL 回放器） =====
//
// 代理 CollectorManager 的 ``mock`` 实例启停。控制面与通用
// ``/api/v1/components/collectors/mock/control`` 等价；前端保留 ``/mock/*`` 路径
// 是为与 ``/api/v1/simulator/*`` 做语义区隔（LLM 仿真 vs JSONL 回放）。
//
// ADR-006 已移除 simulator 半吊子模式（人设 / 参数 / 礼物雨 / 话题注入 / token
// 预算），对应方法已被下线——前端若有遗留调用，应改走到「组件 → 采集器」通用页。
export const mockCollectorApi = {
  getStatus: () => api.get<MockCollectorStatus>('/mock/status'),
  start: () => api.post<MockCollectorStatus>('/mock/start'),
  stop: () => api.post<MockCollectorStatus>('/mock/stop'),
};

// ===== Trace =====
//
// `traces.ts`：GET /traces（最近链路列表）+ GET /traces/{message_id}（按 message_id
// 聚合 messages/planning/execution 三段事件；planning/execution 可能为空数组）。
export * from './traces';

// ===== Agenda（节目单控制面） =====
//
// `GET /agenda/state`：当前节目单运行时快照（available / snapshot / transitions /
// segments / expanded / config）。available=false 时 snapshot=null，前端按不可用
// 态渲染引导用户去设置页开启。
// `POST /agenda/control`：手动控制（pause / resume / skip / rewind / unload /
// jump / start），返回最新 snapshot；前端只在收到响应后做错误提示，正常状态
// 由后端通过 `agenda.update` / `planner.checkpoint` 事件推上来。
export const agendaApi = {
  getState: () => api.get<AgendaStateResponse>('/agenda/state'),
  control: (request: AgendaControlRequest) =>
    api.post<AgendaControlResponse>('/agenda/control', request),
};

export default api;
