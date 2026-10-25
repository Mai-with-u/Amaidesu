<template>
  <div class="pending-page">
    <header class="page-header">
      <div class="header-left">
        <router-link class="back-link" to="/viewers">← 观众列表</router-link>
        <h1 class="page-title">待确认观众</h1>
        <p class="page-subtitle">
          新出现的观众与老观众同名，但老观众的这个昵称已经很久没出现过（可能已被别人拿走），
          或者同名的老观众不止一位——请判断是不是同一个人
          <template v-if="total > 0">（共 {{ total }} 位）</template>
        </p>
      </div>
      <el-button type="primary" plain :loading="loading" @click="load">刷新</el-button>
    </header>

    <div v-loading="loading" class="pending-list">
      <p v-if="!loading && items.length === 0" class="empty-hint">没有待确认的观众</p>

      <article v-for="item in items" :key="item.viewer_id" class="pending-card">
        <!-- 左：新出现的这一位；右：建议并入的老观众，两边并排比对 -->
        <div class="compare">
          <div class="side">
            <span class="side-label">新出现</span>
            <router-link :to="`/viewers/${item.viewer_id}`" class="side-name">
              {{ item.user_name || '（未留名）' }}
            </router-link>
            <code class="mono side-id"
              >#{{ item.viewer_id }} · {{ externalId(item.account_id, item.open_id) }}</code
            >
            <span class="side-stat">
              发言 {{ item.message_count }} · 互动 {{ item.interaction_count }} · 付费 ¥{{
                (item.paid_amount / 1000).toFixed(2)
              }}
            </span>
            <span class="side-stat">最后活跃 {{ relativeAge(item.last_active_ms) }}</span>
          </div>

          <div class="side">
            <span class="side-label">建议并入</span>
            <template v-if="item.candidate">
              <router-link :to="`/viewers/${item.candidate.viewer_id}`" class="side-name">
                {{ item.candidate.user_name || '（未留名）' }}
              </router-link>
              <code class="mono side-id">
                #{{ item.candidate.viewer_id }} ·
                {{ externalId(item.candidate_account_id, item.candidate_open_id) }}
              </code>
              <span class="side-stat">
                发言 {{ item.candidate_message_count }} · 互动
                {{ item.candidate_interaction_count }} · 付费 ¥{{
                  (item.candidate_paid_amount / 1000).toFixed(2)
                }}
              </span>
              <span class="side-stat">
                这个昵称上次出现 {{ relativeAge(item.candidate_name_seen_ms) }} · 最后活跃
                {{ relativeAge(item.candidate_last_active_ms) }}
              </span>
              <span v-if="item.candidate_names.length > 1" class="side-stat">
                用过的昵称：{{ item.candidate_names.join('、') }}
              </span>
            </template>
            <span v-else class="side-stat">建议并入的观众已不存在，可改选或保持新建</span>
          </div>
        </div>

        <!-- 处理：同意 = 并入建议的老观众；改选 = 并入另选的一位；保持新建 = 不是同一个人 -->
        <div class="actions">
          <el-button
            v-if="item.candidate"
            type="primary"
            size="small"
            :loading="busyId === item.viewer_id"
            @click="merge(item, item.candidate.viewer_id, item.candidate.user_name)"
          >
            是同一个人，并入
          </el-button>
          <ViewerPicker v-model="choices[item.viewer_id]" :exclude-id="item.viewer_id" />
          <el-button
            size="small"
            :disabled="choices[item.viewer_id] == null"
            :loading="busyId === item.viewer_id"
            @click="mergeChosen(item)"
          >
            改为并入所选观众
          </el-button>
          <el-button size="small" :loading="busyId === item.viewer_id" @click="keep(item)">
            不是，保持新建
          </el-button>
        </div>
      </article>
    </div>

    <el-pagination
      v-if="total > pageSize"
      class="pager"
      layout="total, prev, pager, next"
      :total="total"
      :page-size="pageSize"
      :current-page="page"
      @current-change="onPageChange"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * 待确认观众 —— 与老观众同名、自动关联不敢下结论的观众，由人判断是不是同一个人
 *
 * 数据面：GET /viewers/pending（每行带建议并入的老观众摘要与其用过的昵称）。
 * 处理：POST /viewers/{viewerId}/merge（同意 / 改选）与 /keep（保持新建）；
 * 合并没有一键撤销，执行前都弹确认框；处理完重新拉取列表。
 */
