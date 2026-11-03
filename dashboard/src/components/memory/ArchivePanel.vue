<template>
  <section id="ledger-archive" class="panel archive-panel" aria-label="场次档案">
    <header class="panel-head">
      <div class="head-text">
        <div class="panel-title">场次档案</div>
        <p class="panel-hint">
          每场正式场整理出的标题与原话，下一场开播回想“上一场”时用；删一行只影响这一份档案。
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
          :loading="linesLoading"
          :disabled="selectedId === null"
          @click="loadLines"
        >
          刷新
        </el-button>
      </div>
    </header>

    <p v-if="loadError" class="panel-error">{{ loadError }}</p>
    <p v-else-if="selectedId !== null" class="archive-summary mono">
      {{ currentSessionLabel }} · {{ lines.length }} 行
    </p>

    <ArchiveLineList
      :items="lines"
      deletable
      :busy-line-id="busyLineId"
      :empty-text="emptyText"
      @delete="removeLine"
    />
  </section>
</template>

<script setup lang="ts">
/**
 * 场次档案面板 —— 按场次查看档案行，可删单行
 *
 * 场次候选来自 live-sessions 列表里已结束的正式场（新的在前）。父页面通过
 * focusSessionId 指定要看哪一场（整理状态面板的"查看该场档案"入口），选中后
 * 拉取该场档案行；删除单行先二次确认，成功后原地重拉这一场的档案。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import type { ArchiveLineItem, LiveSessionItem } from '@/types';
import { ledgerApi, liveSessionsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn } from '@/utils/format';
import ArchiveLineList from './ArchiveLineList.vue';

const props = withDefaults(defineProps<{ focusSessionId?: number | null }>(), {
  focusSessionId: null,
});

const sessions = ref<LiveSessionItem[]>([]);
const sessionsLoading = ref(false);
const selectedId = ref<number | null>(null);
const lines = ref<ArchiveLineItem[]>([]);
const linesLoading = ref(false);
const busyLineId = ref<number | null>(null);
const loadError = ref('');

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
  loadError.value = '';
  try {
    const response = await liveSessionsApi.list({ kind: 'live', limit: 60 });
    sessions.value = response.data.items.filter(item => item.ended_at_ms !== null);
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '场次列表加载失败');
  } finally {
    sessionsLoading.value = false;
  }
}

async function loadLines(): Promise<void> {
  if (selectedId.value === null) {
    lines.value = [];
    return;
  }
  linesLoading.value = true;
  loadError.value = '';
  try {
    lines.value = (await ledgerApi.listArchive(selectedId.value)).data.items;
  } catch (error) {
    lines.value = [];
    loadError.value = getApiErrorMessage(error, '场次档案加载失败');
  } finally {
    linesLoading.value = false;
  }
}

function selectSession(liveSessionId: number): void {
  if (selectedId.value === liveSessionId) {
    void loadLines();
    return;
  }
  selectedId.value = liveSessionId;
  void loadLines();
}

function onSessionChange(value: number | undefined): void {
  if (typeof value !== 'number') return;
  selectSession(value);
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
  busyLineId.value = line.id;
  loadError.value = '';
  try {
    await ledgerApi.deleteArchiveLine(line.id);
    ElMessage.success('已删除这一行');
    await loadLines();
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '删除档案行失败');
  } finally {
    busyLineId.value = null;
  }
}

async function reload(): Promise<void> {
  await loadSessions();
  if (
    selectedId.value !== null &&
    !sessions.value.some(item => item.live_session_id === selectedId.value)
  ) {
    selectedId.value = null;
  }
  if (selectedId.value === null) {
    selectedId.value = props.focusSessionId ?? sessions.value[0]?.live_session_id ?? null;
  }
  if (selectedId.value === null) {
    lines.value = [];
    return;
  }
  await loadLines();
}

watch(
  () => props.focusSessionId,
  liveSessionId => {
    if (liveSessionId !== null) selectSession(liveSessionId);
  },
);

onMounted(async () => {
  await loadSessions();
  const target = props.focusSessionId ?? sessions.value[0]?.live_session_id ?? null;
  if (target !== null) selectSession(target);
});

defineExpose({ reload });
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
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
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
