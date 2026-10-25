// @vitest-environment happy-dom
// useStreamPageQuery 深链查询四件套的测试
//
// 覆盖：初始化读多值键与单值键、空状态删键不留空串、本页 replace 触发的
// query 变化不回写状态、外部导航把 query 写回状态。route/router 用可变
// mock 句柄模拟（与 useAgentDeepLink.test.ts 同手法）。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { effectScope, ref, type EffectScope } from 'vue';

import { useStreamPageQuery } from '@/composables/useStreamPageQuery';

const replaceMock = vi.fn();
const mockQuery = ref<Record<string, unknown>>({});

vi.mock('vue-router', () => ({
  useRoute: () => ({
    get query(): Record<string, unknown> {
      return mockQuery.value;
    },
  }),
  useRouter: () => ({ replace: replaceMock }),
}));

interface SetupResult {
  levels: ReturnType<typeof ref<string[]>>;
  search: ReturnType<typeof ref<string>>;
}

let scope: EffectScope | null = null;

function setup(): SetupResult {
  const levels = ref<string[]>([]);
  const search = ref('');

  // 组件外调用组合式：用 effectScope 持有 watch，afterEach 停掉避免用例串扰
  scope = effectScope();
  scope.run(() => {
    useStreamPageQuery({
      fields: [
        { key: 'level', get: () => levels.value, set: values => (levels.value = values) },
        {
          key: 'q',
          single: true,
          get: () => (search.value ? [search.value] : []),
          set: values => (search.value = values[0] ?? ''),
        },
      ],
      watchSources: [levels, search],
    });
  });
  return { levels, search };
}

function flushRoute(query: Record<string, unknown>): void {
  mockQuery.value = query;
}

beforeEach(() => {
  mockQuery.value = {};
  replaceMock.mockReset();
  replaceMock.mockResolvedValue(undefined);
});

afterEach(() => {
  scope?.stop();
  vi.clearAllMocks();
});

describe('深链初始化', () => {
  it('多值键读为数组写入状态；单值键读为首元素', () => {
    flushRoute({ level: ['INFO', 'ERROR'], q: 'timeout' });
    const { levels, search } = setup();
    expect(levels.value).toEqual(['INFO', 'ERROR']);
    expect(search.value).toBe('timeout');
  });

  it('键缺失或为空时不触碰状态默认值', () => {
    flushRoute({});
    const { levels, search } = setup();
    expect(levels.value).toEqual([]);
    expect(search.value).toBe('');
  });

  it('初始化发生在 watch 注册前，不触发回写', () => {
    flushRoute({ level: 'INFO' });
    setup();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});

describe('筛选变更回写 URL', () => {
  it('非空状态写键，空状态删键（不留空串）', async () => {
    const { levels, search } = setup();
    levels.value = ['INFO', 'WARNING'];
    await vi.waitFor(() => expect(replaceMock).toHaveBeenCalledTimes(1));
    expect(replaceMock.mock.calls[0][0]).toEqual({ query: { level: ['INFO', 'WARNING'] } });

    search.value = 'abc';
    await vi.waitFor(() => expect(replaceMock).toHaveBeenCalledTimes(2));
    expect(replaceMock.mock.calls[1][0]).toEqual({
      query: { level: ['INFO', 'WARNING'], q: ['abc'] },
    });

    levels.value = [];
    await vi.waitFor(() => expect(replaceMock).toHaveBeenCalledTimes(3));
    expect(replaceMock.mock.calls[2][0]).toEqual({ query: { q: ['abc'] } });
  });
});

describe('route.query 变化恢复', () => {
  it('本页 replace 触发的 query 变化（与状态一致）跳过，不回写状态', async () => {
    flushRoute({ level: 'INFO' });
    const { levels } = setup();
    expect(levels.value).toEqual(['INFO']);

    // 模拟本页 replace 后路由更新：query 与状态一致
    flushRoute({ level: ['INFO'] });
    await Promise.resolve();
    expect(levels.value).toEqual(['INFO']);
  });

  it('外部导航（前进/后退）把 query 写回状态', async () => {
    flushRoute({});
    const { levels, search } = setup();

    flushRoute({ level: ['DEBUG'], q: 'boom' });
    await vi.waitFor(() => {
      expect(levels.value).toEqual(['DEBUG']);
      expect(search.value).toBe('boom');
    });
  });
});
