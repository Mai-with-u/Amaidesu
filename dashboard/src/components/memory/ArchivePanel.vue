<template>
  <section id="ledger-archive" class="panel archive-panel" aria-label="场次档案">
    <header class="panel-head">
      <div class="head-text">
        <div class="panel-title">场次档案</div>
        <p class="panel-hint">
          每场正式场整理出的标题与原话，下一场开播回想“上一场”时用。整理自动生效，这里事后审：
          改标题、标稳定会锁住这一行（之后的整理不能改它、删它），漏掉的事可以放回快照或从台词里新增。
          锁住但没放回的行，快照满 12 行时会按规则被挤出简报，不算删掉（还在档案里，回想查得到）；
          要一直留在简报里就放回，放回至多 4 行，满了先取消一条。
        </p>
      </div>
      <div class="head-actions">
        <el-select
          :model-value="selectedId ?? undefined"
          class="session-select"
          size="small"
          placeholder="选择已结束的正式场"
          filterable
          :loading="sessionsLoading"
          @update:model-value="onSessionChange"
        >
          <el-option
            v-for="session in sessions"
            :key="session.live_session_id"
            :label="sessionOptionLabel(session)"
            :value="session.live_session_id"
          />
        </el-select>
        <el-button
          size="small"
          :loading="loading"
          :disabled="selectedId === null"
          @click="refreshCurrent"
        >
          刷新
        </el-button>
      </div>
    </header>

    <p v-if="loadError" class="panel-error">{{ loadError }}</p>
    <template v-else>
      <p v-if="actionError" class="panel-error">{{ actionError }}</p>
      <div v-if="selectedId !== null" class="archive-summary">
        <span class="mono">{{ currentSessionLabel }} · {{ lines.length }} 行</span>
        <el-tag
          v-if="summary?.settled"
          size="small"
          :type="summary.reviewed_at_ms === null ? 'warning' : 'success'"
          >{{ reviewStateLabel(summary.reviewed_at_ms) }}</el-tag
        >
        <span class="grow" />
        <el-button
          size="small"
          :disabled="!summary?.settled || summary.snapshot_session_id === null || pinFull"
          :title="pinFull ? '新增的行也算放回：先取消一条放回' : undefined"
          @click="addOpen = true"
          >从台词新增一行</el-button
        >
        <el-button
          v-if="summary?.settled && summary.reviewed_at_ms === null"
          size="small"
          type="success"
          :loading="reviewing"
          @click="markReviewed"
          >标已审</el-button
        >
      </div>
      <p v-if="summary && summary.snapshot_session_id === null" class="panel-note">
        还没有存下的快照：放回、标稳定、新增要等下一场整理写出快照后再做。
      </p>
      <p v-if="pinnedHint" class="panel-warning">
        {{ pinnedHint }}
      </p>
      <div v-loading="loading" class="archive-lines">
        <ArchiveLineList
          :items="lines"
          deletable
          reviewable
          :can-edit-snapshot="summary?.snapshot_session_id != null"
          :pin-full="pinFull"
          :busy-line-id="busyLineId"
          :empty-text="emptyText"
          @delete="removeLine"
          @edit-title="editTitle"
          @toggle-lasting="toggleLasting"
          @pin="pinLine"
          @unpin="unpinLine"
        />
      </div>
      <ArchiveLineAddDialog
        v-model="addOpen"
        :live-session-id="selectedId"
        @created="refreshCurrent"
      />
    </template>
  </section>
</template>

