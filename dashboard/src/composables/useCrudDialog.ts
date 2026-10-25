/**
 * useCrudDialog —— 主从列表"新增/编辑弹窗 + 删除"CRUD 状态机
 *
 * 收敛同一页面多套实体（如模拟器的人设与礼物）重复的弹窗状态编排：
 * 表单校验 → 按原 ID 判新建/编辑 → 调 API → 成功/业务失败/异常三分支提示
 * → 关弹窗 → 重新拉列表。差异点（表单字段、payload 组装、API 调用、文案）
 * 全部由调用方以参数注入，组合式只管流程。
 *
 * 新建与编辑的 payload 形状允许不同（如礼物新建多 gift_id、编辑不含），
 * 由 createPayload / updatePayload 两个独立组装函数表达。
 */

import { computed, ref, type ComputedRef, type Ref } from 'vue';
import { ElMessage, type FormInstance } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import { getApiErrorMessage } from '@/utils/apiError';

/** 后端 CRUD 接口的统一响应形态（success + 可选 message） */
interface CrudApiResponse {
  data: { success: boolean; message?: string };
}

/** 提示与确认文案组：新建/编辑成功与业务失败文案分开，异常走 getApiErrorMessage 的兜底文案 */
export interface CrudDialogMessages<TRow> {
  /** 新建成功的成功提示（如"已新增"） */
  created: string;
  /** 编辑成功的成功提示（如"已保存"） */
  saved: string;
  /** 新建业务失败（success=false）的 warning 兜底文案 */
  createFailed: string;
  /** 编辑业务失败（success=false）的 warning 兜底文案 */
  saveFailed: string;
  /** 保存请求抛异常时 getApiErrorMessage 的兜底文案 */
  saveError: string;
  /** 删除成功的成功提示 */
  removed: string;
  /** 删除业务失败（success=false）的 warning 兜底文案 */
  removeFailed: string;
  /** 删除请求抛异常时 getApiErrorMessage 的兜底文案 */
  removeError: string;
  /** 删除确认框正文（含行数据的定制文案） */
  removeConfirm: (row: TRow) => string;
}

/** 被动 API 调用组：新建与编辑 payload 类型分开，允许形状不对称 */
export interface CrudDialogApi<TCreatePayload, TUpdatePayload> {
  create: (payload: TCreatePayload) => Promise<CrudApiResponse>;
  update: (id: string, payload: TUpdatePayload) => Promise<CrudApiResponse>;
  remove: (id: string) => Promise<CrudApiResponse>;
}

export interface UseCrudDialogOptions<TForm extends object, TRow, TCreatePayload, TUpdatePayload> {
  /** 响应式表单对象（由调用方 reactive 创建并绑定到 el-form） */
  form: TForm;
  /** 表单空白态工厂：打开弹窗时 Object.assign 重置用 */
  emptyForm: () => TForm;
  /** el-form 实例引用，保存前触发校验 */
  formRef: Ref<FormInstance | undefined>;
  /** 从行数据取实体主键（人设 user_id / 礼物 gift_id） */
  rowId: (row: TRow) => string;
  /** 编辑态把行数据填进表单的字段映射（新建态不需要） */
  fillForm: (row: TRow, form: TForm) => void;
  /** 新建 payload 组装（可含编辑态没有的字段） */
  createPayload: (form: TForm) => TCreatePayload;
  /** 编辑 payload 组装（可省略新建态才有的字段） */
  updatePayload: (form: TForm) => TUpdatePayload;
  api: CrudDialogApi<TCreatePayload, TUpdatePayload>;
  /** 保存/删除成功后的列表刷新回调 */
  refetch: () => Promise<void> | void;
  messages: CrudDialogMessages<TRow>;
}

export interface UseCrudDialogReturn<TRow> {
  /** 弹窗显隐，绑定 el-dialog 的 v-model */
  dialogVisible: Ref<boolean>;
  /** 保存请求进行中，绑定保存按钮 loading */
  saving: Ref<boolean>;
  /** 编辑目标的实体主键，空串表示新建；供模板做 gift_id 输入禁用等判断 */
  originalId: Ref<string>;
  /** 是否编辑态 */
  isEdit: ComputedRef<boolean>;
  /** 打开弹窗；传 row 为编辑（回填表单），不传为新建（空白表单） */
  open: (row?: TRow) => void;
  /** 保存：校验通过后按编辑/新建分流调 API，结束后关弹窗并刷新列表 */
  save: () => Promise<void>;
  /** 删除：确认框通过后调 API 并刷新列表 */
  remove: (row: TRow) => Promise<void>;
}

export function useCrudDialog<TForm extends object, TRow, TCreatePayload, TUpdatePayload>(
  options: UseCrudDialogOptions<TForm, TRow, TCreatePayload, TUpdatePayload>,
): UseCrudDialogReturn<TRow> {
  const {
    form,
    emptyForm,
    formRef,
    rowId,
    fillForm,
    createPayload,
    updatePayload,
    api,
    refetch,
    messages,
  } = options;

  const dialogVisible = ref(false);
  const saving = ref(false);
  const originalId = ref('');
  const isEdit = computed(() => !!originalId.value);

  function open(row?: TRow): void {
    originalId.value = row ? rowId(row) : '';
    Object.assign(form, emptyForm());
    if (row) fillForm(row, form);
    dialogVisible.value = true;
  }

  async function save(): Promise<void> {
    const valid = await formRef.value?.validate().catch(() => false);
    if (!valid) return;
    saving.value = true;
    try {
      if (originalId.value) {
        const res = await api.update(originalId.value, updatePayload(form));
        if (res.data.success) {
          ElMessage.success(messages.saved);
        } else {
          ElMessage.warning(res.data.message || messages.saveFailed);
        }
      } else {
        const res = await api.create(createPayload(form));
        if (res.data.success) {
          ElMessage.success(messages.created);
        } else {
          ElMessage.warning(res.data.message || messages.createFailed);
        }
      }
      dialogVisible.value = false;
      await refetch();
    } catch (err) {
      ElMessage.error(getApiErrorMessage(err, messages.saveError));
    } finally {
      saving.value = false;
    }
  }

  async function remove(row: TRow): Promise<void> {
    const ok = await confirmAction(messages.removeConfirm(row), '删除确认');
    if (!ok) return;
    try {
      const res = await api.remove(rowId(row));
      if (res.data.success) {
        ElMessage.success(messages.removed);
      } else {
        ElMessage.warning(res.data.message || messages.removeFailed);
      }
      await refetch();
    } catch (err) {
      ElMessage.error(getApiErrorMessage(err, messages.removeError));
    }
  }

  return { dialogVisible, saving, originalId, isEdit, open, save, remove };
}
