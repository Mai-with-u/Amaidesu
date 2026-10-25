// useWindowedBuffer（浏览窗口机械工厂）测试
//
// fake fetchPage 驱动，覆盖：实时模式翻页进入窗口模式、窗口上限沿页进方向
// 裁剪对侧（older 裁尾保头 / newer 裁头保尾）、jumpTo 整窗替换、exitWindow
// 复位、loading 守卫、同键新页覆盖基线、sortCompare 注入与 onPageMeta 边车。

import { describe, expect, it, vi } from 'vitest';
import { createWindowedBuffer } from './useWindowedBuffer';
import type { PageResult } from './useWindowedBuffer';

interface Item {
  id: string;
  time: number;
}

/** 构造一条测试条目 */
function makeItem(id: string, time: number): Item {
  return { id, time };
}

/** 条目列表 → 归一化一页结果 */
function page(items: Item[], hasOlder = false, hasNewer = false, meta?: unknown): PageResult<Item> {
  return { items, hasOlder, hasNewer, meta };
}

describe('useWindowedBuffer', () => {
  it('实时模式 fetchOlder：以实时缓冲最旧一条为游标拉页并进入窗口模式', async () => {
    const live = [makeItem('a', 100), makeItem('b', 200)];
    const fetchPage = vi.fn().mockResolvedValue(page([makeItem('z', 50)], true));
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });

    await buf.fetchOlder();

    expect(fetchPage).toHaveBeenCalledWith({ direction: 'older', fromMs: 100 });
    expect(buf.window.value!.map(item => item.id)).toEqual(['z', 'a', 'b']);
    expect(buf.hasOlder.value).toBe(true);
  });

  it('窗口模式 fetchOlder：以窗口最旧一条为游标，超上限裁尾保头', async () => {
    const live = [makeItem('a', 100), makeItem('b', 200)];
    const fetchPage = vi
      .fn()
      .mockResolvedValue(page([makeItem('y', 10), makeItem('z', 20)], false));
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 3,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });

    await buf.fetchOlder();

    expect(fetchPage).toHaveBeenCalledWith({ direction: 'older', fromMs: 100 });
    // 上限 3：归并 = [y, z, a, b]，沿页进方向裁剪对侧——保留靠新侧 [z, a, b]（显示头部）
    expect(buf.window.value!.map(item => item.id)).toEqual(['z', 'a', 'b']);
  });

  it('fetchNewer：以窗口最新一条 +1 为游标，超上限裁头保尾', async () => {
    const live = [makeItem('a', 100), makeItem('b', 200)];
    const fetchPage = vi
      .fn()
      .mockResolvedValue(page([makeItem('x', 300), makeItem('y', 400)], false, false));
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 3,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });

    await buf.fetchNewer();

    expect(fetchPage).toHaveBeenCalledWith({ direction: 'newer', fromMs: 201 });
    // 上限 3：归并 = [a, b, x, y]，沿页进方向裁剪对侧——保留靠旧侧 [a, b, x]（显示尾部）
    expect(buf.window.value!.map(item => item.id)).toEqual(['a', 'b', 'x']);
    expect(buf.hasNewer.value).toBe(false);
  });

  it('fetchOlder 不冲掉已置真的 newer 信号', async () => {
    const live: Item[] = [];
    const fetchPage = vi.fn();
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });
    fetchPage.mockResolvedValueOnce(page([makeItem('a', 100)], true, true));
    await buf.jumpTo(100);
    fetchPage.mockClear();
    fetchPage.mockResolvedValue(page([makeItem('z', 50)], true, false));

    await buf.fetchOlder();

    expect(buf.hasNewer.value).toBe(true);
  });

  it('jumpTo：窗口整体替换为返回页，不做归并裁剪', async () => {
    const live = [makeItem('a', 100)];
    const fetchPage = vi
      .fn()
      .mockResolvedValue(page([makeItem('j2', 200), makeItem('j1', 100)], true, true));
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 1,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });

    await buf.jumpTo(150);

    expect(fetchPage).toHaveBeenCalledWith({ direction: 'jump', fromMs: 150 });
    // 上限 1 也不裁剪：跳转页原样替换（排序去重是调用方的页内职责）
    expect(buf.window.value!.map(item => item.id)).toEqual(['j2', 'j1']);
    expect(buf.hasOlder.value).toBe(true);
    expect(buf.hasNewer.value).toBe(true);
  });

  it('exitWindow：窗口清空、续翻信号复位', async () => {
    const live = [makeItem('a', 100)];
    const fetchPage = vi.fn().mockResolvedValue(page([makeItem('a', 100)], true, true));
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });
    await buf.fetchOlder();
    expect(buf.window.value).not.toBeNull();

    buf.exitWindow();

    expect(buf.window.value).toBeNull();
    expect(buf.hasOlder.value).toBe(true);
    expect(buf.hasNewer.value).toBe(false);
  });

  it('loading 守卫：翻页进行中的后续调用直接跳过，不重复发请求', async () => {
    const live = [makeItem('a', 100)];
    let release!: () => void;
    const gate = new Promise<void>(resolve => {
      release = resolve;
    });
    const fetchPage = vi.fn().mockImplementation(() => gate.then(() => page([makeItem('a', 100)])));
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });

    const first = buf.fetchOlder();
    const second = buf.fetchOlder();
    release();
    await Promise.all([first, second]);

    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(buf.loading.value).toBe(false);
  });

  it('同键新页覆盖基线（基线在前、新页在后）', async () => {
    const live = [makeItem('a', 100)];
    // 新页带回与基线同键但内容更新的条目（id 相同、time 不同）
    const fetchPage = vi.fn().mockResolvedValue(page([makeItem('a', 90)]));
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });

    await buf.fetchOlder();

    expect(buf.window.value).toHaveLength(1);
    expect(buf.window.value![0]!.time).toBe(90);
  });

  it('默认按 entryTime 升序排序', async () => {
    const live = [makeItem('m', 250)];
    // 返回页乱序：归并后按时刻升序
    const fetchPage = vi
      .fn()
      .mockResolvedValue(page([makeItem('c', 300), makeItem('a', 100), makeItem('b', 200)]));
    const buf = createWindowedBuffer<Item>({
      pageSize: 3,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });

    await buf.fetchOlder();

    expect(buf.window.value!.map(item => item.id)).toEqual(['a', 'b', 'm', 'c']);
  });

  it('sortCompare 注入生效：覆盖默认的 entryTime 排序', async () => {
    interface Stamped {
      id: string;
      ts: string;
      ms: number;
    }
    const live: Stamped[] = [{ id: 'mid', ts: '2026-01-01T10:00:00.300', ms: 1000 }];
    // ts 为 ISO 串、ms 为粗粒度毫秒：默认 ms 排序分不出同毫秒先后，注入口按 ts 字典序
    const fetchPage = vi.fn().mockResolvedValue({
      items: [
        { id: 'late', ts: '2026-01-01T10:00:00.500', ms: 1000 },
        { id: 'early', ts: '2026-01-01T10:00:00.100', ms: 1000 },
      ],
      hasOlder: false,
      hasNewer: false,
    });
    const buf = createWindowedBuffer<Stamped>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.ms,
      sortCompare: (a, b) => (a.ts < b.ts ? -1 : 1),
      liveList: () => live,
      fetchPage,
    });

    await buf.fetchOlder();

    expect(buf.window.value!.map(item => item.id)).toEqual(['early', 'mid', 'late']);
  });

  it('onPageMeta：每次成功翻页后以页元数据回调', async () => {
    const live = [makeItem('a', 100)];
    const onPageMeta = vi.fn();
    const fetchPage = vi.fn();
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      onPageMeta,
      fetchPage,
    });
    fetchPage.mockResolvedValueOnce(page([makeItem('a', 100)], true, false, { start: 0 }));
    await buf.fetchOlder();
    fetchPage.mockClear();
    fetchPage.mockResolvedValue(page([makeItem('b', 200)], false, false, { start: 5 }));

    await buf.fetchNewer();
    await buf.jumpTo(300);

    expect(onPageMeta).toHaveBeenCalledTimes(3);
    expect(onPageMeta).toHaveBeenLastCalledWith({ start: 5 });
  });

  it('fetchPage 失败：不写入窗口、解除 loading，不抛错', async () => {
    const live: Item[] = [];
    const fetchPage = vi.fn().mockRejectedValue(new Error('network down'));
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => live,
      fetchPage,
    });

    await expect(buf.fetchOlder()).resolves.toBeUndefined();
    expect(buf.window.value).toBeNull();
    expect(buf.loading.value).toBe(false);
  });

  it('实时缓冲为空时 fetchOlder 跳过（无基线锚点）', async () => {
    const fetchPage = vi.fn();
    const buf = createWindowedBuffer<Item>({
      pageSize: 2,
      maxWindow: 10,
      entryKey: item => item.id,
      entryTime: item => item.time,
      liveList: () => [],
      fetchPage,
    });

    await buf.fetchOlder();

    expect(fetchPage).not.toHaveBeenCalled();
  });
});
