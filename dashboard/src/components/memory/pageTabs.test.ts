import { describe, expect, it } from 'vitest';
import {
  DEFAULT_MEMORY_TAB,
  MEMORY_TAB_KEYS,
  MEMORY_TAB_LABELS,
  resolveMemoryTab,
} from './pageTabs';

describe('记忆页 tab 解析', () => {
  it('七个 tab 各有显示名，键表与显示名一一对应', () => {
    expect(MEMORY_TAB_KEYS).toHaveLength(7);
    expect(Object.keys(MEMORY_TAB_LABELS).sort()).toEqual([...MEMORY_TAB_KEYS].sort());
  });

  it('合法值原样透传，缺省与未知值回退默认 tab', () => {
    expect(resolveMemoryTab('archive')).toBe('archive');
    expect(resolveMemoryTab('player-candidates')).toBe('player-candidates');
    expect(resolveMemoryTab(undefined)).toBe(DEFAULT_MEMORY_TAB);
    expect(resolveMemoryTab('')).toBe(DEFAULT_MEMORY_TAB);
    expect(resolveMemoryTab('no-such-tab')).toBe(DEFAULT_MEMORY_TAB);
    expect(resolveMemoryTab(123)).toBe(DEFAULT_MEMORY_TAB);
  });
});
