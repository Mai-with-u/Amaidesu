// useConsoleViewMode.ts 视图档位偏好的测试
//
// 覆盖：默认完整档、localStorage 恢复简洁档、非法值回落完整档、
// 切档写回持久化、存储不可用时读档回落与切档照常生效。

import { nextTick } from 'vue';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { useConsoleViewMode } from '@/composables/live/useConsoleViewMode';

/** 极简 localStorage 桩：node 测试环境无 localStorage（同 stores/events.test.ts 手法） */
function installLocalStorageStub(): Map<string, string> {
  const backing = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => (backing.has(key) ? backing.get(key)! : null),
    setItem: (key: string, value: string) => void backing.set(key, value),
  });
  return backing;
}

describe('useConsoleViewMode', () => {
  let backing: Map<string, string>;

  beforeEach(() => {
    backing = installLocalStorageStub();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('无存储值时默认完整档', () => {
    const { viewMode, isFullMode } = useConsoleViewMode();
    expect(viewMode.value).toBe('full');
    expect(isFullMode.value).toBe(true);
  });

  it('存储值为 simple 时恢复简洁档', () => {
    backing.set('live-console-view-mode', 'simple');
    const { viewMode, isFullMode } = useConsoleViewMode();
    expect(viewMode.value).toBe('simple');
    expect(isFullMode.value).toBe(false);
  });

  it('存储值非法时回落完整档', () => {
    backing.set('live-console-view-mode', 'bogus');
    const { viewMode } = useConsoleViewMode();
    expect(viewMode.value).toBe('full');
  });

  it('切档写回 localStorage，新实例读回一致（刷新语义）', async () => {
    const { viewMode } = useConsoleViewMode();
    viewMode.value = 'simple';
    await nextTick(); // 持久化走 watch，等调度器冲刷后再断言
    expect(backing.get('live-console-view-mode')).toBe('simple');
    expect(useConsoleViewMode().viewMode.value).toBe('simple');
  });

  it('存储不可用时读档回落完整档、切档照常生效', async () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('storage blocked');
      },
      setItem: () => {
        throw new Error('storage blocked');
      },
    });
    const { viewMode, isFullMode } = useConsoleViewMode();
    expect(viewMode.value).toBe('full');
    expect(isFullMode.value).toBe(true);
    viewMode.value = 'simple';
    await nextTick();
    expect(viewMode.value).toBe('simple');
  });
});
