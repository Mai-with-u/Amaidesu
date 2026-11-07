// @vitest-environment happy-dom
// useLaneScroll.ts 双轨贴底跟随的测试：挂载即落底、换批条目即跟随
// （条目长度不变也跟随——缓冲满 MAX_ENTRIES 后长度恒定，按长度监听会停摆）、
// 用户上翻后该列不再跟随并计入待看条数、滚回底部恢复跟随、
// "回到最新"让两列都回到底部并恢复跟随、列高变化时维持贴底。
//
// 滚动几何用 defineProperty 覆写模拟（happy-dom 不做真实布局）：列高可变，
// 以便模拟"内容又长高了"。ResizeObserver 用桩替换并保留实例供手动触发。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, nextTick, onMounted, ref, type Ref } from 'vue';
import { mount, type VueWrapper } from '@vue/test-utils';

import { useLaneScroll } from '@/composables/live/useLaneScroll';
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

/** 列的滚动几何：内容高与视口高可调；scrollTop 一并接管（happy-dom 原生 setter 不保留任意赋值） */
interface ColumnBox {
  contentHeight: number;
  viewHeight: number;
}

function stubGeometry(el: HTMLElement, box: ColumnBox): void {
  let scrollTopValue = 0;
  Object.defineProperty(el, 'scrollHeight', {
    get: () => box.contentHeight,
    configurable: true,
  });
  Object.defineProperty(el, 'clientHeight', {
    get: () => box.viewHeight,
    configurable: true,
  });
  Object.defineProperty(el, 'scrollTop', {
    get: () => scrollTopValue,
    set: (v: number) => {
      scrollTopValue = v;
    },
    configurable: true,
  });
}

interface Exposed {
  stageCol: Ref<HTMLElement | null>;
  gameCol: Ref<HTMLElement | null>;
  onScroll: (lane: 'stage' | 'game') => void;
  stopFollowing: (lane: 'stage' | 'game') => void;
  scrollToLatest: () => void;
  pendingCount: Ref<number>;
}

const stageBox: ColumnBox = { contentHeight: 1000, viewHeight: 100 };
const gameBox: ColumnBox = { contentHeight: 1000, viewHeight: 100 };

function makeHost(
  entries: Ref<ShowEntry[]>,
  out: Partial<Exposed>,
): ReturnType<typeof defineComponent> {
  return defineComponent({
    setup() {
      const lane = useLaneScroll(entries);
      Object.assign(out, lane);
      // 组合式的 onMounted 要 await nextTick 后才落底；宿主先打几何桩，
      // 保证它读到覆写后的列高
      onMounted(() => {
        if (out.stageCol?.value) stubGeometry(out.stageCol.value, stageBox);
        if (out.gameCol?.value) stubGeometry(out.gameCol.value, gameBox);
      });
      return () =>
        h('div', [
          h('div', { ref: out.stageCol, id: 'stage-col' }),
          h('div', { ref: out.gameCol, id: 'game-col' }),
        ]);
    },
  });
}

