<template>
  <section class="panel settle-panel" aria-label="下播整理状态">
    <header class="panel-head">
      <div class="head-text">
        <div class="panel-title">下播整理</div>
        <p class="panel-hint">
          每场正式场收播后，主播会把这一场整理成承诺与档案；失败或想重做的场次可重新排队整理。
          列表只列最近 20 场正式场，待整理数含更早的场次。
        </p>
      </div>
      <div class="head-actions">
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </header>

    <p v-if="loadError" class="panel-error">{{ loadError }}</p>

    <div v-if="state" class="settle-summary">
      <span class="chip mono">待整理 {{ state.pending_count }}</span>
      <span class="chip mono">待审 {{ state.review_pending_count }}</span>
      <span class="chip mono">自动重试上限 {{ state.max_attempts }} 次</span>
      <span class="chip mono">最近正式场 {{ state.items.length }}</span>
    </div>

    <ul v-loading="loading" class="settle-list">
      <li v-for="row in state?.items ?? []" :key="row.live_session_id" class="settle-item">
        <div class="settle-head">
          <span class="settle-title">{{ row.title || `场次 #${row.live_session_id}` }}</span>
          <span class="settle-time mono">
            {{ formatDateTimeCn(row.started_at_ms) }}
            <template v-if="row.ended_at_ms !== null">
              – {{ formatRangeEnd(row.started_at_ms, row.ended_at_ms) }}
            </template>
          </span>
          <el-tag size="small" :type="row.settled_at_ms !== null ? 'success' : 'warning'">{{
            settleStateLabel(row.settled_at_ms)
          }}</el-tag>
          <el-tag
            v-if="row.settled_at_ms !== null"
            size="small"
            effect="plain"
            :type="row.reviewed_at_ms === null ? 'warning' : 'info'"
            >{{ reviewStateLabel(row.reviewed_at_ms) }}</el-tag
          >
          <span
            class="settle-attempts mono"
            :class="{ 'is-exhausted': isExhausted(row) }"
            :title="
              isExhausted(row)
                ? `自动重试已用满 ${state?.max_attempts} 次，调度不会再挑这一场`
                : '自动重试次数'
            "
            >{{ settleAttemptsText(row.settle_attempts, state?.max_attempts ?? 0) }}</span
          >
          <span class="settle-archive mono">档案 {{ row.archive_count }} 行</span>
          <span class="grow" />
          <el-button
            link
            size="small"
            type="primary"
            @click="emit('view-archive', row.live_session_id)"
            >查看该场档案</el-button
          >
          <el-button
            link
            size="small"
            type="warning"
            :loading="resettlingId === row.live_session_id"
            @click="resettle(row)"
            >重新整理</el-button
          >
        </div>
        <p v-if="row.settled_at_ms !== null" class="settle-meta mono">
          整理于 {{ formatDateTimeCn(row.settled_at_ms) }}
        </p>
        <p v-if="row.settle_report?.failure_reason" class="settle-failure">
          失败原因：{{ row.settle_report?.failure_reason }}
        </p>
        <p v-else-if="row.settled_at_ms === null && isExhausted(row)" class="settle-failure">
          自动重试用满，这一场需要点「重新整理」才会再排进调度。
        </p>
        <div
          v-if="row.settle_report && (row.settle_report.snapshot_changes ?? []).length > 0"
          class="unregistered"
        >
          <div class="unregistered-title">
            档案快照的改动（{{ row.settle_report.snapshot_changes.length }}
            处，去掉与替换要有本场台词作证据）
          </div>
          <div
            v-for="(change, index) in row.settle_report.snapshot_changes"
            :key="index"
            class="unregistered-item"
          >
            <div class="unregistered-head">
              <el-tag size="small" type="warning" effect="plain">{{
                snapshotChangeLabel(change.action)
              }}</el-tag>
              <span class="unregistered-what">{{ change.what || '（没有标题）' }}</span>
              <span class="unregistered-reason">{{ change.reason }}</span>
            </div>
            <blockquote v-if="change.quote" class="unregistered-quote">
              “{{ change.quote }}”
            </blockquote>
          </div>
        </div>
        <div
          v-if="row.settle_report && row.settle_report.unregistered.length > 0"
          class="unregistered"
        >
          <div class="unregistered-title">
            未登记的条目（{{ row.settle_report.unregistered.length }} 条，可据此手建承诺）
          </div>
          <div
            v-for="(item, index) in row.settle_report.unregistered"
            :key="index"
            class="unregistered-item"
          >
            <div class="unregistered-head">
              <el-tag size="small" type="primary" effect="plain">{{
                unregisteredKindLabel(item.kind)
              }}</el-tag>
              <span class="unregistered-what">{{ item.what || '（没有标题）' }}</span>
              <span class="unregistered-reason">{{ item.reason }}</span>
            </div>
            <blockquote v-if="item.quote" class="unregistered-quote">“{{ item.quote }}”</blockquote>
          </div>
        </div>
        <p v-else-if="row.settled_at_ms !== null && row.archive_count === 0" class="settle-meta">
          这一场没有登记任何档案行。
        </p>
      </li>
    </ul>

    <p v-if="!loading && !loadError && (state?.items.length ?? 0) === 0" class="empty-hint">
      还没有已结束的正式场。每场正式场收播后会自动整理。
    </p>
  </section>