<script setup lang="ts">
/**
 * 场次档案面板 —— 按场次查看档案行，可删单行
 *
 * 场次候选来自 live-sessions 列表里已结束的正式场（新的在前）。外部入口
 * （整理状态面板的"查看该场档案"）由父页面经 ref 调 `selectSession` 指定场次，
 * 不靠 prop 监听：切场时清空旧行、丢弃先发后到的过期响应，错误态与空态分开。
 * 删除单行先二次确认，成功后原地重拉这一场的档案。
 *
 * 人工审核（整理自动生效，这里事后改）：改标题、标或取消稳定、放回快照、从台词新增一行、标已审，
 * 每样做完都原地重拉这一场；做不了的原因（还没存下快照、原话对不上等）由后端给，显示在面板顶部。
 *
 * 加载状态机在 composables/useArchiveLines（与场次复盘页共用同一套切场语义）。
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { AxiosResponse } from 'axios';
import type { ArchiveLineItem, LedgerMutationResponse, LiveSessionItem } from '@/types';
import { ledgerApi, liveSessionsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn } from '@/utils/format';
import { useArchiveLines } from '@/composables/useArchiveLines';
import ArchiveLineAddDialog from './ArchiveLineAddDialog.vue';
import ArchiveLineList from './ArchiveLineList.vue';
import { isPinFull, pinnedFullHint, reviewStateLabel, validateArchiveTitle } from './ledgerView';

const { lines, summary, loading, error, load } = useArchiveLines();
const addOpen = ref(false);
const reviewing = ref(false);

const sessions = ref<LiveSessionItem[]>([]);
const sessionsLoading = ref(false);
const selectedId = ref<number | null>(null);
const busyLineId = ref<number | null>(null);
const sessionsError = ref('');
const actionError = ref('');

/** 放回满了没有：满了放回、新增都做不了，提示人先取消一条 */
const pinFull = computed<boolean>(() =>
  summary.value
    ? isPinFull(summary.value.snapshot_pinned_count, summary.value.snapshot_pinned_max)
    : false,
);
const pinnedHint = computed<string | null>(() =>
  summary.value
    ? pinnedFullHint(summary.value.snapshot_pinned_count, summary.value.snapshot_pinned_max)
    : null,
);

/** 加载类错误（场次列表 / 档案行）：非空时列表不渲染，避免显示误导性的空态 */
const loadError = computed(() => sessionsError.value || error.value);

const currentSession = computed<LiveSessionItem | null>(
  () => sessions.value.find(item => item.live_session_id === selectedId.value) ?? null,
);

const currentSessionLabel = computed<string>(() =>
  currentSession.value ? sessionOptionLabel(currentSession.value) : `场次 #${selectedId.value}`,
);

const emptyText = computed<string>(() =>
  selectedId.value === null ? '还没有已结束的正式场' : '这一场还没有档案行',
);

function sessionOptionLabel(item: LiveSessionItem): string {
  const title = item.title || `场次 #${item.live_session_id}`;
  return `${title} · ${formatDateTimeCn(item.started_at_ms)}`;
}

async function loadSessions(): Promise<void> {
  sessionsLoading.value = true;
  sessionsError.value = '';
  try {
    const response = await liveSessionsApi.list({ kind: 'live', limit: 60 });
    sessions.value = response.data.items.filter(item => item.ended_at_ms !== null);
  } catch (err) {
    sessionsError.value = getApiErrorMessage(err, '场次列表加载失败');
  } finally {
    sessionsLoading.value = false;
  }
}

/** 切到某一场：父页面（"查看该场档案"）与下拉选择共用；同一场再点等于刷新 */
function selectSession(liveSessionId: number): void {
  const changed = selectedId.value !== liveSessionId;
  selectedId.value = liveSessionId;
  actionError.value = '';
  void load(liveSessionId, { clearLines: changed });
}

function onSessionChange(value: number | undefined): void {
  if (typeof value !== 'number') return;
  selectSession(value);
}

function refreshCurrent(): void {
  if (selectedId.value === null) return;
  actionError.value = '';
  void load(selectedId.value, { clearLines: false });
}

async function removeLine(line: ArchiveLineItem): Promise<void> {
  if (
    !(await confirmAction(
      `删除档案行「${line.text}」？删掉后这一行不会再出现在下一场开播的回想里，也不影响承诺账本。`,
      '删除档案行',
    ))
  ) {
    return;
  }
  const target = selectedId.value;
  if (target === null) return;
  busyLineId.value = line.id;
  actionError.value = '';
  try {
    await ledgerApi.deleteArchiveLine(line.id);
    ElMessage.success('已删除这一行');
    await load(target, { clearLines: false });
  } catch (err) {
    actionError.value = getApiErrorMessage(err, '删除档案行失败');
  } finally {
    busyLineId.value = null;
  }
}

