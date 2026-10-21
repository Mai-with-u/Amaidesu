// useRundownLibrary.ts 流程单库与编辑器的测试：库加载（含业务失败）、
// 新建预填模板（id 清空）、编辑当前指向（命中/内置默认回落/未加载警告）、
// 保存校验与写回、环节移动删除守卫、环节二级弹窗（分钟换算/校验）、
// 删除（确认取消）、复制与设为当前。

import { ref } from 'vue';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { rundownApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useRundownLibrary } from '@/composables/rundown/useRundownLibrary';
import { confirmAction } from '@/utils/confirmAction';
import type { RundownSegmentView, RundownStateResponse } from '@/types';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  rundownApi: {
    listRundowns: vi.fn(),
    getTemplate: vi.fn(),
    upsert: vi.fn(),
    remove: vi.fn(),
    duplicate: vi.fn(),
    activate: vi.fn(),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/utils/confirmAction', () => ({
  confirmAction: vi.fn(),
}));

function segment(id: string, expectedMs = 300_000): RundownSegmentView {
  return {
    id,
    title: `环节${id}`,
    task_description: '',
    expected_ms: expectedMs,
    min_duration_ms: null,
    notes: null,
  };
}

function stateFixture(partial: Partial<RundownStateResponse> = {}): RundownStateResponse {
  return {
    available: true,
    message: null,
    snapshot: null,
    transitions: [],
    segments: [segment('a'), segment('b')],
    config: { rundown_id: '' },
    ...partial,
  };
}

