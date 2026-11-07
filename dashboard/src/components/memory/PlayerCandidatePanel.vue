<template>
  <section class="panel player-candidate-panel" aria-label="玩家自述">
    <header class="panel-head">
      <div class="head-text">
        <div class="panel-title">
          玩家自述
          <el-tag v-if="pendingCount > 0" size="small" type="warning"
            >{{ pendingCount }} 条待审</el-tag
          >
        </div>
        <p class="panel-hint">
          游戏玩家在公屏说的关于他自己的事（怎么称呼他、家或在盖的东西在哪、起了名的动物），她当场接住了。
          批准前她看不到；批准后只在这位玩家在游戏里找她时写在他的玩家卡上，不进开播简报。待审的列出后
          14 天没人审会自动删掉。
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
          <el-tag size="small" effect="plain">{{ item.player_name || '已删除的玩家' }}</el-tag>
          <el-input
            v-if="item.status === 'pending'"
            v-model="titles[item.id]"
            size="small"
            class="title-input"
            :maxlength="ARCHIVE_TITLE_MAX_LENGTH"
            show-word-limit
          />
          <span v-else class="candidate-title">{{ item.title }}</span>
        </div>
        <blockquote class="candidate-quote player">他：“{{ item.player_quote }}”</blockquote>
        <blockquote class="candidate-quote">
          她（{{ playerReplyWhere(item) }}）：“{{ item.reply_quote }}”
        </blockquote>
        <div v-if="item.status === 'pending'" class="candidate-actions">
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
        <div v-else class="candidate-actions">
          <span class="candidate-meta mono">
            {{ candidateStatusLabel(item.status) }}
            <template v-if="item.decided_at_ms !== null">
              · {{ formatDateTimeCn(item.decided_at_ms) }}</template
            >
          </span>
          <span class="grow" />
          <el-button
            v-if="item.status === 'approved'"
            size="small"
            :loading="busyId === item.id"
            :disabled="busyId !== null && busyId !== item.id"
            @click="revoke(item)"
            >撤销批准</el-button
          >
          <el-button
            size="small"
            type="danger"
            plain
            :loading="busyId === item.id"
            :disabled="busyId !== null && busyId !== item.id"
            @click="remove(item)"
            >删除这一条</el-button
          >
        </div>
      </li>
      <li v-if="!loading && !loadError && items.length === 0" class="empty-hint">
        {{ status === 'pending' ? '没有待审的玩家自述' : '没有记录' }}
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
/**
 * 玩家自述审核区 —— 游戏玩家说的关于他自己的事，人批准才进他的玩家卡
 *
 * 下播整理从她接住的那几句公屏里列出候选（他那句原话 + 她回他的那句），代码核过对象与原话。
 * 批准（标题可以先改）后只在这位玩家在游戏里找她时写在玩家卡上，不进开播简报；驳回的以后不再列。
 * 自述挂在他那句公屏上：删除这位玩家或他发“忘了我”时一起删掉。批错了可以撤销批准（回到待审），
 * 哪种状态的都可以整条删掉。
 *
 * 不含定时器 / 订阅 / WebSocket，无 keep-alive 生命周期语义。
 */
import { onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { CandidateStatus, PlayerCandidateItem } from '@/types';
import { playersApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn } from '@/utils/format';
import { ARCHIVE_TITLE_MAX_LENGTH, candidateStatusLabel, validateArchiveTitle } from './ledgerView';
import { playerReplyWhere } from './playersView';

const status = ref<CandidateStatus>('pending');
const items = ref<PlayerCandidateItem[]>([]);
const pendingCount = ref(0);
const loading = ref(false);
const loadError = ref('');
const actionError = ref('');
const busyId = ref<number | null>(null);
/** 每条待审自述的标题输入框（批准时交上去） */
const titles = reactive<Record<number, string>>({});

async function reload(): Promise<void> {
  loading.value = true;
  loadError.value = '';
  try {
    const { data } = await playersApi.listCandidates(status.value);
    items.value = data.items;
    pendingCount.value = data.pending_count;
    for (const item of data.items) titles[item.id] = item.title;
  } catch (err) {
    loadError.value = getApiErrorMessage(err, '玩家自述加载失败');
  } finally {
    loading.value = false;
  }
}

async function approve(item: PlayerCandidateItem): Promise<void> {
  const title = (titles[item.id] ?? item.title).trim();
  const titleError = validateArchiveTitle(title);
  if (titleError) {
    actionError.value = titleError;
    return;
  }
  const warning = `批准后「${title}」写在 ${item.player_name || '这位玩家'} 的玩家卡上：他在游戏里找她时她会想起来。删除这位玩家或他发“忘了我”时会一起删掉。`;
  if (!(await confirmAction(warning, '批准玩家自述', { type: 'info' }))) return;
  await mutate(item, () => playersApi.approveCandidate(item.id, { title }), '已批准', '批准失败');
}

async function reject(item: PlayerCandidateItem): Promise<void> {
  await mutate(
    item,
    () => playersApi.rejectCandidate(item.id),
    '已驳回，以后不再列这一条',
    '驳回失败',
  );
}

async function revoke(item: PlayerCandidateItem): Promise<void> {
  const warning = `撤销后「${item.title}」从 ${item.player_name || '这位玩家'} 的玩家卡上撤下，回到待审，可以改了标题再批或驳回。`;
  if (!(await confirmAction(warning, '撤销批准', { type: 'warning' }))) return;
  await mutate(item, () => playersApi.revokeCandidate(item.id), '已撤销，回到待审', '撤销失败');
}

async function remove(item: PlayerCandidateItem): Promise<void> {
  const warning =
    item.status === 'rejected'
      ? `删掉「${item.title}」：删了以后同一句话下次整理可能再被列出。`
      : `删掉「${item.title}」：批准过的会从玩家卡上撤下，删了不能恢复。`;
  if (!(await confirmAction(warning, '删除这一条', { type: 'warning' }))) return;
  await mutate(item, () => playersApi.deleteCandidate(item.id), '已删掉', '删除失败');
}

async function mutate(
  item: PlayerCandidateItem,
  action: () => Promise<unknown>,
  success: string,
  fallback: string,
): Promise<void> {
  busyId.value = item.id;
  actionError.value = '';
  try {
    await action();
    ElMessage.success(success);
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
.player-candidate-panel {
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

.candidate-quote.player {
  color: var(--text-secondary);
}

.empty-hint {
  padding: var(--spacing-sm);
  text-align: center;
  font-size: 12px;
  color: var(--text-placeholder);
}
</style>
