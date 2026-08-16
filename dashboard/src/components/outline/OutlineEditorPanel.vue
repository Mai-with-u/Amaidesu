<template>
  <div class="outline-editor-panel">
    <!-- 顶部按钮行（嵌在 Tab 里，无页面标题） -->
    <div class="panel-toolbar">
      <el-button :loading="loading" @click="loadOutline">
        <el-icon><Refresh /></el-icon>
        重新加载
      </el-button>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="!loaded || segments.length === 0"
        @click="handleSave"
      >
        <el-icon><Check /></el-icon>
        保存
      </el-button>
    </div>

    <!-- 加载 / 错误 -->
    <div v-if="loading && !loaded" class="loading-container">
      <el-icon class="is-loading" :size="48"><Loading /></el-icon>
      <p>加载大纲中...</p>
    </div>

    <el-alert
      v-else-if="loadError"
      type="error"
      :title="loadError"
      show-icon
      class="error-alert"
      :closable="false"
    />

    <el-alert
      v-else-if="!loaded"
      type="info"
      title="尚未加载大纲"
      description="后端未返回大纲数据。请确认后端已加载 outline_path 配置。"
      show-icon
      :closable="false"
      class="error-alert"
    />

    <!-- 主内容 -->
    <div v-else class="editor-content">
      <!-- 大纲元信息 -->
      <el-card shadow="never" class="meta-card">
        <template #header>
          <div class="card-header-row">
            <el-icon><Document /></el-icon>
            <span class="card-title">大纲元信息</span>
            <el-tag v-if="meta.path" size="small" type="info" effect="plain">{{
              meta.path
            }}</el-tag>
          </div>
        </template>
        <el-form :model="meta" label-width="120px" size="default">
          <el-form-item label="outline_id">
            <el-input v-model="meta.outline_id" placeholder="唯一标识" />
          </el-form-item>
          <el-form-item label="标题">
            <el-input v-model="meta.title" placeholder="大纲标题" />
          </el-form-item>
          <el-form-item label="回退环节">
            <el-select
              v-model="meta.fallback_segment_id"
              placeholder="未设置（默认走完所有环节）"
              clearable
              filterable
              style="width: 100%"
            >
              <el-option
                v-for="seg in segments"
                :key="seg.id"
                :label="`${seg.id} — ${seg.title || '(无标题)'}`"
                :value="seg.id"
              />
            </el-select>
            <div class="field-hint">分支未命中时回退到此环节（可选）</div>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- 环节列表 -->
      <div class="segments-section">
        <div class="section-header">
          <h3 class="section-title">环节列表（{{ segments.length }}）</h3>
          <el-button type="primary" :icon="Plus" @click="addSegment"> 新增环节 </el-button>
        </div>

        <el-empty v-if="segments.length === 0" description="暂无环节，点击右上角新增第一个环节" />

        <el-card
          v-for="(seg, idx) in segments"
          :key="seg.__key"
          shadow="never"
          class="segment-card"
        >
          <template #header>
            <div class="card-header-row">
              <el-tag type="primary" effect="dark" size="small">#{{ idx + 1 }}</el-tag>
              <span class="card-title">{{ seg.title || seg.id || '(未命名环节)' }}</span>
              <span class="card-sub">{{ seg.id || '(无 id)' }}</span>
              <div class="header-spacer" />
              <el-button-group>
                <el-tooltip content="上移" placement="top">
                  <el-button
                    :disabled="idx === 0"
                    :icon="ArrowUp"
                    size="small"
                    @click="moveSegment(idx, -1)"
                  />
                </el-tooltip>
                <el-tooltip content="下移" placement="top">
                  <el-button
                    :disabled="idx === segments.length - 1"
                    :icon="ArrowDown"
                    size="small"
                    @click="moveSegment(idx, 1)"
                  />
                </el-tooltip>
                <el-tooltip content="删除环节" placement="top">
                  <el-button
                    :icon="Delete"
                    size="small"
                    type="danger"
                    @click="removeSegment(idx)"
                  />
                </el-tooltip>
              </el-button-group>
            </div>
          </template>

          <el-form :model="seg" label-width="120px" size="default">
            <el-form-item label="环节 ID" :error="getSegmentIdError(idx)">
              <el-input v-model="seg.id" placeholder="如 intro / main_topic" />
            </el-form-item>
            <el-form-item label="标题">
              <el-input v-model="seg.title" placeholder="环节标题" />
            </el-form-item>
            <el-form-item label="任务描述">
              <el-input
                v-model="seg.task_description"
                type="textarea"
                :rows="3"
                placeholder="给 AI 的任务指引"
              />
            </el-form-item>
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="时长 (ms)" :error="getDurationError(idx)">
                  <el-input-number
                    v-model="seg.duration_ms"
                    :min="1000"
                    :step="1000"
                    controls-position="right"
                    style="width: 100%"
                  />
                  <div class="field-hint">最少 1000 ms（后端 Pydantic ge=1000 校验）</div>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="最少时长 (ms)" :error="getMinDurationError(idx)">
                  <el-input-number
                    v-model="seg.min_duration_ms"
                    :min="1000"
                    :step="1000"
                    controls-position="right"
                    style="width: 100%"
                    placeholder="可选"
                  />
                </el-form-item>
              </el-col>
            </el-row>

            <!-- 关键点 -->
            <el-form-item label="关键点">
              <div class="key-points-list">
                <div v-for="(_, kpIdx) in seg.key_points" :key="kpIdx" class="key-point-row">
                  <el-input v-model="seg.key_points[kpIdx]" size="small" placeholder="关键点" />
                  <el-button
                    type="danger"
                    size="small"
                    text
                    :icon="Delete"
                    @click="removeKeyPoint(idx, kpIdx)"
                  />
                </div>
                <el-button size="small" :icon="Plus" class="add-kp-btn" @click="addKeyPoint(idx)">
                  添加关键点
                </el-button>
              </div>
            </el-form-item>

            <!-- 分支 -->
            <el-form-item label="分支">
              <div class="branches-list">
                <el-empty
                  v-if="seg.branches.length === 0"
                  :image-size="60"
                  description="本环节无分支"
                />
                <div v-for="(branch, brIdx) in seg.branches" :key="brIdx" class="branch-row">
                  <div class="branch-fields">
                    <el-form-item label="branch_id" label-width="80px" size="small">
                      <el-input v-model="branch.branch_id" placeholder="唯一 id" />
                    </el-form-item>
                    <el-form-item label="描述" label-width="60px" size="small">
                      <el-input
                        v-model="branch.description"
                        type="textarea"
                        :rows="2"
                        placeholder="给 LLM 的分支触发条件"
                      />
                    </el-form-item>
                    <el-form-item
                      label="目标环节"
                      label-width="80px"
                      size="small"
                      :error="getBranchTargetError(idx, brIdx)"
                    >
                      <el-select
                        v-model="branch.target_segment_id"
                        placeholder="选择目标环节"
                        filterable
                        style="width: 100%"
                      >
                        <el-option
                          v-for="target in otherSegmentOptions(idx)"
                          :key="target"
                          :label="target"
                          :value="target"
                        />
                      </el-select>
                    </el-form-item>
                  </div>
                  <el-button
                    type="danger"
                    size="small"
                    text
                    :icon="Delete"
                    @click="removeBranch(idx, brIdx)"
                  />
                </div>
                <el-button size="small" :icon="Plus" class="add-branch-btn" @click="addBranch(idx)">
                  添加分支
                </el-button>
              </div>
            </el-form-item>
          </el-form>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, reactive } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import {
  ArrowUp,
  ArrowDown,
  Check,
  Delete,
  Document,
  Loading,
  Plus,
  Refresh,
} from '@element-plus/icons-vue';
import { outlineApi } from '@/api';
import type { OutlineBranchData, OutlineSegmentData, OutlineSegmentsResponse } from '@/types';
import { serializeOutlineToToml } from '@/utils/outlineToml';

