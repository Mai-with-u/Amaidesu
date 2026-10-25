// @vitest-environment happy-dom
// useCrudDialog.ts 通用 CRUD 弹窗状态机的测试
//
// 覆盖：新建走 create / 编辑走 update、业务失败（success=false）走 warning、
// 请求抛异常走 getApiErrorMessage 兜底、成功后关弹窗并刷新列表、
// saving 与 originalId/isEdit 状态流转、删除的确认框取消分支。

import { reactive } from 'vue';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ElMessage } from 'element-plus';
import { useCrudDialog } from '@/composables/useCrudDialog';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/utils/confirmAction', () => ({
  confirmAction: vi.fn(),
}));

vi.mock('@/utils/apiError', () => ({
  getApiErrorMessage: vi.fn((_err: unknown, fallback: string) => `${fallback}:提取自异常`),
}));

interface TestForm {
  name: string;
  level: number;
}

interface TestRow {
  id: string;
  name: string;
}

function ok(message?: string) {
  return { data: { success: true, message } };
}
function rejected(message: string) {
  return { data: { success: false, message } };
}

/** 装配一套最小可用的 CRUD 弹窗组合式（表单字段与 API mock 集中在此，用例只关心流程分支） */
function setup() {
  const form = reactive<TestForm>({ name: '', level: 1 });
  const emptyForm = (): TestForm => ({ name: '', level: 1 });
  const formRef = {
    value: { validate: vi.fn().mockResolvedValue(true) },
  } as never;
  const api = {
    create: vi.fn().mockResolvedValue(ok()),
    update: vi.fn().mockResolvedValue(ok()),
    remove: vi.fn().mockResolvedValue(ok()),
  };
  const refetch = vi.fn().mockResolvedValue(undefined);
  const crud = useCrudDialog<TestForm, TestRow, { name: string }, { level: number }>({
    form,
    emptyForm,
    formRef,
    rowId: row => row.id,
    fillForm: (row, f) => {
      f.name = row.name;
    },
    createPayload: f => ({ name: f.name }),
    updatePayload: f => ({ level: f.level }),
    api,
    refetch,
    messages: {
      created: '已新增',
      saved: '已保存',
      createFailed: '新增失败',
      saveFailed: '保存失败',
      saveError: '保存失败',
      removed: '已删除',
      removeFailed: '删除失败',
      removeError: '删除失败',
      removeConfirm: row => `确认删除「${row.name}」？`,
    },
  });
  return { crud, form, api, refetch };
}

