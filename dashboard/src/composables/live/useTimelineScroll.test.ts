// @vitest-environment happy-dom
// useTimelineScroll.ts 时间线滚动跟随的测试：挂载即贴底、贴底跟随新条目、
// 上滚后未读计数（含锚点缺失视为全新）、回到最新与清零、容器尺寸变化维持
// 贴底、向上滚轮宽限期内不拽回、卸载断开 ResizeObserver。
//
// 滚动几何用 defineProperty 覆写 scrollHeight/clientHeight 模拟（happy-dom
// 不做真实布局）；ResizeObserver 用桩替换并保留实例引用供手动触发；
// Date.now 用 spy 接管以推进宽限期时钟。

import { defineComponent, h, nextTick, onMounted, ref, type Component, type Ref } from 'vue';
import { mount, type VueWrapper } from '@vue/test-utils';
import { vi, describe, expect, it, beforeEach, afterEach } from 'vitest';

import { useTimelineScroll } from '@/composables/live/useTimelineScroll';
import type { ShowEntry } from '@/utils/liveFeed';

class ResizeObserverStub {
  static instances: ResizeObserverStub[] = [];
  callback: ResizeObserverCallback;
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    ResizeObserverStub.instances.push(this);
  }
}

function entry(id: string): ShowEntry {
  // 只填测试关心的字段，其余按类型缺省（滚动逻辑不读它们）
  return { id, kind: 'speech', tsMs: 1_000, actor: '观众', text: `消息${id}` } as ShowEntry;
}

interface Exposed {
  scrollRef: Ref<HTMLElement | null>;
  unseen: Ref<number>;
  onScroll: () => void;
  jumpToLatest: () => void;
  resetUnseen: () => void;
}

function makeHost(entries: Ref<ShowEntry[]>, out: Partial<Exposed>): Component {
  return defineComponent({
    setup() {
      const cmp = useTimelineScroll(entries);
      Object.assign(out, cmp);
      // 组合式的 onMounted 要 await nextTick 后才贴底；宿主先打几何桩，
      // 保证其读到覆写后的 scrollHeight
      onMounted(() => {
        const el = out.scrollRef?.value;
        if (el) stubGeometry(el);
      });
      return () =>
        h(
          'div',
          { ref: out.scrollRef, id: 'scroller', style: 'height:100px;overflow:auto' },
          String(entries.value.length),
        );
    },
  });
}

/** 覆写元素的滚动几何：scrollHeight=1000、clientHeight=100；scrollTop 一并
 *  接管（happy-dom 的原生 setter 不保留任意赋值） */
function stubGeometry(el: HTMLElement): void {
  let scrollTopValue = 0;
  Object.defineProperty(el, 'scrollHeight', { get: () => 1000, configurable: true });
  Object.defineProperty(el, 'clientHeight', { get: () => 100, configurable: true });
  Object.defineProperty(el, 'scrollTop', {
    get: () => scrollTopValue,
    set: (v: number) => {
      scrollTopValue = v;
    },
    configurable: true,
  });
}

