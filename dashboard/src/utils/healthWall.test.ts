// healthWall 纯逻辑结构断言（node 环境，不挂载组件）

import { describe, expect, it } from 'vitest';
import type { SystemHealthItem } from '@/types';
import {
  HEALTH_DOMAIN_LABELS,
  isReconnectable,
  summarizeDomains,
  worstHealthState,
  type HealthDomainGroup,
} from './healthWall';

function item(partial: Partial<SystemHealthItem>): SystemHealthItem {
  return {
    name: 'x',
    state: 'ok',
    detail: '',
    last_error: '',
    supports_reconnect: false,
    ...partial,
  };
}

describe('worstHealthState', () => {
  it('按 offline > degraded > unknown > ok 取最差', () => {
    expect(worstHealthState([item({ state: 'ok' }), item({ state: 'degraded' })])).toBe('degraded');
    expect(worstHealthState([item({ state: 'ok' }), item({ state: 'offline' })])).toBe('offline');
    expect(worstHealthState([item({ state: 'ok' }), item({ state: 'unknown' })])).toBe('unknown');
    expect(worstHealthState([])).toBe('unknown');
  });
});

describe('summarizeDomains', () => {
  it('按固定域顺序整理已知域，未知域排尾；响应中缺席的域不出卡', () => {
    const groups: HealthDomainGroup[] = summarizeDomains({
      agents: [item({ name: 'streamer' })],
      custom_domain: [],
      tts: [item({ name: 'tts', state: 'offline' })],
    });
    expect(groups.map(g => g.domain)).toEqual(['tts', 'agents', 'custom_domain']);
    expect(groups[0].label).toBe(HEALTH_DOMAIN_LABELS.tts);
    expect(groups[0].worst).toBe('offline');
    expect(groups[2].label).toBe('custom_domain');
  });

  it('响应缺失时产出空列表（组件据此不铺空卡）', () => {
    expect(summarizeDomains(undefined)).toEqual([]);
  });
});

describe('isReconnectable', () => {
  it('仅 supports_reconnect 为真的条目可重连', () => {
    expect(isReconnectable(item({ supports_reconnect: true }))).toBe(true);
    expect(isReconnectable(item({}))).toBe(false);
  });
});
