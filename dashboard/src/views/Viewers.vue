<template>
  <div class="viewers-page">
    <!-- 页面头：身份 + 总数 + 互动分析入口                              -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">观众</h1>
        <p class="page-subtitle">
          谁在看直播 · 谁最活跃 · 谁贡献最多
          <template v-if="total > 0">（共 {{ total }} 位）</template>
          <span class="scope-note">计数为历史累计，含已清理的场次</span>
        </p>
      </div>
      <div class="header-actions">
        <el-button type="primary" plain :loading="loading" @click="load">刷新</el-button>
        <el-button plain @click="openForgetDialog">
          不记录名单<template v-if="forgetCount > 0">（{{ forgetCount }}）</template>
        </el-button>
        <!-- 有观众与老观众同名、等人工确认是不是同一个人时，入口带上数量提醒 -->
        <router-link to="/viewers/pending" class="insights-link">
          待确认观众<template v-if="pendingTotal > 0">（{{ pendingTotal }}）</template> →
        </router-link>
        <router-link to="/viewers/insights" class="insights-link">互动分析 →</router-link>
      </div>
    </header>

    <!-- 工具条：搜索（防抖）+ 排序                                      -->
    <div class="toolbar">
      <el-input
        v-model="searchText"
        class="search-input"
        placeholder="搜索昵称或外部 ID"
        clearable
        :prefix-icon="Search"
      />
      <el-select v-model="orderBy" class="order-select">
        <el-option
          v-for="opt in ORDER_OPTIONS"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <span class="grow" />
      <span class="count-hint mono">{{ shownRange }} / {{ total }}</span>
    </div>

    <!-- 观众表                                                        -->
    <section class="table-card">
      <el-table
        v-loading="loading"
        :data="items"
        stripe
        style="width: 100%"
        :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
        @row-click="goDetail"
      >
        <el-table-column label="昵称" min-width="160">
          <template #default="{ row }">
            <span class="name-cell" :title="row.user_name || '（未留名）'">
              {{ row.user_name || '（未留名）' }}
            </span>
            <el-tag
              v-if="row.link_status === 'pending'"
              size="small"
              type="warning"
              class="pending-tag"
            >
              待确认
            </el-tag>
            <el-tag
              v-if="row.forget_requested_at_ms"
              size="small"
              type="danger"
              class="pending-tag"
              :title="`已请求遗忘，将于下播后或 ${formatTime(row.forget_requested_at_ms + 86_400_000)} 前删除`"
            >
              待删除
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="外部 ID" min-width="150">
          <template #default="{ row }">
            <code class="mono id-cell" :title="row.user_id">{{ row.user_id || '—' }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="message_count" label="发言" width="90" sortable />
        <el-table-column prop="gift_count" label="礼物" width="90" />
        <el-table-column prop="replied_count" label="被回复" width="90" />
        <el-table-column prop="interaction_count" label="互动" width="90" />
        <el-table-column label="付费金额" width="110" align="right">
          <template #default="{ row }">
            <span class="mono" :title="`${row.paid_count} 次付费（礼物+SC+上舰）`">
              ¥{{ (row.paid_amount / 1000).toFixed(2) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="最后活跃" width="130">
          <template #default="{ row }">
            <span :title="formatTime(row.last_active_ms)">{{
              relativeAge(row.last_active_ms)
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="" width="80" align="right">
          <template #default>
            <el-button link size="small" type="primary">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && items.length === 0" class="empty-hint">
        {{ searchText ? '没有匹配的观众' : '还没有观众数据——开启场次后弹幕与礼物会积累在这里' }}
      </div>
    </section>

    <el-pagination
      v-if="total > pageSize"
      class="pager"
      layout="total, prev, pager, next"
      :total="total"
      :page-size="pageSize"
      :current-page="page"
      @current-change="onPageChange"
    />

    <!-- 不记录名单管理弹窗：仅展示总条数（不保存明文 ID），支持按外部 ID 解除 -->
    <el-dialog v-model="forgetDialogVisible" title="不记录名单" width="460px">
      <div class="forget-dialog-body">
        <p class="forget-desc">
          当前不记录名单共有 <strong>{{ forgetCount }}</strong> 条外部 ID 摘要（数据库不保存明文外部
          ID）。若观众请求恢复记录，请输入其平台外部 ID 解除限制；已删除的历史数据无法恢复。
        </p>
        <div class="forget-form">
          <el-select v-model="unblockIdKind" style="width: 130px">
            <el-option label="账号 ID" value="account" />
            <el-option label="开放平台 ID" value="open" />
          </el-select>
          <el-input
            v-model="unblockExternalId"
            placeholder="输入外部 ID（如 B 站 uid 或 open_id）"
            clearable
          />
          <el-button
            type="primary"
            :loading="unblockBusy"
            :disabled="!unblockExternalId.trim()"
            @click="submitUnblock"
          >
            解除
          </el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 观众列表 —— 跨场次观众统计的检索入口
 *
 * 数据面：GET /viewers（viewers 表，StorageLedger 写穿的跨场计数）。
 * 搜索 250ms 防抖走服务端 LIKE（外部 ID / 昵称），排序取后端白名单列；
 * 点行按内部观众 ID 进入 /viewers/:viewerId 档案页（外部 ID 列只作展示：
 * 同一位观众可能有账号 ID 与开放平台 ID 两种）。头部入口显示待确认观众数。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { Search } from '@element-plus/icons-vue';
import { viewersApi } from '@/api';
import type { ViewerListItem } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatDateTimeCn as formatTime, relativeAge } from '@/utils/format';

const router = useRouter();

const ORDER_OPTIONS = [
  { label: '按发言数', value: 'message_count' },
  { label: '按礼物数', value: 'gift_count' },
  { label: '按被回复', value: 'replied_count' },
  { label: '按互动数', value: 'interaction_count' },
  { label: '按付费', value: 'paid_amount' },
  { label: '按最近活跃', value: 'last_active_ms' },
] as const;

const searchText = ref('');
const orderBy = ref<string>('message_count');
const page = ref(1);
const pageSize = 20;

const items = ref<ViewerListItem[]>([]);
const total = ref(0);
const loading = ref(false);
const pendingTotal = ref(0);
const forgetCount = ref(0);
const forgetDialogVisible = ref(false);
const unblockIdKind = ref<'account' | 'open'>('account');
const unblockExternalId = ref('');
const unblockBusy = ref(false);

const shownRange = computed(() => {
  if (total.value === 0) return '0';
  const start = (page.value - 1) * pageSize + 1;
  const end = Math.min(page.value * pageSize, total.value);
  return `${start}-${end}`;
});

async function load(): Promise<void> {
  loading.value = true;
  try {
    const response = await viewersApi.list({
      search: searchText.value.trim() || undefined,
      order_by: orderBy.value,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    });
    items.value = response.data.items;
    total.value = response.data.total;
  } finally {
    loading.value = false;
  }
  // 待确认数与不记录名单条数只用于入口提醒，取不到不影响列表
  try {
    pendingTotal.value = (await viewersApi.pending({ limit: 1 })).data.total;
  } catch {
    pendingTotal.value = 0;
  }
  try {
    forgetCount.value = (await viewersApi.getForgetListCount()).data.count;
  } catch {
    forgetCount.value = 0;
  }
}

function openForgetDialog(): void {
  forgetDialogVisible.value = true;
}

async function submitUnblock(): Promise<void> {
  const extId = unblockExternalId.value.trim();
  if (!extId) return;
  unblockBusy.value = true;
  try {
    const res = await viewersApi.removeFromForgetList(unblockIdKind.value, extId);
    forgetCount.value = res.data.count;
    if (res.data.removed) {
      ElMessage.success('已从不记录名单解除，后续发言将恢复正常记录');
      unblockExternalId.value = '';
      void load();
    } else {
      ElMessage.info('该外部 ID 不在不记录名单中');
    }
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '解除不记录名单失败'));
  } finally {
    unblockBusy.value = false;
  }
}

function onPageChange(next: number): void {
  page.value = next;
  void load();
}

function goDetail(row: ViewerListItem): void {
  void router.push(`/viewers/${row.viewer_id}`);
}

// 搜索防抖：停顿 250ms 再请求；排序变化即时刷新并回到第一页
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(searchText, () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    page.value = 1;
    void load();
  }, 250);
});
watch(orderBy, () => {
  page.value = 1;
  void load();
});

onMounted(() => {
  void load();
});
</script>

<style scoped>
.viewers-page {
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

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
}

.page-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.scope-note {
  margin-left: var(--spacing-sm);
  font-size: 11px;
  color: var(--text-placeholder);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.insights-link {
  font-size: 13px;
  color: var(--color-primary);
  text-decoration: none;
}
.insights-link:hover {
  text-decoration: underline;
}

.search-input {
  width: 260px;
}

.order-select {
  width: 140px;
}

.table-card :deep(.el-table__row) {
  cursor: pointer;
}

.name-cell {
  font-weight: 600;
  color: var(--text-primary);
}

.pending-tag {
  margin-left: var(--spacing-xs);
}

.id-cell {
  font-size: 11px;
  color: var(--text-placeholder);
}

.forget-dialog-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.forget-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.forget-form {
  display: flex;
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
  .search-input {
    width: 160px;
  }
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