</template>

<script setup lang="ts">
/**
 * 下播整理面板 —— 最近正式场的整理状态与人工补救入口
 *
 * 后端按场次给整理结果：已整理（settled_at_ms）或待整理，自动重试次数是否
 * 用满，失败原因，以及整理时看懂了却没能登记的条目（原话照原样显示，供运营者
 * 判断要不要手建）。"重新整理"清掉整理完成时刻与重试次数，下一次调度再挑到这一场；
 * 已整理的场次也能重排（整场替换，不写重复行）。之后已有整理好的正式场时，确认框另外
 * 说明：只重算这一场的档案快照，之后几场不重放，要带到简报里得按时间顺序依次重新整理。
 * "查看该场档案"通过 view-archive 事件交给记忆页切到档案面板。
 */
import { onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { SettleListResponse, SettleSessionItem } from '@/types';
import { ledgerApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn, formatRangeEnd } from '@/utils/format';
import {
  hasLaterSettledSession,
  settleAttemptsExhausted,
  settleAttemptsText,
  reviewStateLabel,
  settleStateLabel,
  snapshotChangeLabel,
  unregisteredKindLabel,
} from './ledgerView';

const emit = defineEmits<{ 'view-archive': [liveSessionId: number] }>();

const state = ref<SettleListResponse | null>(null);
const loading = ref(false);
const loadError = ref('');
const resettlingId = ref<number | null>(null);

function isExhausted(row: SettleSessionItem): boolean {
  return settleAttemptsExhausted(row.settle_attempts, state.value?.max_attempts ?? 0);
}

async function reload(): Promise<void> {
  loading.value = true;
  loadError.value = '';
  try {
    state.value = (await ledgerApi.getSettleState()).data;
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '整理状态加载失败');
  } finally {
    loading.value = false;
  }
}

async function resettle(row: SettleSessionItem): Promise<void> {
  const label = row.title || `场次 #${row.live_session_id}`;
  // 之后已有整理好的正式场：重新整理只重算这一场的快照，之后几场不跟着重算，确认框要说清楚
  const laterNote = hasLaterSettledSession(row, state.value?.items ?? [])
    ? '这一场之后已经有整理好的正式场：重新整理只重算这一场自己的档案快照，这一场原来的档案行会' +
      '从之后几场的快照里撤掉，新写的不会进去。要让结果带到下一场的简报里，得按时间顺序把之后几场' +
      '也依次重新整理。'
    : '';
  if (
    !(await confirmAction(
      `把「${label}」重新排进整理队列？重试次数清零、清掉上次的失败原因，下一次调度会重新整理这一场；` +
        '该场已登记的档案行与本场尚未了结的承诺会被这次整理的结果替换，运营者已了结或判误记的承诺保留。' +
        laterNote,
      '重新整理',
    ))
  ) {
    return;
  }
  resettlingId.value = row.live_session_id;
  try {
    const response = await ledgerApi.resettle(row.live_session_id);
    ElMessage.success(response.data.detail || '已排进下一次整理');
    await reload();
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '重新整理失败'));
  } finally {
    resettlingId.value = null;
  }
}

onMounted(() => {
  void reload();
});

defineExpose({ reload });
</script>

<style scoped>
.settle-panel {
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

.panel-error {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
}

.settle-summary {
  display: flex;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.chip {
  padding: 3px 10px;
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-regular);
}

.settle-list {
  list-style: none;
  margin: 0;
  padding: 0;
  min-height: 40px;
}

.settle-item {
  padding: 10px 0;
  border-top: 1px dashed var(--border-color-light);
}

.settle-item:first-child {
  border-top: none;
}

.settle-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.settle-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.settle-time {
  font-size: 11px;
  color: var(--text-secondary);
}

.settle-attempts,
.settle-archive {
  font-size: 11px;
  color: var(--text-secondary);
}

.settle-attempts.is-exhausted {
  color: var(--color-warning);
  font-weight: 600;
}

.grow {
  flex: 1;
}

.settle-meta {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-placeholder);
}

.settle-failure {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--color-danger);
}

.unregistered {
  margin-top: var(--spacing-sm);
  padding: var(--spacing-sm);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
}

.unregistered-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.unregistered-item {
  margin-top: var(--spacing-xs);
  padding-top: var(--spacing-xs);
  border-top: 1px dashed var(--border-color-light);
}

.unregistered-head {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.unregistered-what {
  font-size: 13px;
  color: var(--text-primary);
}

.unregistered-reason {
  font-size: 12px;
  color: var(--text-secondary);
}

.unregistered-quote {
  margin: 4px 0 0;
  padding-left: var(--spacing-sm);
  border-left: 2px solid var(--border-color-light);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-regular);
}

.empty-hint {
  padding: var(--spacing-md);
  text-align: center;
  font-size: 13px;
  color: var(--text-placeholder);
}

@media (max-width: 860px) {
  .panel-head {
    flex-direction: column;
  }
}
</style>
