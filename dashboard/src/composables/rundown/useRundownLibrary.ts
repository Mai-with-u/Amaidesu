/**
 * 流程单库与编辑器：库列表（删除/复制/设为当前）+ 流程单编辑表单 +
 * 环节二级编辑弹窗。页面私有状态。
 *
 * 编辑保存（upsert）写入存储；保存的是直播运行中的那份流程单时，后端写穿
 * 运行态（进度按环节 id 对齐），页面经既有 rundown.changed 防抖重拉机制
 * 自动刷新——保存后额外的静默 fetchState 只为保证非运行态也即时刷新。
 * 依赖注入：state 引用（读当前指向 id / 从运行快照预填新建）与
 * fetchState（保存/设为当前后同步页面态）由 useRundownState 提供。
 */

import { computed, reactive, ref, type Ref } from 'vue';
import { ElMessage } from 'element-plus';
import { rundownApi } from '@/api';
import { confirmAction } from '@/utils/confirmAction';
import type { RundownDefinition, RundownSegmentView, RundownStateResponse } from '@/types';

export interface UseRundownLibraryOptions {
  state: Ref<RundownStateResponse | null>;
  /** 保存/设为当前后同步页面 state（静默拉取） */
  fetchState: (opts?: { silent?: boolean }) => Promise<void>;
}