const emit = defineEmits<{
  (e: 'reloaded'): void;
}>();

// ── 状态 ──────────────────────────────────────────────────
const loading = ref(false);
const saving = ref(false);
const reloading = ref(false);
const loadError = ref<string | null>(null);
const loaded = ref(false);

const meta = reactive({
  outline_id: '',
  title: '',
  fallback_segment_id: '' as string,
  path: '' as string,
});

const segments = ref<(OutlineSegmentData & { __key: string })[]>([]);

// ── 加载 ──────────────────────────────────────────────────
async function loadOutline() {
  loading.value = true;
  loadError.value = null;
  try {
    const res = await outlineApi.getSegments();
    const data: OutlineSegmentsResponse = res.data;
    applyLoaded(data);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '加载失败';
    loadError.value = `加载大纲失败: ${msg}`;
    ElMessage.error(loadError.value);
  } finally {
    loading.value = false;
  }
}

function applyLoaded(data: OutlineSegmentsResponse) {
  loaded.value = data.loaded;
  if (!data.loaded) {
    segments.value = [];
    meta.outline_id = '';
    meta.title = '';
    meta.fallback_segment_id = '';
    meta.path = data.path ?? '';
    return;
  }
  meta.outline_id = data.outline_id ?? '';
  meta.title = data.title ?? '';
  meta.fallback_segment_id = data.fallback_segment_id ?? '';
  meta.path = data.path ?? '';
  segments.value = (data.segments || []).map(s => ({
    ...s,
    key_points: [...(s.key_points || [])],
    branches: (s.branches || []).map(b => ({ ...b })),
    __key: `seg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  }));
}

onMounted(loadOutline);

// ── 校验（前端基础校验，后端 Pydantic 兜底） ──────────────
const validationErrors = computed<string[]>(() => {
  const errors: string[] = [];
  const ids = new Set<string>();
  segments.value.forEach((seg, idx) => {
    const prefix = `环节 #${idx + 1}`;
    if (!seg.id) {
      errors.push(`${prefix}: id 不能为空`);
    } else if (ids.has(seg.id)) {
      errors.push(`${prefix}: id "${seg.id}" 重复`);
    } else {
      ids.add(seg.id);
    }
    if (!seg.title) errors.push(`${prefix}: 标题不能为空`);
    if (!seg.task_description) errors.push(`${prefix}: 任务描述不能为空`);
    if (!Number.isFinite(seg.duration_ms) || seg.duration_ms < 1000) {
      errors.push(`${prefix}: 时长必须 ≥ 1000 ms（当前 ${seg.duration_ms}）`);
    }
    if (seg.min_duration_ms != null && seg.min_duration_ms !== undefined) {
      if (!Number.isFinite(seg.min_duration_ms) || seg.min_duration_ms < 1000) {
        errors.push(`${prefix}: 最少时长必须 ≥ 1000 ms`);
      } else if (seg.min_duration_ms > seg.duration_ms) {
        errors.push(
          `${prefix}: 最少时长 (${seg.min_duration_ms}) 不能大于时长 (${seg.duration_ms})`,
        );
      }
    }
    seg.branches?.forEach((b, bIdx) => {
      if (!b.branch_id) errors.push(`${prefix} 分支 #${bIdx + 1}: branch_id 不能为空`);
      if (!b.description) errors.push(`${prefix} 分支 #${bIdx + 1}: 描述不能为空`);
      if (!b.target_segment_id) {
        errors.push(`${prefix} 分支 #${bIdx + 1}: 必须选择目标环节`);
      } else if (!ids.has(b.target_segment_id)) {
        errors.push(`${prefix} 分支 #${bIdx + 1}: 目标环节 "${b.target_segment_id}" 不存在`);
      }
    });
  });
  if (meta.fallback_segment_id && !ids.has(meta.fallback_segment_id)) {
    errors.push(`回退环节 "${meta.fallback_segment_id}" 不存在`);
  }
  return errors;
});