describe('useRundownLibrary', () => {
  const fetchState = vi.fn().mockResolvedValue(undefined);

  function setup(state: RundownStateResponse | null = stateFixture()) {
    return useRundownLibrary({ state: ref(state), fetchState });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    fetchState.mockClear();
  });

  it('fetchLibrary：成功落位；success=false 提示错误；异常透出 detail', async () => {
    const lib = setup();
    vi.mocked(rundownApi.listRundowns).mockResolvedValue({
      data: {
        success: true,
        message: '',
        rundowns: [{ rundown_id: 'r1', title: '单', segments: [] }],
        current_id: 'r1',
      },
    } as never);
    await lib.fetchLibrary();
    expect(lib.libraryItems.value.map(r => r.rundown_id)).toEqual(['r1']);
    expect(lib.libraryLoading.value).toBe(false);

    vi.mocked(rundownApi.listRundowns).mockResolvedValue({
      data: { success: false, message: '库不可用', rundowns: [], current_id: '' },
    } as never);
    await lib.fetchLibrary();
    expect(ElMessage.error).toHaveBeenCalledWith('库不可用');

    vi.mocked(rundownApi.listRundowns).mockRejectedValue({
      response: { data: { detail: '服务重启中' } },
    });
    await lib.fetchLibrary();
    expect(ElMessage.error).toHaveBeenCalledWith('服务重启中');
  });

  it('startCreate：预填模板并清空 id（新建不带 id）', async () => {
    const lib = setup();
    vi.mocked(rundownApi.getTemplate).mockResolvedValue({
      data: {
        success: true,
        message: '',
        definition: { rundown_id: 'tpl', title: '默认流程单', segments: [segment('a')] },
      },
    } as never);

    await lib.startCreate();

    expect(lib.editorOpen.value).toBe(true);
    expect(lib.editorForm.rundown_id).toBe('');
    expect(lib.editorOriginalId.value).toBe('');
    expect(lib.editorForm.title).toBe('默认流程单');
    expect(lib.editorForm.segments).toHaveLength(1);
  });

  it('startEditCurrent：指向命中进编辑；内置默认回落预填；两者皆无警告', async () => {
    vi.mocked(rundownApi.listRundowns).mockResolvedValue({
      data: {
        success: true,
        message: '',
        rundowns: [{ rundown_id: 'rd_1', title: '我的单', segments: [] }],
        current_id: 'rd_1',
      },
    } as never);
    const hit = setup(stateFixture({ config: { rundown_id: 'rd_1' } }));
    await hit.startEditCurrent();
    expect(hit.editorOpen.value).toBe(true);
    expect(hit.editorForm.rundown_id).toBe('rd_1');

    const fallback = setup(
      stateFixture({
        config: { rundown_id: '' },
        snapshot: {
          status: 'running',
          rundown_id: '',
          title: '内置单',
          current: null,
          index: 0,
          total: 2,
          progress_percent: 0,
          paused: false,
        },
      }),
    );
    await fallback.startEditCurrent();
    expect(fallback.editorForm.rundown_id).toBe('');
    expect(fallback.editorForm.title).toBe('内置单');
    expect(ElMessage.info).toHaveBeenCalledWith(
      '当前使用的是内置默认流程单；保存后将作为新流程单入库',
    );

    const empty = setup(stateFixture({ config: { rundown_id: '' }, snapshot: null, segments: [] }));
    await empty.startEditCurrent();
    expect(ElMessage.warning).toHaveBeenCalledWith(
      '流程单内容尚未加载，请先启动主播 Agent 或从流程单库选择',
    );
  });

  it('saveEditor：空标题/零环节拦截；成功写回并同步 state 与打开的库', async () => {
    const lib = setup();
    lib.editorForm.title = '  ';
    await lib.saveEditor();
    expect(ElMessage.warning).toHaveBeenCalledWith('请填写流程单标题');

    lib.editorForm.title = '单';
    await lib.saveEditor();
    expect(ElMessage.warning).toHaveBeenCalledWith('至少需要一个环节');

    lib.editorForm.title = ' 单二 ';
    lib.editorForm.rundown_id = ' rd_9 ';
    lib.editorForm.segments = [segment('a')];
    lib.editorOriginalId.value = 'rd_9';
    vi.mocked(rundownApi.upsert).mockResolvedValue({
      data: { success: true, message: '已保存', rundown_id: 'rd_9' },
    } as never);
    lib.libraryOpen.value = true;
    await lib.saveEditor();

    expect(rundownApi.upsert).toHaveBeenCalledWith({
      rundown_id: 'rd_9',
      title: '单二',
      segments: [segment('a')],
    });
    expect(ElMessage.success).toHaveBeenCalledWith('已保存');
    expect(lib.editorOpen.value).toBe(false);
    expect(fetchState).toHaveBeenCalledWith({ silent: true });
    expect(rundownApi.listRundowns).toHaveBeenCalledTimes(1);
    expect(lib.editorSaving.value).toBe(false);
  });

  it('环节移动与删除守卫：首上移/末下移无效，单环节不可删', () => {
    const lib = setup();
    lib.editorForm.segments = [segment('a'), segment('b'), segment('c')];

    lib.moveSegment(0, -1);
    expect(lib.editorForm.segments.map(s => s.id)).toEqual(['a', 'b', 'c']);

    lib.moveSegment(1, -1);
    expect(lib.editorForm.segments.map(s => s.id)).toEqual(['b', 'a', 'c']);

    lib.moveSegment(2, 1);
    expect(lib.editorForm.segments.map(s => s.id)).toEqual(['b', 'a', 'c']);

    lib.editorForm.segments = [segment('a')];
    lib.removeSegment(0);
    expect(lib.editorForm.segments).toHaveLength(1);
  });

  it('环节弹窗：新建默认 id 按序生成，编辑回填分钟换算', () => {
    const lib = setup();
    lib.editorForm.segments = [segment('a')];

    lib.openSegmentDialog(-1);
    expect(lib.segForm.id).toBe('segment_2');
    expect(lib.segForm.expectedMinutes).toBe(5);
    expect(lib.segDialogOpen.value).toBe(true);

    lib.openSegmentDialog(0);
    expect(lib.segForm.id).toBe('a');
    expect(lib.segForm.expectedMinutes).toBe(5);
  });

  it('saveSegmentDialog：ID/标题必填、ID 查重、最短≤预期校验、分钟换算毫秒', () => {
    const lib = setup();
    lib.editorForm.segments = [segment('a')];
    lib.openSegmentDialog(-1);

    lib.segForm.id = ' ';
    lib.saveSegmentDialog();
    expect(ElMessage.warning).toHaveBeenCalledWith('请填写环节 ID');

    lib.segForm.id = 'b';
    lib.segForm.title = '';
    lib.saveSegmentDialog();
    expect(ElMessage.warning).toHaveBeenCalledWith('请填写环节名');

    lib.segForm.title = '环节b';
    lib.segForm.id = 'a';
    lib.saveSegmentDialog();
    expect(ElMessage.warning).toHaveBeenCalledWith('环节 ID "a" 已存在');

    lib.segForm.id = 'b';
    lib.segForm.expectedMinutes = 3;
    lib.segForm.minMinutes = 5;
    lib.saveSegmentDialog();
    expect(ElMessage.warning).toHaveBeenCalledWith('最短停留不能大于预期时长');

    lib.segForm.expectedMinutes = 2.5;
    lib.segForm.minMinutes = 1;
    lib.segForm.notes = ' 备注 ';
    lib.saveSegmentDialog();
    expect(lib.segDialogOpen.value).toBe(false);
    expect(lib.editorForm.segments[1]).toMatchObject({
      id: 'b',
      title: '环节b',
      expected_ms: 150_000,
      min_duration_ms: 60_000,
      notes: '备注',
    });
  });

  it('removeRundown：确认取消不调接口；确认后删除并刷新库', async () => {
    const lib = setup(stateFixture({ config: { rundown_id: 'r1' } }));
    vi.mocked(rundownApi.listRundowns).mockResolvedValue({
      data: { success: true, message: '', rundowns: [], current_id: '' },
    } as never);

    vi.mocked(confirmAction).mockResolvedValue(false);
    await lib.removeRundown({ rundown_id: 'r1', title: '单', segments: [] });
    expect(confirmAction).toHaveBeenCalledWith(
      '确定删除「单」？当前配置仍指向它，重启主播 Agent 后将回退内置默认流程单。',
      '删除流程单',
      { confirmButtonText: '删除' },
    );
    expect(rundownApi.remove).not.toHaveBeenCalled();

    vi.mocked(confirmAction).mockResolvedValue(true);
    vi.mocked(rundownApi.remove).mockResolvedValue({
      data: { success: true, message: '已删除', rundown_id: 'r1' },
    } as never);
    await lib.removeRundown({ rundown_id: 'r1', title: '单', segments: [] });
    expect(rundownApi.remove).toHaveBeenCalledWith('r1');
    expect(ElMessage.success).toHaveBeenCalledWith('已删除');
    expect(rundownApi.listRundowns).toHaveBeenCalledTimes(1);
  });

  it('duplicate 与 activate：业务失败提示对应文案；activate 同步刷新库与 state', async () => {
    const lib = setup();
    vi.mocked(rundownApi.duplicate).mockResolvedValue({
      data: { success: true, message: '', rundown_id: 'r2' },
    } as never);
    vi.mocked(rundownApi.listRundowns).mockResolvedValue({
      data: { success: true, message: '', rundowns: [], current_id: '' },
    } as never);
    await lib.duplicateRundown({ rundown_id: 'r1', title: '单', segments: [] });
    expect(ElMessage.success).toHaveBeenCalledWith('已复制为 r2');

    vi.mocked(rundownApi.duplicate).mockResolvedValue({
      data: { success: false, message: '源不存在', rundown_id: null },
    } as never);
    await lib.duplicateRundown({ rundown_id: 'r1', title: '单', segments: [] });
    expect(ElMessage.error).toHaveBeenCalledWith('源不存在');

    vi.mocked(rundownApi.activate).mockResolvedValue({
      data: { success: true, message: '已设为当前', rundown_id: 'r1' },
    } as never);
    await lib.activateRundown({ rundown_id: 'r1', title: '单', segments: [] });
    expect(fetchState).toHaveBeenCalledWith({ silent: true });

    vi.mocked(rundownApi.activate).mockResolvedValue({
      data: { success: false, message: '设为当前失败', rundown_id: null },
    } as never);
    await lib.activateRundown({ rundown_id: 'r1', title: '单', segments: [] });
    expect(ElMessage.error).toHaveBeenCalledWith('设为当前失败');
  });
});