export function useRundownLibrary(options: UseRundownLibraryOptions) {
  const { state, fetchState } = options;

  // 流程单库

  const libraryOpen = ref(false);
  const libraryLoading = ref(false);
  const libraryItems = ref<RundownDefinition[]>([]);

  /** 配置当前指向的流程单 id；空串 = 使用内置默认流程单 */
  const currentRundownId = computed(() => state.value?.config.rundown_id ?? '');

  function totalExpectedMs(def: RundownDefinition): number {
    return def.segments.reduce((sum, seg) => sum + (seg.expected_ms || 0), 0);
  }

  async function fetchLibrary(): Promise<void> {
    libraryLoading.value = true;
    try {
      const res = await rundownApi.listRundowns();
      if (!res.data.success) {
        ElMessage.error(res.data.message || '流程单库加载失败');
        return;
      }
      libraryItems.value = res.data.rundowns;
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : '流程单库加载失败');
    } finally {
      libraryLoading.value = false;
    }
  }

  async function openLibrary(): Promise<void> {
    await fetchLibrary();
    libraryOpen.value = true;
  }

  // ---- 编辑器 ----

  const editorOpen = ref(false);
  const editorSaving = ref(false);
  /** 打开时的 rundown_id；空串 = 新建 */
  const editorOriginalId = ref('');
  const editorForm = reactive<{
    rundown_id: string;
    title: string;
    segments: RundownSegmentView[];
  }>({
    rundown_id: '',
    title: '',
    segments: [],
  });

  function fillEditorForm(def: RundownDefinition): void {
    editorForm.rundown_id = def.rundown_id;
    editorForm.title = def.title;
    editorForm.segments = JSON.parse(JSON.stringify(def.segments)) as RundownSegmentView[];
  }

  async function startCreate(): Promise<void> {
    try {
      const res = await rundownApi.getTemplate();
      if (!res.data.success || !res.data.definition) {
        ElMessage.error(res.data.message || '模板加载失败');
        return;
      }
      fillEditorForm(res.data.definition);
      editorForm.rundown_id = `rundown_${Date.now() % 100_000}`;
      editorOriginalId.value = '';
      editorOpen.value = true;
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : '模板加载失败');
    }
  }

  /** 编辑当前配置指向的流程单；未选单或指向不存在时从运行快照预填新建 */
  async function startEditCurrent(): Promise<void> {
    await fetchLibrary();
    const found = currentRundownId.value
      ? libraryItems.value.find(r => r.rundown_id === currentRundownId.value)
      : null;
    if (found) {
      startEdit(found);
      return;
    }
    if (state.value && state.value.segments.length > 0) {
      // 运行中的是内置默认流程单（虚拟存在不写库）：从快照预填，保存即落库
      fillEditorForm({
        rundown_id: `rundown_${Date.now() % 100_000}`,
        title: state.value.snapshot?.title || '未命名流程单',
        segments: state.value.segments,
      });
      editorOriginalId.value = '';
      editorOpen.value = true;
      ElMessage.info('当前使用的是内置默认流程单；保存后将作为新流程单入库');
      return;
    }
    ElMessage.warning('流程单内容尚未加载，请先启动主播 Agent 或从流程单库选择');
  }

  function startEdit(def: RundownDefinition): void {
    fillEditorForm(def);
    editorOriginalId.value = def.rundown_id;
    editorOpen.value = true;
  }

  async function saveEditor(): Promise<void> {
    if (!editorForm.rundown_id.trim()) {
      ElMessage.warning('请填写流程单 ID');
      return;
    }
    if (!editorForm.title.trim()) {
      ElMessage.warning('请填写流程单标题');
      return;
    }
    if (editorForm.segments.length === 0) {
      ElMessage.warning('至少需要一个环节');
      return;
    }
    editorSaving.value = true;
    try {
      const res = await rundownApi.upsert({
        rundown_id: editorForm.rundown_id.trim(),
        title: editorForm.title.trim(),
        segments: editorForm.segments,
      });
      if (!res.data.success) {
        ElMessage.error(res.data.message || '保存失败');
        return;
      }
      ElMessage.success(res.data.message || '已保存');
      editorOpen.value = false;
      // 写穿后 rundown.changed 会触发防抖重拉；这里主动刷新保证非运行态也即时
      void fetchState({ silent: true });
      if (libraryOpen.value) await fetchLibrary();
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      editorSaving.value = false;
    }
  }

  // ---- 环节卡片操作 ----

  function moveSegment(idx: number, dir: -1 | 1): void {
    const target = idx + dir;
    if (target < 0 || target >= editorForm.segments.length) return;
    const segs = editorForm.segments;
    [segs[idx], segs[target]] = [segs[target], segs[idx]];
  }

  function removeSegment(idx: number): void {
    if (editorForm.segments.length <= 1) return;
    editorForm.segments.splice(idx, 1);
  }

  // ---- 环节二级编辑 ----

  const segDialogOpen = ref(false);
  const segEditingIndex = ref(-1);
  const segForm = reactive({
    id: '',
    title: '',
    task_description: '',
    keyPointsText: '',
    expectedMinutes: 5,
    minMinutes: null as number | null,
    notes: '',
  });

  const MINUTES_MS = 60_000;

  function openSegmentDialog(idx: number): void {
    segEditingIndex.value = idx;
    if (idx >= 0) {
      const seg = editorForm.segments[idx];
      segForm.id = seg.id;
      segForm.title = seg.title;
      segForm.task_description = seg.task_description;
      segForm.keyPointsText = seg.key_points.join('\n');
      segForm.expectedMinutes = Math.round((seg.expected_ms / MINUTES_MS) * 10) / 10;
      segForm.minMinutes =
        seg.min_duration_ms != null
          ? Math.round((seg.min_duration_ms / MINUTES_MS) * 10) / 10
          : null;
      segForm.notes = seg.notes ?? '';
    } else {
      segForm.id = `segment_${editorForm.segments.length + 1}`;
      segForm.title = '';
      segForm.task_description = '';
      segForm.keyPointsText = '';
      segForm.expectedMinutes = 5;
      segForm.minMinutes = null;
      segForm.notes = '';
    }
    segDialogOpen.value = true;
  }

  function saveSegmentDialog(): void {
    if (!segForm.id.trim()) {
      ElMessage.warning('请填写环节 ID');
      return;
    }
    if (!segForm.title.trim()) {
      ElMessage.warning('请填写环节名');
      return;
    }
    const duplicate = editorForm.segments.some(
      (seg, i) => seg.id === segForm.id.trim() && i !== segEditingIndex.value,
    );
    if (duplicate) {
      ElMessage.warning(`环节 ID "${segForm.id.trim()}" 已存在`);
      return;
    }
    if (
      segForm.minMinutes != null &&
      segForm.expectedMinutes != null &&
      segForm.minMinutes > segForm.expectedMinutes
    ) {
      ElMessage.warning('最短停留不能大于预期时长');
      return;
    }

    const segment: RundownSegmentView = {
      id: segForm.id.trim(),
      title: segForm.title.trim(),
      task_description: segForm.task_description.trim(),
      key_points: segForm.keyPointsText
        .split('\n')
        .map(line => line.trim())
        .filter(Boolean),
      expected_ms: Math.max(1000, Math.round((segForm.expectedMinutes ?? 0.1) * MINUTES_MS)),
      min_duration_ms:
        segForm.minMinutes != null
          ? Math.max(1000, Math.round(segForm.minMinutes * MINUTES_MS))
          : null,
      notes: segForm.notes.trim() || null,
    };
    if (segEditingIndex.value >= 0) {
      editorForm.segments[segEditingIndex.value] = segment;
    } else {
      editorForm.segments.push(segment);
    }
    segDialogOpen.value = false;
  }

  // ---- 库操作：删除 / 复制 / 设为当前 ----

  async function removeRundown(def: RundownDefinition): Promise<void> {
    const referenced = def.rundown_id === currentRundownId.value;
    const ok = await confirmAction(
      referenced
        ? `确定删除「${def.title}」？当前配置仍指向它，重启主播 Agent 后将回退内置默认流程单。`
        : `确定删除「${def.title}」？`,
      '删除流程单',
      { confirmButtonText: '删除' },
    );
    if (!ok) return;
    try {
      const res = await rundownApi.remove(def.rundown_id);
      if (!res.data.success) {
        ElMessage.error(res.data.message || '删除失败');
        return;
      }
      ElMessage.success(res.data.message || '已删除');
      await fetchLibrary();
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : '删除失败');
    }
  }

  async function duplicateRundown(def: RundownDefinition): Promise<void> {
    try {
      const res = await rundownApi.duplicate(def.rundown_id);
      if (!res.data.success) {
        ElMessage.error(res.data.message || '复制失败');
        return;
      }
      ElMessage.success(`已复制为 ${res.data.rundown_id}`);
      await fetchLibrary();
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : '复制失败');
    }
  }

  async function activateRundown(def: RundownDefinition): Promise<void> {
    try {
      const res = await rundownApi.activate(def.rundown_id);
      if (!res.data.success) {
        ElMessage.error(res.data.message || '设置失败');
        return;
      }
      ElMessage.success(res.data.message || '已设为当前');
      // 同步刷新页面 state：当前标记 / 按钮禁用 / 删除确认文案都读 config.rundown_id，
      // 只刷库列表会让它们停留在旧值
      await Promise.all([fetchLibrary(), fetchState({ silent: true })]);
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : '设置失败');
    }
  }

  return {
    libraryOpen,
    libraryLoading,
    libraryItems,
    currentRundownId,
    totalExpectedMs,
    fetchLibrary,
    openLibrary,
    editorOpen,
    editorSaving,
    editorOriginalId,
    editorForm,
    startCreate,
    startEditCurrent,
    startEdit,
    saveEditor,
    moveSegment,
    removeSegment,
    segDialogOpen,
    segEditingIndex,
    segForm,
    openSegmentDialog,
    saveSegmentDialog,
    removeRundown,
    duplicateRundown,
    activateRundown,
  };
}
