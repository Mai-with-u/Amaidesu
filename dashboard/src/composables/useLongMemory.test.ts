// useLongMemory.ts 长期记忆编辑组合式的测试
//
// 覆盖：读取填充编辑区与已保存内容、改动后 dirty、保存整份覆盖写并清 dirty、
// 切换正式 / 彩排重新读取对应那一份、接口失败写入 error；读取与保存后刷新改动记录、
// 查看某次改动之前的全文、撤回改动覆盖编辑区。

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { memoryApi } from '@/api';
import { useLongMemory } from '@/composables/useLongMemory';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  memoryApi: {
    getLongMemory: vi.fn(),
    saveLongMemory: vi.fn(),
    listLongMemoryHistory: vi.fn(),
    getLongMemorySnapshot: vi.fn(),
    restoreLongMemory: vi.fn(),
  },
}));

const getMock = vi.mocked(memoryApi.getLongMemory);
const saveMock = vi.mocked(memoryApi.saveLongMemory);
const historyMock = vi.mocked(memoryApi.listLongMemoryHistory);
const snapshotMock = vi.mocked(memoryApi.getLongMemorySnapshot);
const restoreMock = vi.mocked(memoryApi.restoreLongMemory);

const ENTRY = {
  id: '1700',
  at_ms: 1_700,
  reason: '收播整理（场次 #3）',
  session_id: 3,
  changes: ['新增〔老梗与口头禅〕床名定成咕咕床'],
  has_snapshot: true,
};

function body(text: string, kind = 'live') {
  return { data: { kind, text, path: `D:/data/memory/${kind}.md`, updated_at_ms: 1_000 } } as never;
}

describe('useLongMemory', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    historyMock.mockResolvedValue({ data: { kind: 'live', items: [ENTRY] } } as never);
  });

  it('load / 编辑 / 保存：编辑区改动标 dirty，保存后以服务端返回为准', async () => {
    getMock.mockResolvedValue(body('## 老梗与口头禅\n- 床名'));
    const memory = useLongMemory();
    await memory.load();
    expect(getMock).toHaveBeenCalledWith('live');
    expect(memory.draft.value).toBe('## 老梗与口头禅\n- 床名');
    expect(memory.dirty.value).toBe(false);

    memory.draft.value = '## 老梗与口头禅\n- 床名定成咕咕床\n';
    expect(memory.dirty.value).toBe(true);
    saveMock.mockResolvedValue(body('## 老梗与口头禅\n- 床名定成咕咕床'));
    expect(await memory.save()).toBe(true);
    expect(saveMock).toHaveBeenCalledWith('live', '## 老梗与口头禅\n- 床名定成咕咕床\n');
    expect(memory.dirty.value).toBe(false);
  });

  it('switchKind：切到彩排读彩排那一份；失败写入 error', async () => {
    getMock.mockResolvedValueOnce(body('彩排里记的', 'rehearsal'));
    const memory = useLongMemory();
    await memory.switchKind('rehearsal');
    expect(getMock).toHaveBeenLastCalledWith('rehearsal');
    expect(memory.kind.value).toBe('rehearsal');
    expect(memory.draft.value).toBe('彩排里记的');

    saveMock.mockRejectedValueOnce(new Error('boom'));
    expect(await memory.save()).toBe(false);
    expect(memory.error.value).toBe('boom');
  });

  it('改动记录：读取后就有；看改之前的全文；撤回覆盖编辑区并刷新记录', async () => {
    getMock.mockResolvedValue(body('现在的笔记'));
    const memory = useLongMemory();
    await memory.load();
    expect(historyMock).toHaveBeenCalledWith('live');
    expect(memory.history.value).toEqual([ENTRY]);

    snapshotMock.mockResolvedValueOnce({
      data: { kind: 'live', entry_id: '1700', text: '改之前' },
    } as never);
    expect(await memory.loadSnapshot('1700')).toBe('改之前');
    expect(snapshotMock).toHaveBeenCalledWith('live', '1700');

    restoreMock.mockResolvedValueOnce(body('改之前'));
    memory.draft.value = '没保存的改动';
    expect(await memory.restore('1700')).toBe(true);
    expect(restoreMock).toHaveBeenCalledWith('live', '1700');
    expect(memory.draft.value).toBe('改之前');
    expect(memory.dirty.value).toBe(false);
    expect(historyMock).toHaveBeenCalledTimes(2);

    snapshotMock.mockRejectedValueOnce(new Error('没有这条'));
    expect(await memory.loadSnapshot('nope')).toBeNull();
    expect(memory.error.value).toBe('没有这条');
  });
});
