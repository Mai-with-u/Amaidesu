/**
 * 实时流列表页（LogViewer / EventLog）深链查询四件套的共享机械。
 *
 * 收编两页成对重复的三段：深链初始化（setup 期把 query 写入筛选状态）、
 * 筛选变更回写 URL（空值删键不留空串，router.replace 不产生历史记录）、
 * route.query 变化恢复（本页 replace 触发的变化跳过，外部导航写回状态）。
 * 键名映射与域规则（level 白名单过滤、搜索词单值语义）由页面以字段钩子提供，
 * 谓词筛选不在此——那是 useLogStreamQuery 的职责。
 */

import { watch, type WatchSource } from 'vue';
import { useRoute, useRouter, type LocationQuery } from 'vue-router';

/** 单个深链键的读写钩子：状态以字符串列表表示（单值键即 0/1 个元素） */
export interface StreamQueryField {
  /** URL 上的键名 */
  key: string;
  /** 单值键（如搜索词）：缺失/空串读作空列表，写入时取列表首元素 */
  single?: boolean;
  /** 读当前筛选状态；空列表表示默认态（回写时删键而非留空串） */
  get: () => string[];
  /** 把字符串列表写回筛选状态（域规则如白名单过滤在此实现） */
  set: (values: string[]) => void;
}

export interface UseStreamPageQueryOptions {
  fields: StreamQueryField[];
  /** 筛选状态的响应源：任一变化即回写 URL */
  watchSources: Array<WatchSource<unknown>>;
}

/** query 的单值与重复键（?level=INFO&level=ERROR）统一读为字符串数组；
 * 单值键遇重复键取首值（对应原 String(raw ?? '') 的单值语义） */
function readQueryValues(raw: LocationQuery[string], single: boolean): string[] {
  if (single) {
    const first = Array.isArray(raw) ? raw[0] : raw;
    return first ? [String(first)] : [];
  }
  if (Array.isArray(raw)) return raw.map(String);
  return raw ? [String(raw)] : [];
}

export function useStreamPageQuery(options: UseStreamPageQueryOptions): void {
  const route = useRoute();
  const router = useRouter();

  function readField(field: StreamQueryField, query: LocationQuery): string[] {
    return readQueryValues(query[field.key], field.single === true);
  }

  // 深链初始化：setup 期写入筛选状态，发生在 watch 注册之前，不触发回写
  for (const field of options.fields) {
    const values = readField(field, route.query);
    if (values.length > 0) {
      field.set(values);
    }
  }

  // 筛选变更回写 URL：空列表（默认态）删键而非留空串，replace 不产生历史记录。
  // 客户端筛选不触发请求，故无回写循环风险
  watch(options.watchSources, () => {
    const query: Record<string, string[]> = {};
    for (const field of options.fields) {
      const values = field.get();
      if (values.length > 0) {
        query[field.key] = values;
      }
    }
    void router.replace({ query });
  });

  // 前进/后退恢复：query 变化与当前状态一致（本页 replace 触发）时跳过，
  // 否则视为用户导航，把 query 写回筛选状态。列表用 join('\u0000') 比较
  // 以区分多值键的内容差异
  watch(
    () => route.query,
    query => {
      const changed = options.fields.some(
        field => readField(field, query).join('\u0000') !== field.get().join('\u0000'),
      );
      if (!changed) return;
      for (const field of options.fields) {
        field.set(readField(field, query));
      }
    },
  );
}