/** 对一行做一次审核改动：转圈只转那一行，成功提示后端给的说明并重拉，失败把原因放到面板顶部 */
async function mutateLine(
  line: ArchiveLineItem,
  action: () => Promise<AxiosResponse<LedgerMutationResponse>>,
  fallback: string,
): Promise<void> {
  const target = selectedId.value;
  if (target === null) return;
  busyLineId.value = line.id;
  actionError.value = '';
  try {
    const { data } = await action();
    ElMessage.success(data.detail || '已保存');
    await load(target, { clearLines: false });
  } catch (err) {
    actionError.value = getApiErrorMessage(err, fallback);
  } finally {
    busyLineId.value = null;
  }
}

async function editTitle(line: ArchiveLineItem): Promise<void> {
  let text = '';
  try {
    const { value } = await ElMessageBox.prompt(
      '改过标题的行之后的整理不能去掉或替换；原话不变，她会照着新标题说。',
      '改标题',
      {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValue: line.text,
        inputValidator: (value: string) => validateArchiveTitle(value ?? '') ?? true,
      },
    );
    text = (value ?? '').trim();
  } catch {
    return;
  }
  await mutateLine(line, () => ledgerApi.editArchiveTitle(line.id, text), '改标题失败');
}

async function toggleLasting(line: ArchiveLineItem): Promise<void> {
  await mutateLine(line, () => ledgerApi.setArchiveLasting(line.id, !line.lasting), '标稳定失败');
}

async function pinLine(line: ArchiveLineItem): Promise<void> {
  await mutateLine(line, () => ledgerApi.pinArchiveLine(line.id), '放回快照失败');
}

/** 取消放回：这一行还在快照里，之后按规则取舍，快照满了可能被挤出简报 */
async function unpinLine(line: ArchiveLineItem): Promise<void> {
  await mutateLine(line, () => ledgerApi.unpinArchiveLine(line.id), '取消放回失败');
}

async function markReviewed(): Promise<void> {
  const target = selectedId.value;
  if (target === null) return;
  reviewing.value = true;
  actionError.value = '';
  try {
    await ledgerApi.markReviewed(target);
    ElMessage.success('已标为已审');
    await load(target, { clearLines: false });
  } catch (err) {
    actionError.value = getApiErrorMessage(err, '标已审失败');
  } finally {
    reviewing.value = false;
  }
}

async function reload(): Promise<void> {
  actionError.value = '';
  await loadSessions();
  if (
    selectedId.value !== null &&
    !sessions.value.some(item => item.live_session_id === selectedId.value)
  ) {
    selectedId.value = null;
  }
  const target = selectedId.value ?? sessions.value[0]?.live_session_id ?? null;
  if (target === null) {
    selectedId.value = null;
    await load(null);
    return;
  }
  const changed = selectedId.value !== target;
  selectedId.value = target;
  await load(target, { clearLines: changed });
}

onMounted(() => {
  void reload();
});

defineExpose({ reload, selectSession });
</script>

<style scoped>
.archive-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding: 14px 16px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.panel-head {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
}

.head-text {
  flex: 1;
  min-width: 0;
}

.panel-title {
  font-size: 15px;
  font-weight: 650;
  color: var(--text-primary);
}

.panel-hint {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.session-select {
  width: 280px;
}

.panel-error {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
}

.archive-summary {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 12px;
  color: var(--text-secondary);
}

.archive-summary .grow {
  flex: 1;
}

.panel-note {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.panel-warning {
  margin: 0;
  font-size: 12px;
  color: var(--color-warning);
}

.archive-lines {
  min-height: 40px;
}

@media (max-width: 860px) {
  .panel-head {
    flex-direction: column;
  }
  .session-select {
    width: 100%;
  }
}
</style>
