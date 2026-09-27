// useAgentDeepLink.ts Agent 页深链解析的测试
//
// 覆盖：解析契约（完整参数 / 仅 agent 无 round / 无参数 / 空 agent）、
// 拼装与解析对偶、consumeDeepLink 读一次并清参。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  agentRoundLocation,
  parseAgentDeepLink,
  useAgentDeepLink,
} from '@/composables/useAgentDeepLink';

const replaceMock = vi.fn();
let mockQuery: Record<string, unknown> = {};

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: mockQuery }),
  useRouter: () => ({ replace: replaceMock }),
}));

describe('parseAgentDeepLink', () => {
  it('完整参数解析出 agent 与 round', () => {
    expect(parseAgentDeepLink({ agent: 'streamer', round: 'rnd_1_0' })).toEqual({
      agent: 'streamer',
      round: 'rnd_1_0',
    });
  });

  it('仅 agent 无 round：round 为空串（只选中不高亮）', () => {
    expect(parseAgentDeepLink({ agent: 'minecraft' })).toEqual({
      agent: 'minecraft',
      round: '',
    });
  });

  it('无参数或空 agent 返回 null', () => {
    expect(parseAgentDeepLink({})).toBeNull();
    expect(parseAgentDeepLink({ agent: '' })).toBeNull();
    expect(parseAgentDeepLink({ agent: '  ' })).toBeNull();
    expect(parseAgentDeepLink({ round: 'rnd_1_0' })).toBeNull();
  });
});

describe('agentRoundLocation', () => {
  it('拼装结果可被 parse 还原（对偶）', () => {
    const location = agentRoundLocation('streamer', 'rnd_9_9');
    expect(location.path).toBe('/agents');
    expect(parseAgentDeepLink(location.query)).toEqual({ agent: 'streamer', round: 'rnd_9_9' });
  });
});

describe('useAgentDeepLink', () => {
  beforeEach(() => {
    mockQuery = {};
    replaceMock.mockReset();
    replaceMock.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('consumeDeepLink 返回参数并清掉 agent/round', async () => {
    mockQuery = { agent: 'streamer', round: 'rnd_1_0' };
    const { consumeDeepLink } = useAgentDeepLink();
    expect(consumeDeepLink()).toEqual({ agent: 'streamer', round: 'rnd_1_0' });
    expect(replaceMock).toHaveBeenCalledTimes(1);
    const arg = replaceMock.mock.calls[0][0] as { query: Record<string, unknown> };
    expect(arg.query).not.toHaveProperty('agent');
    expect(arg.query).not.toHaveProperty('round');
  });

  it('无深链参数时不触发路由改写', () => {
    mockQuery = {};
    const { consumeDeepLink } = useAgentDeepLink();
    expect(consumeDeepLink()).toBeNull();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