describe('useLaneScroll', () => {
  let wrapper: VueWrapper | null = null;
  let entries: Ref<ShowEntry[]>;
  let out: Partial<Exposed>;

  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);
    ResizeObserverStub.instances = [];
    stageBox.contentHeight = 1000;
    gameBox.contentHeight = 1000;
    entries = ref<ShowEntry[]>([entry('1'), entry('2')]);
    out = {};
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    vi.unstubAllGlobals();
  });

  async function mountHost(): Promise<{ stage: HTMLElement; game: HTMLElement }> {
    wrapper = mount(makeHost(entries, out));
    const stage = wrapper.find('#stage-col').element as HTMLElement;
    const game = wrapper.find('#game-col').element as HTMLElement;
    await nextTick();
    await nextTick();
    return { stage, game };
  }

  it('挂载后两列都落在底部', async () => {
    const { stage, game } = await mountHost();
    expect(stage.scrollTop).toBe(1000);
    expect(game.scrollTop).toBe(1000);
  });

  it('条目换批且长度不变时仍然跟随（缓冲满额后的真实情形）', async () => {
    const { stage, game } = await mountHost();
    // 内容又长高了：新条目挤掉旧条目，条目总数不变
    stageBox.contentHeight = 2000;
    gameBox.contentHeight = 2000;

    entries.value = [...entries.value.slice(1), entry('new')];
    await nextTick();
    await nextTick();

    expect(stage.scrollTop).toBe(2000);
    expect(game.scrollTop).toBe(2000);
  });

  it('条目换批且长度增加时同样跟随', async () => {
    const { stage } = await mountHost();
    stageBox.contentHeight = 2000;
    entries.value = [...entries.value, entry('new')];
    await nextTick();
    await nextTick();
    expect(stage.scrollTop).toBe(2000);
  });

  it('用户上翻后该列不再跟随，另一列照常跟随', async () => {
    const { stage, game } = await mountHost();
    stage.scrollTop = 0;
    out.onScroll!('stage');
    stageBox.contentHeight = 2000;
    gameBox.contentHeight = 2000;

    entries.value = [...entries.value.slice(1), entry('new')];
    await nextTick();
    await nextTick();

    expect(stage.scrollTop).toBe(0);
    expect(game.scrollTop).toBe(2000);
  });

  it('滚回底部后恢复跟随', async () => {
    const { stage } = await mountHost();
    stage.scrollTop = 0;
    out.onScroll!('stage');
    stageBox.contentHeight = 2000;

    entries.value = [...entries.value.slice(1), entry('new')];
    await nextTick();
    await nextTick();
    expect(stage.scrollTop).toBe(0);

    // 用户滚回底部：跟随开关恢复
    stage.scrollTop = 2000;
    out.onScroll!('stage');
    entries.value = [...entries.value.slice(1), entry('newer')];
    await nextTick();
    await nextTick();
    expect(stage.scrollTop).toBe(2000);
  });

  it('显式停止跟随后不再跟随', async () => {
    const { stage } = await mountHost();
    out.stopFollowing!('stage');
    stageBox.contentHeight = 2000;
    entries.value = [...entries.value.slice(1), entry('new')];
    await nextTick();
    await nextTick();
    expect(stage.scrollTop).toBe(1000);
  });

  it('没在跟随的列把落下的新内容计入待看条数', async () => {
    const { stage } = await mountHost();
    stage.scrollTop = 0;
    out.onScroll!('stage');

    entries.value = [...entries.value.slice(1), entry('new')];
    await nextTick();
    await nextTick();

    expect(out.pendingCount!.value).toBe(1);
  });

  it('跟随中的列不计入待看条数', async () => {
    await mountHost();
    entries.value = [...entries.value, entry('new')];
    await nextTick();
    await nextTick();
    expect(out.pendingCount!.value).toBe(0);
  });

  it('滚回底部后待看条数清零', async () => {
    const { stage } = await mountHost();
    stage.scrollTop = 0;
    out.onScroll!('stage');
    entries.value = [...entries.value.slice(1), entry('new')];
    await nextTick();
    await nextTick();
    expect(out.pendingCount!.value).toBe(1);

    stage.scrollTop = 1000;
    out.onScroll!('stage');
    expect(out.pendingCount!.value).toBe(0);
  });

  it('回到最新：两列都落到底部、恢复跟随并清零待看条数', async () => {
    const { stage, game } = await mountHost();
    stage.scrollTop = 0;
    game.scrollTop = 0;
    out.onScroll!('stage');
    out.onScroll!('game');
    entries.value = [...entries.value.slice(1), entry('new')];
    await nextTick();
    await nextTick();
    expect(out.pendingCount!.value).toBe(2);

    out.scrollToLatest!();
    expect(stage.scrollTop).toBe(1000);
    expect(game.scrollTop).toBe(1000);
    expect(out.pendingCount!.value).toBe(0);

    // 恢复跟随后，新条目继续把两列带到底
    stageBox.contentHeight = 2000;
    gameBox.contentHeight = 2000;
    entries.value = [...entries.value.slice(1), entry('newer')];
    await nextTick();
    await nextTick();
    expect(stage.scrollTop).toBe(2000);
    expect(game.scrollTop).toBe(2000);
  });

  it('时间线清空后两列从头跟随、待看条数归零', async () => {
    const { stage } = await mountHost();
    stage.scrollTop = 0;
    out.onScroll!('stage');
    entries.value = [...entries.value.slice(1), entry('new')];
    await nextTick();
    await nextTick();
    expect(out.pendingCount!.value).toBe(1);

    entries.value = [];
    await nextTick();
    await nextTick();
    expect(out.pendingCount!.value).toBe(0);

    // 清空后新内容重新把两列带到底
    stageBox.contentHeight = 2000;
    entries.value = [entry('after')];
    await nextTick();
    await nextTick();
    expect(stage.scrollTop).toBe(2000);
  });

  it('列高变化时维持贴底', async () => {
    const { stage } = await mountHost();
    // 模拟窗口变化后内容变高
    stageBox.contentHeight = 2000;
    ResizeObserverStub.instances[0].callback([], {} as ResizeObserver);
    expect(stage.scrollTop).toBe(2000);
  });

  it('卸载时断开 ResizeObserver', async () => {
    await mountHost();
    const observer = ResizeObserverStub.instances[0];
    expect(observer.observe).toHaveBeenCalledTimes(2);
    wrapper?.unmount();
    wrapper = null;
    expect(observer.disconnect).toHaveBeenCalledTimes(1);
  });
});
