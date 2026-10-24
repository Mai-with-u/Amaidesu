// @vitest-environment happy-dom
// useLogStreamQuery 日志类流式列表共享机械的测试
//
// 覆盖：显示列表窗口优先、谓词全命中语义、countBy 基于显示列表、
// 翻页/跳转后的滚动锚定方向、谓词闭包读 ref 的响应式追踪。

import { describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';

import { useLogStreamQuery } from './useLogStreamQuery';

interface Row {
  id: number;
  tag: string;
}

function row(id: number, tag: string): Row {
  return { id, tag };
}

function setup(
  liveInitial: Row[],
  windowInitial: Row[] | null,
  predicates: Array<(item: Row) => boolean> = [],
) {
  const live = ref(liveInitial);
  const window = ref(windowInitial);
  const fetchOlder = vi.fn().mockResolvedValue(undefined);
  const fetchNewer = vi.fn().mockResolvedValue(undefined);
  const jumpTo = vi.fn().mockResolvedValue(undefined);
  const listEl = ref<HTMLElement | null>(null);

  const query = useLogStreamQuery<Row>({
    live,
    window,
    fetchOlder,
    fetchNewer,
    jumpTo,
    listEl,
    predicates,
  });
  return { live, window, fetchOlder, fetchNewer, jumpTo, query };
}

describe('显示列表与窗口模式', () => {
  it('窗口为 null 时显示实时缓冲', () => {
    const { query } = setup([row(1, 'a')], null);
    expect(query.displayItems.value.map(r => r.id)).toEqual([1]);
    expect(query.windowActive.value).toBe(false);
  });

  it('窗口非 null 时优先于实时缓冲', () => {
    const { query } = setup([row(1, 'a')], [row(2, 'b')]);
    expect(query.displayItems.value.map(r => r.id)).toEqual([2]);
    expect(query.windowActive.value).toBe(true);
  });
});

describe('筛选与计数', () => {
  it('谓词全部命中才保留（AND 语义）', () => {
    const rows = [row(1, 'x'), row(2, 'y'), row(3, 'x')];
    const { query } = setup(rows, null, [r => r.tag === 'x', r => r.id > 1]);
    expect(query.filteredItems.value.map(r => r.id)).toEqual([3]);
  });

  it('谓词闭包读 ref：筛选状态变化后 filteredItems 跟新', () => {
    const picked = ref('x');
    const { query } = setup([row(1, 'x'), row(2, 'y')], null, [r => r.tag === picked.value]);
    expect(query.filteredItems.value.map(r => r.id)).toEqual([1]);
    picked.value = 'y';
    expect(query.filteredItems.value.map(r => r.id)).toEqual([2]);
  });

  it('countBy 基于显示列表（不裁剪筛选结果）', () => {
    const { query } = setup([row(1, 'x'), row(2, 'y')], [row(3, 'x')]);
    expect(query.countBy(r => r.tag === 'x')).toBe(1);
  });

  it('filteredItems 派生空态（机械方不内置空态，调用方判定）', () => {
    const { query } = setup([row(1, 'x')], null, [r => r.tag === 'none']);
    expect(query.filteredItems.value).toHaveLength(0);
  });
});

describe('翻页与滚动锚定', () => {
  it('loadOlder 调 store.fetchOlder；scrollListTo 把容器滚到顶部', async () => {
    const el = document.createElement('div');
    Object.defineProperty(el, 'scrollHeight', { value: 500 });
    el.scrollTop = 100;
    const listEl = ref<HTMLElement | null>(el);
    const fetchOlder = vi.fn().mockResolvedValue(undefined);
    const query = useLogStreamQuery<Row>({
      live: ref([]),
      window: ref(null),
      fetchOlder,
      fetchNewer: vi.fn(),
      jumpTo: vi.fn(),
      listEl,
      predicates: [],
    });

    query.loadOlder();
    await vi.waitFor(() => expect(el.scrollTop).toBe(0));
    expect(fetchOlder).toHaveBeenCalledTimes(1);
  });

  it('loadNewer 调 store.fetchNewer；scrollListTo bottom 滚到底', async () => {
    const el = document.createElement('div');
    Object.defineProperty(el, 'scrollHeight', { value: 500 });
    const listEl = ref<HTMLElement | null>(el);
    const fetchNewer = vi.fn().mockResolvedValue(undefined);
    const query = useLogStreamQuery<Row>({
      live: ref([]),
      window: ref(null),
      fetchOlder: vi.fn(),
      fetchNewer,
      jumpTo: vi.fn(),
      listEl,
      predicates: [],
    });

    query.loadNewer();
    await vi.waitFor(() => expect(el.scrollTop).toBe(500));
    expect(fetchNewer).toHaveBeenCalledTimes(1);
  });

  it('jump 转发时刻参数并锚定顶部；容器为 null 时锚定零操作不抛错', async () => {
    const jumpTo = vi.fn().mockResolvedValue(undefined);
    const query = useLogStreamQuery<Row>({
      live: ref([]),
      window: ref(null),
      fetchOlder: vi.fn(),
      fetchNewer: vi.fn(),
      jumpTo,
      listEl: ref<HTMLElement | null>(null),
      predicates: [],
    });

    expect(() => query.scrollListTo('top')).not.toThrow();
    query.jump(1234);
    await vi.waitFor(() => expect(jumpTo).toHaveBeenCalledWith(1234));
  });
});
