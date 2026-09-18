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
        <router-link to="/viewers/insights" class="insights-link">互动分析 →</router-link>
      </div>
    </header>

    <!-- 工具条：搜索（防抖）+ 排序                                      -->
    <div class="toolbar">
      <el-input
        v-model="searchText"
        class="search-input"
        placeholder="搜索昵称或 user_id"
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
          </template>
        </el-table-column>
        <el-table-column label="user_id" min-width="150">
          <template #default="{ row }">
            <code class="mono id-cell" :title="row.user_id">{{ row.user_id }}</code>
          </template>
        </el-table-column>
        <el-table-column prop="message_count" label="发言" width="90" sortable />
        <el-table-column prop="gift_count" label="礼物" width="90" />
        <el-table-column prop="replied_count" label="被回复" width="90" />
        <el-table-column prop="interaction_count" label="互动" width="90" />
        <el-table-column label="最后活跃" width="130">
          <template #default="{ row }">
            <span :title="formatTime(row.last_active_ms)">{{
              relativeTime(row.last_active_ms)
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
  </div>
</template>

<script setup lang="ts">
/**
 * 观众列表 —— 跨场次观众统计的检索入口
 *
 * 数据面：GET /viewers（viewers 表，StorageLedger 写穿的跨场计数）。
 * 搜索 250ms 防抖走服务端 LIKE（user_id / user_name），排序取后端白名单列；
 * 点行进入 /viewers/:userId 档案页。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { Search } from '@element-plus/icons-vue';
import { viewersApi } from '@/api';
import type { ViewerListItem } from '@/types';

const router = useRouter();

const ORDER_OPTIONS = [
  { label: '按发言数', value: 'message_count' },
  { label: '按礼物数', value: 'gift_count' },
  { label: '按被回复', value: 'replied_count' },
  { label: '按互动数', value: 'interaction_count' },
  { label: '按最近活跃', value: 'last_active_ms' },
] as const;

const searchText = ref('');
const orderBy = ref<string>('message_count');
const page = ref(1);
const pageSize = 20;

const items = ref<ViewerListItem[]>([]);
const total = ref(0);
const loading = ref(false);

const shownRange = computed(() => {
  if (total.value === 0) return '0';
  const start = (page.value - 1) * pageSize + 1;
  const end = Math.min(page.value * pageSize, total.value);
  return `${start}-${end}`;
});

function formatTime(ms: number): string {
  return new Date(ms).toLocaleString('zh-CN', { hour12: false });
}

function relativeTime(ms: number): string {
  if (!ms) return '—';
  const diffSec = Math.floor((Date.now() - ms) / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86_400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 30 * 86_400) return `${Math.floor(diffSec / 86_400)} 天前`;
  return formatTime(ms).split(' ')[0];
}

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
}

function onPageChange(next: number): void {
  page.value = next;
  void load();
}

function goDetail(row: ViewerListItem): void {
  void router.push(`/viewers/${encodeURIComponent(row.user_id)}`);
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

.grow {
  flex: 1;
}

.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
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

.toolbar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.search-input {
  width: 260px;
}

.order-select {
  width: 140px;
}

.count-hint {
  font-size: 11px;
  color: var(--text-placeholder);
}

.table-card {
  padding: var(--spacing-sm);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.table-card :deep(.el-table__row) {
  cursor: pointer;
}

.name-cell {
  font-weight: 600;
  color: var(--text-primary);
}

.id-cell {
  font-size: 11px;
  color: var(--text-placeholder);
}

.empty-hint {
  padding: var(--spacing-lg);
  text-align: center;
  font-size: 13px;
  color: var(--text-placeholder);
}

.pager {
  justify-content: flex-end;
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
