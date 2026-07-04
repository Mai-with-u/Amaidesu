import api from './index';
import type { TraceListResponse, TraceDetailResponse } from '@/types';

export const tracesApi = {
  list: (limit = 20) =>
    api.get<TraceListResponse>('/traces', { params: { limit } }),
  get: (messageId: string) =>
    api.get<TraceDetailResponse>(`/traces/${messageId}`),
};
