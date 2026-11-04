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
      <p v-if="selectedId !== null" class="archive-summary mono">
        {{ currentSessionLabel }} · {{ lines.length }} 行
      </p>
      <div v-loading="loading" class="archive-lines">
        <ArchiveLineList
          :items="lines"
          deletable
          :busy-line-id="busyLineId"
          :empty-text="emptyText"
          @delete="removeLine"
        />
      </div>
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
 * 加载状态机在 composables/useArchiveLines（与场次复盘页共用同一套切场语义）。
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { ArchiveLineItem, LiveSessionItem } from '@/types';
import { ledgerApi, liveSessionsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn } from '@/utils/format';
import { useArchiveLines } from '@/composables/useArchiveLines';
import ArchiveLineList from './ArchiveLineList.vue';

const { lines, loading, error, load } = useArchiveLines();

const sessions = ref<LiveSessionItem[]>([]);
const sessionsLoading = ref(false);
const selectedId = ref<number | null>(null);
const busyLineId = ref<number | null>(null);
const sessionsError = ref('');
const actionError = ref('');

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
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
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
