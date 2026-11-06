<template>
  <section class="panel candidate-panel" aria-label="观众候选">
    <header class="panel-head">
      <div class="head-text">
        <div class="panel-title">
          观众候选
          <el-tag v-if="pendingCount > 0" size="small" type="warning"
            >{{ pendingCount }} 条待审</el-tag
          >
        </div>
        <p class="panel-hint">
          观众定下、她当场接受了却没自己复述的事，以及她说“欠着”、账上却找不到的事。批准前她完全看不到；
          批准后进记忆，标题可以先改再批。待审的列出后 14 天没人审会自动删掉。
        </p>
      </div>
      <div class="head-actions">
        <el-radio-group v-model="status" size="small" @change="reload">
          <el-radio-button value="pending">待审</el-radio-button>
          <el-radio-button value="approved">已批</el-radio-button>
          <el-radio-button value="rejected">已驳回</el-radio-button>
        </el-radio-group>
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </header>

    <p v-if="loadError" class="panel-error">{{ loadError }}</p>
    <p v-if="actionError" class="panel-error">{{ actionError }}</p>
    <ul v-loading="loading" class="candidate-list">
      <li v-for="item in items" :key="item.id" class="candidate-item">
        <div class="candidate-head">
          <el-tag
            size="small"
            :type="item.kind === 'claimed_missing' ? 'danger' : 'primary'"
            effect="plain"
            >{{ candidateKindLabel(item.kind) }}</el-tag
          >
          <el-input
            v-if="item.status === 'pending'"
            v-model="titles[item.id]"
            size="small"
            class="title-input"
            :maxlength="ARCHIVE_TITLE_MAX_LENGTH"
            show-word-limit
          />
          <span v-else class="candidate-title">{{ item.title }}</span>
          <span v-if="item.game" class="candidate-game mono">{{
            item.save_name ? `${item.game}「${item.save_name}」` : item.game
          }}</span>
        </div>
        <blockquote v-if="item.viewer_quote" class="candidate-quote viewer">
          弹幕：“{{ item.viewer_quote }}”
        </blockquote>
        <blockquote class="candidate-quote">她：“{{ item.reply_quote }}”</blockquote>
        <p
          v-if="item.kind === 'claimed_missing' && item.status === 'pending'"
          class="candidate-nearest"
        >
          {{ nearestCommitmentsText(item.nearest_commitments) }}
        </p>
        <div v-if="item.status === 'pending'" class="candidate-actions">
          <el-checkbox v-if="item.kind === 'viewer_defined'" v-model="lasting[item.id]" size="small"
            >名字、住处、关系这类稳定的事</el-checkbox
          >
          <span class="grow" />
          <el-button
            size="small"
            :loading="busyId === item.id"
            :disabled="busyId !== null && busyId !== item.id"
            @click="reject(item)"
            >驳回</el-button
          >
          <el-button
            size="small"
            type="primary"
            :loading="busyId === item.id"
            :disabled="busyId !== null && busyId !== item.id"
            @click="approve(item)"
            >批准</el-button
          >
        </div>
        <p v-else class="candidate-meta mono">
          {{ candidateStatusLabel(item.status) }}
          <template v-if="item.decided_at_ms !== null">
            · {{ formatDateTimeCn(item.decided_at_ms) }}</template
          >
        </p>
      </li>
      <li v-if="!loading && !loadError && items.length === 0" class="empty-hint">
        {{ status === 'pending' ? '没有待审的候选' : '没有记录' }}
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
/**
 * 观众候选审核区 —— 观众定下的事、她说欠着的事，人批准才进记忆
 *
 * 观众定下的批准后写成一行档案（原话是她那句回应，挂着那条弹幕），放进最新一份快照；她说欠着的
 * 批准后登记成一条未了结承诺。批准前先确认：这条记忆挂在那位观众的弹幕上，ta 被删除或发“忘了我”
 * 时会一起删掉。她说欠着的一类要看她是不是顺着观众的话编的，批了就成了一笔要兑现的账。
 *
 * 不含定时器 / 订阅 / WebSocket，无 keep-alive 生命周期语义。
 */
import { onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { CandidateItem, CandidateStatus } from '@/types';
import { ledgerApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn } from '@/utils/format';
import {
  ARCHIVE_TITLE_MAX_LENGTH,
  candidateKindLabel,
  candidateStatusLabel,
  nearestCommitmentsText,
  validateArchiveTitle,
} from './ledgerView';

const emit = defineEmits<{ changed: [] }>();

const status = ref<CandidateStatus>('pending');
const items = ref<CandidateItem[]>([]);
const pendingCount = ref(0);
const loading = ref(false);
const loadError = ref('');
const actionError = ref('');
const busyId = ref<number | null>(null);
/** 每条待审候选的标题输入框与稳定标记（批准时一起交上去） */
const titles = reactive<Record<number, string>>({});
const lasting = reactive<Record<number, boolean>>({});

async function reload(): Promise<void> {
  loading.value = true;
  loadError.value = '';
  try {
    const { data } = await ledgerApi.listCandidates(status.value);
    items.value = data.items;
    pendingCount.value = data.pending_count;
    for (const item of data.items) {
      titles[item.id] = item.title;
      lasting[item.id] = false;
    }
  } catch (err) {
    loadError.value = getApiErrorMessage(err, '观众候选加载失败');
  } finally {
    loading.value = false;
  }
}

async function approve(item: CandidateItem): Promise<void> {
  const title = (titles[item.id] ?? item.title).trim();
  const titleError = validateArchiveTitle(title);
  if (titleError) {
    actionError.value = titleError;
    return;
  }
  const warning =
    item.kind === 'claimed_missing'
      ? `批准后登记成一条未了结承诺「${title}」，之后每场都会提醒她兑现。先确认她不是顺着观众的话编的。`
      : `批准后「${title}」进记忆，下一场开播会记得。这条记忆挂在那位观众的弹幕上：删除这位观众或 ta 发“忘了我”时会一起删掉。`;
  if (!(await confirmAction(warning, '批准观众候选', { type: 'info' }))) return;
  await mutate(
    item,
    () => ledgerApi.approveCandidate(item.id, { title, lasting: lasting[item.id] ?? false }),
    '已批准',
    '批准失败',
  );
}

async function reject(item: CandidateItem): Promise<void> {
  await mutate(
    item,
    () => ledgerApi.rejectCandidate(item.id),
    '已驳回，以后不再列这一条',
    '驳回失败',
  );
}

async function mutate(
  item: CandidateItem,
  action: () => Promise<unknown>,
  success: string,
  fallback: string,
): Promise<void> {
  busyId.value = item.id;
  actionError.value = '';
  try {
    await action();
    ElMessage.success(success);
    emit('changed');
    await reload();
  } catch (err) {
    actionError.value = getApiErrorMessage(err, fallback);
  } finally {
    busyId.value = null;
  }
}

onMounted(() => {
  void reload();
});

defineExpose({ reload });
</script>

<style scoped>
.candidate-panel {
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
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
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

.candidate-list {
  list-style: none;
  margin: 0;
  padding: 0;
  min-height: 40px;
}

.candidate-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 0;
  border-top: 1px dashed var(--border-color-light);
}

.candidate-item:first-child {
  border-top: none;
}

.candidate-head,
.candidate-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.candidate-actions .grow {
  flex: 1;
}

.title-input {
  max-width: 360px;
}

.candidate-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.candidate-game,
.candidate-meta {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.candidate-quote {
  margin: 0;
  padding-left: var(--spacing-sm);
  border-left: 2px solid var(--border-color-light);
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
}

.candidate-quote.viewer {
  color: var(--text-secondary);
}

/* 她说欠着的：账上最接近的几条，给审的人判断是漏登记还是她编的 */
.candidate-nearest {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.empty-hint {
  padding: var(--spacing-sm);
  text-align: center;
  font-size: 12px;
  color: var(--text-placeholder);
}
</style>