import { onMounted, reactive, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { viewersApi } from '@/api';
import ViewerPicker from '@/components/viewers/ViewerPicker.vue';
import type { PendingViewerItem } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { relativeAge } from '@/utils/format';

const items = ref<PendingViewerItem[]>([]);
const total = ref(0);
const loading = ref(false);
const page = ref(1);
const pageSize = 20;
/** 每位待确认观众"改选"时选中的目标观众 */
const choices = reactive<Record<number, number | null>>({});
/** 正在处理的待确认观众（同一时刻只处理一位，避免重复提交） */
const busyId = ref<number | null>(null);

function externalId(accountId: string | null, openId: string | null): string {
  return (
    [accountId && `账号 ${accountId}`, openId && `开放平台 ${openId}`]
      .filter(Boolean)
      .join(' / ') || '无外部 ID'
  );
}

async function load(): Promise<void> {
  loading.value = true;
  try {
    const response = await viewersApi.pending({
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    });
    items.value = response.data.items;
    total.value = response.data.total;
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '待确认观众加载失败'));
  } finally {
    loading.value = false;
  }
}

function onPageChange(next: number): void {
  page.value = next;
  void load();
}

/** 弹确认框 → 调接口 → 提示并刷新列表；用户取消时什么都不做 */
async function act(
  item: PendingViewerItem,
  confirmText: string,
  title: string,
  run: () => Promise<unknown>,
  done: string,
): Promise<void> {
  try {
    await ElMessageBox.confirm(confirmText, title, {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    });
  } catch {
    return;
  }
  busyId.value = item.viewer_id;
  try {
    await run();
    ElMessage.success(done);
    delete choices[item.viewer_id];
    await load();
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, `${title}失败`));
  } finally {
    busyId.value = null;
  }
}

function merge(item: PendingViewerItem, targetId: number, targetName: string): void {
  void act(
    item,
    `把新出现的「${item.user_name}」（#${item.viewer_id}）并入「${targetName}」（#${targetId}）？` +
      '外部 ID、计数、昵称记录与对话记录都会移过去；合并没有一键撤销，错了可在档案页取消关联。',
    '合并观众',
    () => viewersApi.merge(item.viewer_id, targetId),
    '已合并',
  );
}

function mergeChosen(item: PendingViewerItem): void {
  const targetId = choices[item.viewer_id];
  if (targetId == null) return;
  merge(item, targetId, `观众 #${targetId}`);
}

function keep(item: PendingViewerItem): void {
  void act(
    item,
    `确认「${item.user_name}」（#${item.viewer_id}）与建议的老观众不是同一个人，保持为独立观众？`,
    '保持新建',
    () => viewersApi.keepSeparate(item.viewer_id),
    '已保持为独立观众',
  );
}

onMounted(() => {
  void load();
});
</script>

<style scoped>
.pending-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--spacing-md);
}

.back-link {
  font-size: 12px;
  color: var(--color-primary);
  text-decoration: none;
}

.page-title {
  margin: 4px 0 0;
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
}

.page-subtitle {
  margin: 4px 0 0;
  max-width: 760px;
  font-size: 12px;
  color: var(--text-secondary);
}

.pending-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  min-height: 120px;
}

.pending-card {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.compare {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-md);
}

.side {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--spacing-sm);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-hover);
}

.side-label {
  font-size: 11px;
  color: var(--text-placeholder);
}

.side-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  text-decoration: none;
}
.side-name:hover {
  text-decoration: underline;
}

.side-id {
  font-size: 11px;
  color: var(--text-placeholder);
  word-break: break-all;
}

.side-stat {
  font-size: 12px;
  color: var(--text-secondary);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-sm);
}

.empty-hint {
  padding: var(--spacing-lg);
  text-align: center;
  font-size: 13px;
  color: var(--text-placeholder);
}

@media (max-width: 860px) {
  .compare {
    grid-template-columns: 1fr;
  }
}
</style>
