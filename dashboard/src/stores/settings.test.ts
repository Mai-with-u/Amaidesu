// settings store 逐项撤销（revertChange）的测试
//
// 覆盖：嵌套 key 的当前值恢复（恢复为 originalValues 对应值且不共享引用）、
// 撤销后待保存清单移除该条、撤销中间一项不影响其他项、撤销不存在的 key 零操作。

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { ConfigFieldSchema, PendingChange } from '@/types/settings';
import { restoreNestedValue, useSettingsStore } from '@/stores/settings';

// store 模块顶部导入 api 单例，测试环境无后端，mock 掉即可
vi.mock('@/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

function makeChange(key: string, oldValue: unknown, newValue: unknown): PendingChange {
  const field: ConfigFieldSchema = {
    key,
    label: key,
    type: 'string' as ConfigFieldSchema['type'],
    required: false,
    sensitive: false,
  };
  return { key, oldValue, newValue, field };
}

describe('settings store revertChange', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('嵌套 key 撤销：当前值恢复为原始值，清单移除该条', () => {
    const store = useSettingsStore();
    store.currentValues = {
      model: { provider: { name: 'openai', key: 'new-key' }, temperature: 0.7 },
    };
    store.originalValues = {
      model: { provider: { name: 'openai', key: 'old-key' }, temperature: 0.7 },
    };
    store.pendingChanges = [
      makeChange('model.provider.key', 'old-key', 'new-key'),
      makeChange('model.temperature', 0.7, 0.9),
    ];

    store.revertChange('model.provider.key');

    const values = store.currentValues as Record<string, any>;
    const originals = store.originalValues as Record<string, any>;
    expect(store.pendingChanges.map(c => c.key)).toEqual(['model.temperature']);
    expect(values.model.provider.key).toBe('old-key');
    // 恢复走浅拷贝，不得与 originalValues 共享沿途对象引用
    expect(values.model).not.toBe(originals.model);
    expect(values.model.provider).not.toBe(originals.model.provider);
  });

  it('撤销中间一项：其余两项保留且值不受影响', () => {
    const store = useSettingsStore();
    store.currentValues = { a: 1, b: 2, c: 3 };
    store.originalValues = { a: 0, b: 0, c: 0 };
    store.pendingChanges = [makeChange('a', 0, 1), makeChange('b', 0, 2), makeChange('c', 0, 3)];

    store.revertChange('b');

    expect(store.pendingChanges.map(c => c.key)).toEqual(['a', 'c']);
    expect(store.currentValues).toEqual({ a: 1, b: 0, c: 3 });
  });

  it('撤销不存在的 key：零操作', () => {
    const store = useSettingsStore();
    store.currentValues = { a: 1 };
    store.originalValues = { a: 1 };
    store.pendingChanges = [makeChange('a', 1, 2)];

    store.revertChange('no.such.key');

    expect(store.pendingChanges).toHaveLength(1);
    expect(store.currentValues).toEqual({ a: 1 });
  });

  it('原始值路径缺失时撤销：叶子键被删除', () => {
    const store = useSettingsStore();
    store.currentValues = { infra: { dashboard: { port: 60215 } } };
    store.originalValues = { infra: { dashboard: {} } };
    store.pendingChanges = [makeChange('infra.dashboard.port', undefined, 60215)];

    store.revertChange('infra.dashboard.port');

    expect(store.pendingChanges).toHaveLength(0);
    expect((store.currentValues as Record<string, any>).infra.dashboard).toEqual({});
  });
});

describe('restoreNestedValue', () => {
  it('undefined 值删除叶子，普通值沿途浅拷贝写入', () => {
    const tree = { a: { b: { c: 1 }, d: 2 }, e: 3 };
    expect(restoreNestedValue(tree, 'a.b.c', undefined)).toEqual({ a: { b: {}, d: 2 }, e: 3 });
    expect(restoreNestedValue(tree, 'a.d', 9)).toEqual({ a: { b: { c: 1 }, d: 9 }, e: 3 });
    // 传入树未被原地修改
    expect(tree).toEqual({ a: { b: { c: 1 }, d: 2 }, e: 3 });
  });
});
