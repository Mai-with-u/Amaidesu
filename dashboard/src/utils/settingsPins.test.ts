// @vitest-environment happy-dom
// settingsPins 表驱动测试：读取校验与写入往返（localStorage 依赖 happy-dom）

import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadPinnedEntries, savePinnedEntries, type PinnedEntry } from './settingsPins';

function entry(partial: Partial<PinnedEntry>): PinnedEntry {
  return {
    key: 'agents.agents.minecraft.context',
    label: '上下文',
    fileKey: 'agents.toml',
    ...partial,
  };
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe('loadPinnedEntries', () => {
  it('空存储返回空列表', () => {
    expect(loadPinnedEntries()).toEqual([]);
  });

  it('合法数据原样读回', () => {
    window.localStorage.setItem('settings-pinned-entries', JSON.stringify([entry({})]));
    expect(loadPinnedEntries()).toEqual([entry({})]);
  });

  it('非法 JSON 与非数组数据按空列表处理', () => {
    window.localStorage.setItem('settings-pinned-entries', '{broken');
    expect(loadPinnedEntries()).toEqual([]);
    window.localStorage.setItem('settings-pinned-entries', '{"a":1}');
    expect(loadPinnedEntries()).toEqual([]);
  });

  it('缺字段或类型不对的条目被剔除', () => {
    window.localStorage.setItem(
      'settings-pinned-entries',
      JSON.stringify([
        entry({ key: 'a' }),
        { key: 'b', label: 3, fileKey: 'x.toml' },
        null,
        entry({ key: 'c', label: '合法', fileKey: 'infra.toml' }),
      ]),
    );
    expect(loadPinnedEntries().map(e => e.key)).toEqual(['a', 'c']);
  });

  it('超出上限的条目被截断', () => {
    const many = Array.from({ length: 20 }, (_, i) => entry({ key: `k${i}` }));
    window.localStorage.setItem('settings-pinned-entries', JSON.stringify(many));
    expect(loadPinnedEntries()).toHaveLength(12);
  });
});

describe('savePinnedEntries', () => {
  it('写入后可读回', () => {
    savePinnedEntries([entry({})]);
    expect(loadPinnedEntries()).toEqual([entry({})]);
  });

  it('写入超出上限自动截断', () => {
    const many = Array.from({ length: 20 }, (_, i) => entry({ key: `k${i}` }));
    savePinnedEntries(many);
    expect(loadPinnedEntries()).toHaveLength(12);
  });

  it('localStorage 异常时静默降级不抛错', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(() => savePinnedEntries([entry({})])).not.toThrow();
  });
});