describe('useTimelineScroll', () => {
  let wrapper: VueWrapper | null = null;
  let entries: Ref<ShowEntry[]>;
  let out: Partial<Exposed>;

  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);
    ResizeObserverStub.instances = [];
    entries = ref<ShowEntry[]>([entry('1')]);
    out = {};
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function mountHost(): Promise<HTMLElement> {
    // 几何桩由宿主组件的 onMounted 打（先于组合式 nextTick 后的贴底动作），
    // 此处不得重复打桩——重打会把已写入的 scrollTop 归零
    wrapper = mount(makeHost(entries, out));
    const el = wrapper.find('#scroller').element as HTMLElement;
    await nextTick();
    await nextTick();
    return el;
  }

  it('挂载后自动贴底', async () => {
    // mountHost 内已等过两个 nextTick：onMounted 的贴底动作此刻应已完成
    const el = await mountHost();
    expect(el.scrollTop).toBe(1000);
  });

  it('贴底时新条目自动跟随，未读数保持 0', async () => {
    const el = await mountHost();
    el.scrollTop = 1000;
    entries.value = [...entries.value, entry('2'), entry('3')];
    await nextTick();
    await nextTick();
    expect(el.scrollTop).toBe(1000);
    expect(out.unseen!.value).toBe(0);
  });

  it('上滚后新条目只累加未读数，不强制滚动', async () => {
    const el = await mountHost();
    el.scrollTop = 0;
    out.onScroll!();
    entries.value = [...entries.value, entry('2'), entry('3')];
    await nextTick();
    await nextTick();
    expect(out.unseen!.value).toBe(2);
    expect(el.scrollTop).toBe(0);
  });

  it('锚点缺失（整批替换）按全新条目计数', async () => {
    const el = await mountHost();
    el.scrollTop = 0;
    out.onScroll!();
    entries.value = [entry('a'), entry('b'), entry('c')];
    await nextTick();
    await nextTick();
    expect(out.unseen!.value).toBe(3);
  });

  it('向上滚轮后的宽限期内，新条目不贴底只计未读', async () => {
    const clock = { now: 1_000_000 };
    vi.spyOn(Date, 'now').mockImplementation(() => clock.now);
    const el = await mountHost();
    // 模拟竞态现场：位置已上滚，但 scroll 事件尚未分发（atBottom 仍旧值 true）
    el.scrollTop = 0;
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 }));
    entries.value = [...entries.value, entry('2'), entry('3')];
    await nextTick();
    await nextTick();
    expect(el.scrollTop).toBe(0);
    expect(out.unseen!.value).toBe(2);
  });

  it('宽限期过后恢复贴底跟随', async () => {
    const clock = { now: 1_000_000 };
    vi.spyOn(Date, 'now').mockImplementation(() => clock.now);
    const el = await mountHost();
    el.scrollTop = 0;
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 }));
    entries.value = [...entries.value, entry('2')];
    await nextTick();
    await nextTick();
    expect(el.scrollTop).toBe(0);

    clock.now += 2001; // 超过 SCROLL_UP_GRACE_MS
    entries.value = [...entries.value, entry('3')];
    await nextTick();
    await nextTick();
    expect(el.scrollTop).toBe(1000);
    expect(out.unseen!.value).toBe(0);
  });

  it('滚轮向下不触发宽限，仍贴底跟随', async () => {
    vi.spyOn(Date, 'now').mockImplementation(() => 1_000_000);
    const el = await mountHost();
    el.scrollTop = 0;
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: 100 }));
    entries.value = [...entries.value, entry('2')];
    await nextTick();
    await nextTick();
    expect(el.scrollTop).toBe(1000);
    expect(out.unseen!.value).toBe(0);
  });

  it('真实回到底部后宽限解除，恢复贴底跟随', async () => {
    const clock = { now: 1_000_000 };
    vi.spyOn(Date, 'now').mockImplementation(() => clock.now);
    const el = await mountHost();
    el.scrollTop = 0;
    el.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 }));
    entries.value = [...entries.value, entry('2')];
    await nextTick();
    await nextTick();
    expect(el.scrollTop).toBe(0);

    // 用户滚回底部且 scroll 事件已分发：宽限解除，此后新条目照常贴底
    el.scrollTop = 1000;
    out.onScroll!();
    entries.value = [...entries.value, entry('3')];
    await nextTick();
    await nextTick();
    expect(el.scrollTop).toBe(1000);
    expect(out.unseen!.value).toBe(0);
  });

  it('jumpToLatest：清未读并回到最新', async () => {
    const el = await mountHost();
    el.scrollTop = 0;
    out.onScroll!();
    entries.value = [...entries.value, entry('2')];
    await nextTick();
    await nextTick();
    expect(out.unseen!.value).toBe(1);

    out.jumpToLatest!();
    expect(out.unseen!.value).toBe(0);
    expect(el.scrollTop).toBe(1000);
  });

  it('resetUnseen 清零未读数', async () => {
    await mountHost();
    out.resetUnseen!();
    expect(out.unseen!.value).toBe(0);
  });

  it('容器尺寸变化时维持贴底', async () => {
    const el = await mountHost();
    el.scrollTop = 1000;
    // 模拟窗口变化后内容变高
    Object.defineProperty(el, 'scrollHeight', { get: () => 2000, configurable: true });
    ResizeObserverStub.instances[0].callback([], {} as ResizeObserver);
    expect(el.scrollTop).toBe(2000);
  });

  it('卸载时断开 ResizeObserver', async () => {
    await mountHost();
    const ro = ResizeObserverStub.instances[0];
    expect(ro.observe).toHaveBeenCalledTimes(1);
    wrapper?.unmount();
    wrapper = null;
    expect(ro.disconnect).toHaveBeenCalledTimes(1);
  });
});
