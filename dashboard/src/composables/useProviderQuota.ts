/**
 * 按模型取所属平台的额度快照（上下文面板 Usage 区数据源）。
 *
 * 面板每次弹出都会重新挂载，模块级缓存 60s 让同一模型反复悬停时直接复用上次结果，
 * Usage 区不闪现；同一模型的并发请求合并为一次。查询失败按"无额度"处理并同样缓存。
 */
import { ref, watch, type Ref } from 'vue';
import { llmApi } from '@/api';
import type { LLMProviderQuota } from '@/types';

const CACHE_TTL_MS = 60_000;
const cache = new Map<string, { at: number; data: LLMProviderQuota | null }>();
const inflight = new Map<string, Promise<LLMProviderQuota | null>>();

function cachedQuota(modelName: string): LLMProviderQuota | null | undefined {
  const hit = cache.get(modelName);
  return hit && Date.now() - hit.at < CACHE_TTL_MS ? hit.data : undefined;
}

function fetchQuota(modelName: string): Promise<LLMProviderQuota | null> {
  const pending = inflight.get(modelName);
  if (pending) return pending;
  const request = llmApi
    .getProviderQuota(modelName)
    .then(response => response.data ?? null)
    .catch((error: unknown) => {
      // 额度属增强信息：失败只隐藏 Usage 区，不打断上下文明细
      console.error('Failed to fetch provider quota:', error);
      return null;
    })
    .then(data => {
      cache.set(modelName, { at: Date.now(), data });
      return data;
    })
    .finally(() => inflight.delete(modelName));
  inflight.set(modelName, request);
  return request;
}

export function useProviderQuota(
  modelName: Ref<string | null | undefined>,
): Ref<LLMProviderQuota | null> {
  const quota = ref<LLMProviderQuota | null>(null);
  watch(
    modelName,
    async name => {
      if (!name) {
        quota.value = null;
        return;
      }
      // 缓存命中直接同步填充；未命中先清空，等请求回来再显示
      const hit = cachedQuota(name);
      if (hit !== undefined) {
        quota.value = hit;
        return;
      }
      quota.value = null;
      const data = await fetchQuota(name);
      // 等待期间换了模型时丢弃旧结果
      if (modelName.value === name) {
        quota.value = data;
      }
    },
    { immediate: true },
  );
  return quota;
}