function getSegmentIdError(idx: number): string {
  const seg = segments.value[idx];
  if (!seg) return '';
  if (!seg.id) return 'id 不能为空';
  const dup = segments.value.findIndex((s, i) => i !== idx && s.id === seg.id);
  if (dup >= 0) return `与环节 #${dup + 1} id 重复`;
  return '';
}

function getDurationError(idx: number): string {
  const seg = segments.value[idx];
  if (!seg) return '';
  if (!Number.isFinite(seg.duration_ms) || seg.duration_ms < 1000) {
    return '时长必须 ≥ 1000 ms';
  }
  return '';
}

function getMinDurationError(idx: number): string {
  const seg = segments.value[idx];
  if (!seg) return '';
  if (seg.min_duration_ms == null || seg.min_duration_ms === undefined) return '';
  if (!Number.isFinite(seg.min_duration_ms) || seg.min_duration_ms < 1000) {
    return '最少时长必须 ≥ 1000 ms';
  }
  if (seg.min_duration_ms > seg.duration_ms) {
    return '不能大于时长';
  }
  return '';
}

function getBranchTargetError(segIdx: number, brIdx: number): string {
  const seg = segments.value[segIdx];
  if (!seg) return '';
  const b = seg.branches?.[brIdx];
  if (!b) return '';
  if (!b.target_segment_id) return '请选择目标环节';
  const ids = new Set(segments.value.map(s => s.id));
  if (!ids.has(b.target_segment_id)) return '目标环节不存在';
  return '';
}

