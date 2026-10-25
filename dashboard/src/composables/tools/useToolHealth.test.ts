// useToolHealth.ts 工具熔断状态的测试：WS tool.health.* 消息就地更新工具
// 条目（open → tripped / closed → 清空）、非健康消息与畸形载荷忽略、
// tooltip 文案、作用域销毁时退订。

import { effectScope, ref, type EffectScope } from 'vue';
import { describe, expect, it, vi, type Mock } from 'vitest';

import { useToolHealth } from '@/composables/tools/useToolHealth';
import type { ToolEntry, WebSocketMessage } from '@/types';

const subscribers: Array<(msg: WebSocketMessage) => void> = [];
const unsubscribeSpy = vi.fn();

vi.mock('@/stores/websocket', () => ({
  useWebSocketStore: () => ({
    subscribe: (fn: (msg: WebSocketMessage) => void) => {
      subscribers.push(fn);
    },
    unsubscribe: unsubscribeSpy,
  }),
}));

function toolEntry(name: string): ToolEntry {
  return {
    name,
    full_name: `prov_${name}`,
    parameters: {},
    provider: 'prov',
    category: 'studio',
    disabled: false,
  };
}

function healthMessage(
  partial: Partial<WebSocketMessage['data']> & { type?: string },
): WebSocketMessage {
  return {
    type: (partial.type ?? 'tool.health.prov_search') as WebSocketMessage['type'],
    timestamp_ms: 1_000,
    data: {
      tool_name: 'search',
      provider: 'prov',
      state: 'open',
      failure_count: 3,
      last_error: 'boom',
      timestamp_ms: 5_000,
      ...partial,
    },
  };
}

describe('useToolHealth', () => {
  let scope: EffectScope | null = null;

  function setup(tools: ToolEntry[]) {
    const toolsRef = ref(tools);
    subscribers.length = 0;
    unsubscribeSpy.mockClear();
    scope = effectScope();
    scope.run(() => useToolHealth(toolsRef));
    return toolsRef;
  }

  it('open 事件：写入 tripped 健康态（次数/错误/时刻）', () => {
    const tools = setup([toolEntry('search')]);
    subscribers[0](healthMessage({}));
    expect(tools.value[0].health).toEqual({
      state: 'tripped',
      failure_count: 3,
      last_error: 'boom',
      tripped_at_ms: 5_000,
    });
  });

  it('closed 事件：清空健康态', () => {
    const tools = setup([toolEntry('search')]);
    tools.value[0].health = {
      state: 'tripped',
      failure_count: 1,
      last_error: 'x',
      tripped_at_ms: 1,
    };
    subscribers[0](healthMessage({ state: 'closed' }));
    expect(tools.value[0].health).toBeNull();
  });

  it('未知工具名与非 tool.health.* 消息忽略；畸形载荷忽略', () => {
    const tools = setup([toolEntry('search')]);
    subscribers[0](healthMessage({ tool_name: 'other' }));
    expect(tools.value[0].health).toBeUndefined();

    subscribers[0]({ type: 'tool.other' as WebSocketMessage['type'], timestamp_ms: 1, data: {} });
    subscribers[0]({ type: 'tool.health.search', timestamp_ms: 1, data: { tool_name: 'search' } });
    expect(tools.value[0].health).toBeUndefined();
  });

  it('healthTooltip：空健康态返回空串，有值返回分号拼接', () => {
    setup([]);
    const toolsRef = ref([
      toolEntry('a'),
      Object.assign(toolEntry('b'), {
        health: {
          state: 'tripped' as const,
          failure_count: 2,
          last_error: '超时',
          tripped_at_ms: 9,
        },
      }),
    ]);
    scope?.stop();
    subscribers.length = 0;
    scope = effectScope();
    const { healthTooltip } = scope.run(() => useToolHealth(toolsRef))!;
    expect(healthTooltip(toolsRef.value[0])).toBe('');
    expect(healthTooltip(toolsRef.value[1])).toBe('超时；连续失败 2 次');
  });

  it('作用域销毁时退订', () => {
    setup([toolEntry('search')]);
    expect(subscribers).toHaveLength(1);
    scope?.stop();
    expect(unsubscribeSpy).toHaveBeenCalledTimes(1);
    expect((unsubscribeSpy as Mock).mock.calls[0][0]).toBe(subscribers[0]);
  });
});
