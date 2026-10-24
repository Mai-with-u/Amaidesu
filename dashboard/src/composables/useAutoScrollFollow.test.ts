// @vitest-environment happy-dom
// useAutoScrollFollow RAF 节流滚动跟随的测试
//
// 覆盖：RAF 节流合并同帧多次滚底、滚底写入 scrollTop、滚离底部触发页面
// 反应、生效门关闭时跳过判定、卸载时取消挂起的动画帧。RAF 用手动排队的
// fake 替身，nextTick 用微任务冲刷。

import { describe, expect, it, vi } from 'vitest';
import { defineComponent, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';

import { useAutoScrollFollow } from '@/composables/useAutoScrollFollow';

/** 手动排队的 RAF 替身：返回冲刷函数与按 id 取消的记录 */
function installFakeRAF(): {
  flush: () => void;
  cancelled: number[];
} {
  const callbacks = new Map<number, FrameRequestCallback>();
  const cancelled: number[] = [];
  let nextId = 1;
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback): number => {
    const id = nextId++;
    callbacks.set(id, cb);
    return id;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number): void => {
    callbacks.delete(id);
    cancelled.push(id);
  });
  return {
    flush: () => {
      const pending = [...callbacks.values()];
      callbacks.clear();
      pending.forEach(cb => cb(0));
    },
    cancelled,
  };
}

function scrollBox(contentHeight: number, viewHeight: number): HTMLElement {
  const el = document.createElement('div');
  Object.defineProperty(el, 'scrollHeight', { value: contentHeight });
  Object.defineProperty(el, 'clientHeight', { value: viewHeight });
  return el;
}

describe('滚动跟随', () => {
  it('同帧多次触发合并为一次 RAF，冲刷后滚到容器底部', async () => {
    const fake = installFakeRAF();
    const el = scrollBox(500, 200);
    const container = ref<HTMLElement | null>(el);
    const { scrollToBottom } = useAutoScrollFollow({
      container,
      canHandleScroll: () => true,
    });

    scrollToBottom();
    scrollToBottom();
    scrollToBottom();
    expect(el.scrollTop).toBe(0);

    fake.flush();
    await nextTick();
    expect(el.scrollTop).toBe(500);
  });

  it('新数据到达经 scrollToBottom 跟随（门控在调用方）', async () => {
    const fake = installFakeRAF();
    const el = scrollBox(800, 300);
    const container = ref<HTMLElement | null>(el);
    const { scrollToBottom } = useAutoScrollFollow({
      container,
      canHandleScroll: () => true,
    });

    scrollToBottom();
    fake.flush();
    await nextTick();
    expect(el.scrollTop).toBe(800);
  });
});

describe('离底判定', () => {
  it('滚离底部时触发 onLeaveBottom', () => {
    installFakeRAF();
    const el = scrollBox(1000, 300);
    el.scrollTop = 400; // 距底 300 > 50
    const onLeaveBottom = vi.fn();
    const { handleScroll } = useAutoScrollFollow({
      container: ref<HTMLElement | null>(el),
      canHandleScroll: () => true,
      onLeaveBottom,
    });

    handleScroll();
    expect(onLeaveBottom).toHaveBeenCalledTimes(1);
  });

  it('贴底时不触发 onLeaveBottom', () => {
    installFakeRAF();
    const el = scrollBox(1000, 300);
    el.scrollTop = 960; // 距底 40 < 50
    const onLeaveBottom = vi.fn();
    const { handleScroll } = useAutoScrollFollow({
      container: ref<HTMLElement | null>(el),
      canHandleScroll: () => true,
      onLeaveBottom,
    });

    handleScroll();
    expect(onLeaveBottom).not.toHaveBeenCalled();
  });

  it('生效门关闭（历史浏览/自由滚动）时跳过判定', () => {
    installFakeRAF();
    const el = scrollBox(1000, 300);
    el.scrollTop = 0;
    const onLeaveBottom = vi.fn();
    const { handleScroll } = useAutoScrollFollow({
      container: ref<HTMLElement | null>(el),
      canHandleScroll: () => false,
      onLeaveBottom,
    });

    handleScroll();
    expect(onLeaveBottom).not.toHaveBeenCalled();
  });
});

describe('生命周期清理', () => {
  it('组件卸载时取消挂起的动画帧', async () => {
    const fake = installFakeRAF();
    const el = scrollBox(500, 200);
    const Host = defineComponent({
      setup() {
        useAutoScrollFollow({
          container: ref<HTMLElement | null>(el),
          canHandleScroll: () => true,
        });
        return () => '';
      },
    });
    const wrapper = mount(Host);
    // 挂载时的初始滚底占有一个挂起帧
    expect(fake.cancelled).toHaveLength(0);
    wrapper.unmount();
    expect(fake.cancelled).toHaveLength(1);
  });
});
