// @vitest-environment happy-dom
// useTimelineAnchor.ts 回看时刻锚定的测试：已加载范围直接命中、未覆盖时自动
// 向前翻页直到最旧条目早于目标、翻页到头落在最近可得条目、空时间线降级提示、
// 锚定高亮 id 的设置与超时熄灭。
//
// 条目元素用 data-entry-id 挂在滚动容器里（与 FeedTimeline 的 data 属性同形）；
// ElMessage 桩掉，断言降级分支的提示调用。

import { defineComponent, h, nextTick, ref, type Component, type Ref } from 'vue';
import { mount } from '@vue/test-utils';
import { vi, describe, expect, it, beforeEach } from 'vitest';

import { useTimelineAnchor } from '@/composables/live/useTimelineAnchor';
import type { ShowEntry } from '@/utils/liveFeed';

const messageInfo = vi.fn();
vi.mock('element-plus', () => ({
  ElMessage: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: (...args: unknown[]) => messageInfo(...args),
  },
}));

function entry(id: string, tsMs: number): ShowEntry {
  return { id, kind: 'speech', tsMs, actor: '', text: id } as ShowEntry;
}

interface AnchorOptions {
  entries: Ref<ShowEntry[]>;
  hasMore: Ref<boolean>;
  loadingMore: Ref<boolean>;
  loadOlder: () => Promise<void>;
}

interface Exposed {
  scrollRef: Ref<HTMLElement | null>;
  anchoredId: Ref<string | null>;
  anchorAt: (ms: number) => Promise<void>;
}

/** 宿主组件：滚动容器内按 entries 渲染 data-entry-id 行（同 FeedTimeline 形态） */
function makeHost(options: AnchorOptions, out: Partial<Exposed>): Component {
  return defineComponent({
    setup() {
      const cmp = useTimelineAnchor({ ...options, scrollRoot: out.scrollRef! });
      Object.assign(out, cmp);
      return () =>
        h(
          'div',
          { ref: out.scrollRef, id: 'scroller', style: 'height:100px;overflow:auto' },
          options.entries.value.map(e => h('div', { 'data-entry-id': e.id }, e.id)),
        );
    },
  });
}

async function setup(initial: ShowEntry[], olderPage?: ShowEntry[]) {
  const entries = ref(initial);
  const hasMore = ref(true);
  const loadingMore = ref(false);
  const loadOlder = vi.fn(async () => {
    if (!olderPage || olderPage.length === 0) {
      hasMore.value = false;
      return;
    }
    entries.value = [...olderPage, ...entries.value];
    hasMore.value = false; // 单页夹具：翻一页即到头
  });
  const out: Partial<Exposed> = { scrollRef: ref<HTMLElement | null>(null) };
  mount(makeHost({ entries, hasMore, loadingMore, loadOlder }, out));
  await nextTick();
  return { entries, hasMore, loadOlder, out };
}

describe('useTimelineAnchor', () => {
  beforeEach(() => {
    messageInfo.mockClear();
  });

  it('已加载范围覆盖目标（最旧 <= 目标）：直接命中不翻页', async () => {
    const { loadOlder, out } = await setup([entry('a', 1_000), entry('b', 5_000)]);
    await out.anchorAt!(4_000);
    expect(loadOlder).not.toHaveBeenCalled();
    expect(out.anchoredId!.value).toBe('b');
  });

  it('目标早于最旧条目：自动向前翻页，命中翻页带来的条目', async () => {
    const { loadOlder, out } = await setup([entry('late', 9_000)], [entry('early', 1_000)]);
    await out.anchorAt!(1_000);
    expect(loadOlder).toHaveBeenCalledTimes(1);
    expect(out.anchoredId!.value).toBe('early');
  });

  it('翻页到头仍无更早内容：落到最近可得条目，不提示', async () => {
    const { out } = await setup([entry('late', 9_000)]);
    await out.anchorAt!(1_000);
    expect(out.anchoredId!.value).toBe('late');
    expect(messageInfo).not.toHaveBeenCalled();
  });

  it('目标晚于最末条目：落到最末条目', async () => {
    const { out } = await setup([entry('a', 1_000), entry('b', 5_000)]);
    await out.anchorAt!(99_999);
    expect(out.anchoredId!.value).toBe('b');
  });

  it('空时间线：提示降级且不高亮', async () => {
    const { out } = await setup([]);
    await out.anchorAt!(1_000);
    expect(messageInfo).toHaveBeenCalledTimes(1);
    expect(out.anchoredId!.value).toBeNull();
  });

  it('高亮 id 超时后熄灭', async () => {
    vi.useFakeTimers();
    try {
      const { out } = await setup([entry('a', 1_000)]);
      await out.anchorAt!(500);
      expect(out.anchoredId!.value).toBe('a');
      vi.advanceTimersByTime(4_100);
      expect(out.anchoredId!.value).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