// ── 环节操作 ──────────────────────────────────────────────
let _segCounter = 0;
function addSegment() {
  _segCounter++;
  segments.value.push({
    id: `segment_${_segCounter}`,
    title: `新环节 ${segments.value.length + 1}`,
    task_description: '',
    duration_ms: 60000,
    min_duration_ms: null,
    key_points: [],
    branches: [],
    __key: `seg-new-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  });
}

function removeSegment(idx: number) {
  const seg = segments.value[idx];
  if (!seg) return;
  const idToRemove = seg.id;
  // 检查是否有其他环节的分支/回退指向它
  const refs = segments.value.filter(
    (s, i) =>
      i !== idx &&
      (s.branches?.some(b => b.target_segment_id === idToRemove) ||
        meta.fallback_segment_id === idToRemove),
  );
  const refNote = refs.length > 0 ? `\n（注意：${refs.length} 个其他环节引用了此 id）` : '';
  ElMessageBox.confirm(`确定删除环节 "${seg.title || seg.id}" 吗？${refNote}`, '删除确认', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    type: 'warning',
  })
    .then(() => {
      segments.value.splice(idx, 1);
      ElMessage.success('环节已删除');
    })
    .catch(() => {
      /* 用户取消 */
    });
}

function moveSegment(idx: number, delta: number) {
  const target = idx + delta;
  if (target < 0 || target >= segments.value.length) return;
  const [item] = segments.value.splice(idx, 1);
  segments.value.splice(target, 0, item);
}

function addKeyPoint(idx: number) {
  const seg = segments.value[idx];
  if (!seg) return;
  if (!seg.key_points) seg.key_points = [];
  seg.key_points.push('');
}

function removeKeyPoint(idx: number, kpIdx: number) {
  const seg = segments.value[idx];
  if (!seg) return;
  seg.key_points.splice(kpIdx, 1);
}

function otherSegmentOptions(excludeIdx: number) {
  return segments.value
    .filter((_, i) => i !== excludeIdx)
    .map(s => s.id)
    .filter(id => !!id);
}

function addBranch(idx: number) {
  const seg = segments.value[idx];
  if (!seg) return;
  if (!seg.branches) seg.branches = [];
  const newBranch: OutlineBranchData = {
    branch_id: `branch_${seg.branches.length + 1}`,
    description: '',
    target_segment_id: '',
  };
  seg.branches.push(newBranch);
}

function removeBranch(segIdx: number, brIdx: number) {
  const seg = segments.value[segIdx];
  if (!seg) return;
  seg.branches.splice(brIdx, 1);
}

// ── 保存（双按钮流程：仅保存 / 保存并重载） ─────────────
async function handleSave() {
  if (validationErrors.value.length > 0) {
    ElMessageBox.alert(
      `请修复以下 ${validationErrors.value.length} 个校验错误后再保存：\n\n${validationErrors.value.join('\n')}`,
      '校验未通过',
      { type: 'warning', confirmButtonText: '好的' },
    );
    return;
  }
  if (!meta.path) {
    ElMessage.error('后端未返回大纲路径，无法保存');
    return;
  }

  const payload = {
    outline_id: meta.outline_id,
    title: meta.title,
    fallback_segment_id: meta.fallback_segment_id || null,
    segments: segments.value.map(s => ({
      id: s.id,
      title: s.title,
      task_description: s.task_description,
      duration_ms: s.duration_ms,
      min_duration_ms: s.min_duration_ms ?? null,
      key_points: (s.key_points || []).filter(k => k && k.trim() !== ''),
      branches: (s.branches || []).map(b => ({
        branch_id: b.branch_id,
        description: b.description,
        target_segment_id: b.target_segment_id,
      })),
    })),
  };

  const toml = serializeOutlineToToml(payload);

  saving.value = true;
  try {
    const res = await outlineApi.saveFile({ path: meta.path, content: toml });
    const data = res.data;
    const savedPath = data.path ?? meta.path;
    const bytes = data.bytes_written;
    ElMessage.success('大纲已保存到磁盘');
    await promptReloadAfterSave(savedPath, bytes);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '保存失败';
    ElMessage.error(`保存失败: ${msg}`);
  } finally {
    saving.value = false;
  }
}

/** 保存成功后询问用户是否立即重新加载（热加载） */
async function promptReloadAfterSave(savedPath: string, bytesWritten?: number): Promise<void> {
  const byteText = bytesWritten != null ? `（${bytesWritten} 字节）` : '';
  try {
    await ElMessageBox.confirm(
      `大纲已保存到磁盘${byteText}。运行时仍在使用旧大纲，是否立即重新加载？`,
      '大纲已保存',
      {
        confirmButtonText: '保存并重载',
        cancelButtonText: '仅保存',
        type: 'info',
        distinguishCancelAndClose: true,
        closeOnClickModal: false,
      },
    );
    // 用户选了「保存并重载」
    await reloadOutlineFromDisk(savedPath);
  } catch (action) {
    // 'cancel' = 仅保存；'close' 同 cancel
    if (action === 'close' || (action && typeof action === 'string')) {
      // 仅保存：什么都不做
    }
  }
}

/** 调 loadOutline(path) 热加载新大纲 */
async function reloadOutlineFromDisk(targetPath: string): Promise<void> {
  reloading.value = true;
  try {
    await outlineApi.loadOutline(targetPath);
    // 重新拉 segments（meta 会随 loadOutline 接口返回而更新；这里也同步刷新）
    await loadOutline();
    ElMessage.success('大纲已重新加载');
    emit('reloaded');
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '重新加载失败';
    ElMessage.error(`重新加载失败: ${msg}`);
  } finally {
    reloading.value = false;
  }
}
</script>

<style scoped>
.outline-editor-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.panel-toolbar {
  display: flex;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--spacing-lg) 0;
  color: var(--text-secondary);
}

.loading-container p {
  margin-top: var(--spacing-md);
}

.error-alert {
  flex-shrink: 0;
}

.editor-content {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.meta-card {
  flex-shrink: 0;
}

.card-header-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.card-title {
  font-weight: 500;
}

.card-sub {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.header-spacer {
  flex: 1;
}

.segments-section {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: var(--spacing-sm);
}

.section-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.segment-card {
  border: 1px solid var(--border-color-light);
  transition: box-shadow var(--transition-fast);
}

.segment-card:hover {
  box-shadow: var(--shadow-md);
}

.field-hint {
  font-size: 11px;
  color: var(--text-placeholder);
  margin-top: 2px;
  line-height: 1.4;
}

.key-points-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  width: 100%;
}

.key-point-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
}

.add-kp-btn {
  align-self: flex-start;
  margin-top: var(--spacing-xs);
}

.branches-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  width: 100%;
}

.branch-row {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm);
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
  border: 1px dashed var(--border-color);
}

.branch-fields {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  flex: 1;
}

.add-branch-btn {
  align-self: flex-start;
  margin-top: var(--spacing-xs);
}
</style>