describe('useCrudDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('不传 row 打开为新建：空白表单 + isEdit=false；保存走 create', async () => {
    const { crud, form, api, refetch } = setup();
    form.name = '上次残留';
    crud.open();
    expect(crud.dialogVisible.value).toBe(true);
    expect(crud.isEdit.value).toBe(false);
    expect(form.name).toBe('');

    form.name = '新实体';
    await crud.save();
    expect(api.create).toHaveBeenCalledWith({ name: '新实体' });
    expect(api.update).not.toHaveBeenCalled();
    expect(ElMessage.success).toHaveBeenCalledWith('已新增');
    expect(crud.dialogVisible.value).toBe(false);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('传 row 打开为编辑：回填字段 + isEdit=true；保存走 update 并携带原 ID', async () => {
    const { crud, api } = setup();
    crud.open({ id: 'p1', name: '旧名字' });
    expect(crud.isEdit.value).toBe(true);
    expect(crud.originalId.value).toBe('p1');

    await crud.save();
    expect(api.update).toHaveBeenCalledWith('p1', { level: 1 });
    expect(api.create).not.toHaveBeenCalled();
    expect(ElMessage.success).toHaveBeenCalledWith('已保存');
  });

  it('校验不通过直接返回：不调 API、不置 saving、不关弹窗', async () => {
    const { api, refetch } = setup();
    const bad = useCrudDialog<TestForm, TestRow, { name: string }, { level: number }>({
      form: reactive({ name: '', level: 1 }),
      emptyForm: () => ({ name: '', level: 1 }),
      formRef: { value: { validate: vi.fn().mockRejectedValue(new Error('invalid')) } } as never,
      rowId: row => row.id,
      fillForm: () => {},
      createPayload: () => ({ name: '' }),
      updatePayload: () => ({ level: 1 }),
      api,
      refetch,
      messages: {
        created: '已新增',
        saved: '已保存',
        createFailed: '新增失败',
        saveFailed: '保存失败',
        saveError: '保存失败',
        removed: '已删除',
        removeFailed: '删除失败',
        removeError: '删除失败',
        removeConfirm: () => '',
      },
    });
    bad.open();
    await bad.save();
    expect(api.create).not.toHaveBeenCalled();
    expect(bad.saving.value).toBe(false);
    expect(bad.dialogVisible.value).toBe(true);
    expect(refetch).not.toHaveBeenCalled();
  });

  it('业务失败（success=false）走 warning 分支，仍关弹窗并刷新列表', async () => {
    const { crud, api, refetch } = setup();
    api.create.mockResolvedValue(rejected('名字重复'));
    crud.open();
    await crud.save();
    expect(ElMessage.warning).toHaveBeenCalledWith('名字重复');
    expect(ElMessage.success).not.toHaveBeenCalled();
    expect(crud.dialogVisible.value).toBe(false);
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(crud.saving.value).toBe(false);
  });

  it('编辑与新建的业务失败兜底文案分别取自 saveFailed / createFailed', async () => {
    const { crud, api } = setup();
    api.update.mockResolvedValue(rejected(''));
    crud.open({ id: 'p1', name: 'x' });
    await crud.save();
    expect(ElMessage.warning).toHaveBeenCalledWith('保存失败');

    api.create.mockResolvedValue(rejected(''));
    crud.open();
    await crud.save();
    expect(ElMessage.warning).toHaveBeenCalledWith('新增失败');
  });

  it('请求抛异常走 getApiErrorMessage 分支：弹窗不关、saving 复位', async () => {
    const { crud, api, refetch } = setup();
    api.create.mockRejectedValue(new Error('network down'));
    crud.open();
    await crud.save();
    expect(getApiErrorMessage).toHaveBeenCalledWith(expect.any(Error), '保存失败');
    expect(ElMessage.error).toHaveBeenCalledWith('保存失败:提取自异常');
    expect(crud.dialogVisible.value).toBe(true);
    expect(refetch).not.toHaveBeenCalled();
    expect(crud.saving.value).toBe(false);
  });

  it('saving 在请求期间为 true：异步未决时可观察到', async () => {
    const { crud, api } = setup();
    let resolveCreate: (v: ReturnType<typeof ok>) => void = () => {};
    api.create.mockReturnValue(
      new Promise(resolve => {
        resolveCreate = resolve;
      }),
    );
    crud.open();
    const pending = crud.save();
    // save 先异步等表单校验，冲刷微任务让流程推进到置 saving 的时点
    await Promise.resolve();
    await Promise.resolve();
    expect(crud.saving.value).toBe(true);
    resolveCreate(ok());
    await pending;
    expect(crud.saving.value).toBe(false);
  });

  it('删除：确认通过调 remove(rowId) 并刷新；确认取消则不调 API', async () => {
    const { crud, api, refetch } = setup();
    vi.mocked(confirmAction).mockResolvedValue(true);
    await crud.remove({ id: 'g1', name: '小红花' });
    expect(confirmAction).toHaveBeenCalledWith('确认删除「小红花」？', '删除确认');
    expect(api.remove).toHaveBeenCalledWith('g1');
    expect(ElMessage.success).toHaveBeenCalledWith('已删除');
    expect(refetch).toHaveBeenCalledTimes(1);

    vi.mocked(confirmAction).mockResolvedValue(false);
    await crud.remove({ id: 'g2', name: '小蓝花' });
    expect(api.remove).toHaveBeenCalledTimes(1);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('删除业务失败走 warning，抛异常走 getApiErrorMessage 兜底', async () => {
    const { crud, api } = setup();
    api.remove.mockResolvedValue(rejected('仍被引用'));
    vi.mocked(confirmAction).mockResolvedValue(true);
    await crud.remove({ id: 'g1', name: 'x' });
    expect(ElMessage.warning).toHaveBeenCalledWith('仍被引用');

    api.remove.mockRejectedValue(new Error('boom'));
    await crud.remove({ id: 'g1', name: 'x' });
    expect(ElMessage.error).toHaveBeenCalledWith('删除失败:提取自异常');
  });
});
