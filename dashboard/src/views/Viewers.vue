<template>
  <div class="viewers-page">
    <!-- 页面头：身份 + 总数 + 管理入口                                        -->
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
        <el-button plain @click="router.push('/viewers/pending')">
          待确认观众<template v-if="pendingTotal > 0">（{{ pendingTotal }}）</template>
        </el-button>
        <el-button plain @click="router.push('/viewers/insights')">互动分析</el-button>
      </div>
    </header>

    <!-- 工具条：搜索（防抖）+ 活跃时段过滤；排序由列头点击承担               -->
    <div class="toolbar">
      <el-input
        v-model="searchText"
        class="search-input"
        placeholder="搜索昵称或外部 ID"
        clearable
        :prefix-icon="Search"
      />
      <el-radio-group v-model="activeWithin">
        <el-radio-button label="all">全部</el-radio-button>
        <el-radio-button label="today">今日活跃</el-radio-button>
        <el-radio-button label="week">近7天活跃</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 观众表                                                                -->
    <section class="table-card">
      <el-table
        v-loading="loading"
        :data="items"
        stripe
        style="width: 100%"
        :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
        :default-sort="{ prop: 'message_count', order: 'descending' }"
        @row-click="goDetail"
        @sort-change="onSortChange"
      >
        <el-table-column label="昵称" min-width="180">
          <template #default="{ row }">
            <div class="identity">
              <!-- 首字圆徽：色相按观众 ID 从既有色板轮转；角标 = 活跃档位 -->
              <span
                class="disc"
                :class="discClass(row.viewer_id)"
                :title="activityLabel(row.last_active_ms)"
              >
                {{ discChar(row.user_name) }}
                <span class="dot" :class="`dot-${activityTier(row.last_active_ms)}`" />
              </span>
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
            </div>
          </template>
        </el-table-column>
        <el-table-column label="外部 ID" width="140">
          <template #default="{ row }">
            <code class="mono id-cell" :title="row.user_id">{{ extIdDisplay(row.user_id) }}</code>
          </template>
        </el-table-column>
        <el-table-column label="场次" width="64">
          <template #default="{ row }">
            <span
              :class="{ 'num-dim': !row.sessions_count }"
              :title="`来过 ${row.sessions_count ?? 0} 场正式直播`"
            >
              {{ row.sessions_count ?? 0 }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="message_count" label="发言" width="72" sortable="custom">
          <template #default="{ row }">
            <span :class="{ 'num-dim': !row.message_count }">{{ row.message_count }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="gift_count" label="礼物" width="72" sortable="custom">
          <template #default="{ row }">
            <span :class="{ 'num-dim': !row.gift_count }">{{ row.gift_count }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="replied_count" label="被回复" width="80" sortable="custom">
          <template #default="{ row }">
            <span :class="{ 'num-dim': !row.replied_count }">{{ row.replied_count }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="interaction_count" label="互动" width="72" sortable="custom">
          <template #default="{ row }">
            <span :class="{ 'num-dim': !row.interaction_count }">{{ row.interaction_count }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="paid_amount"
          label="付费金额"
          width="104"
          sortable="custom"
          align="right"
        >
          <template #default="{ row }">
            <span
              class="mono"
              :class="row.paid_amount > 0 ? 'paid-strong' : 'num-dim'"
              :title="`${row.paid_count} 次付费（礼物+SC+上舰）`"
            >
              ¥{{ (row.paid_amount / 1000).toFixed(2) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="last_active_ms" label="最后活跃" width="130" sortable="custom">
          <template #default="{ row }">
            <span :title="formatTime(row.last_active_ms)">{{
              relativeAge(row.last_active_ms)
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="" width="64" align="right">
          <template #default>
            <el-button link size="small" type="primary">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && items.length === 0" class="empty-hint">
        {{ emptyText }}
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
 * 搜索 250ms 防抖走服务端 LIKE（外部 ID / 昵称）；活跃时段过滤（今日零点后 /
 * 近 7 天）与排序（列 + 方向）都由服务端完成，排序取后端白名单列；点行按内部
 * 观众 ID 进入 /viewers/:viewerId 档案页（外部 ID 列只作展示：同一位观众可能
 * 有账号 ID 与开放平台 ID 两种）。头部入口显示待确认观众数。
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

const searchText = ref('');
const activeWithin = ref<'all' | 'today' | 'week'>('all');
const sortState = ref<{ by: string; dir: 'ASC' | 'DESC' }>({ by: 'message_count', dir: 'DESC' });
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

const emptyText = computed(() =>
  activeWithin.value === 'all' && !searchText.value
    ? '还没有观众数据——开启场次后弹幕与礼物会积累在这里'
    : '没有匹配的观众',
);

async function load(): Promise<void> {
  loading.value = true;
  try {
    const response = await viewersApi.list({
      search: searchText.value.trim() || undefined,
      order_by: sortState.value.by,
      order_dir: sortState.value.dir,
      active_within: activeWithin.value === 'all' ? undefined : activeWithin.value,
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

// 列头排序：升 / 降由服务端执行；第三次点击（order 为空）回到默认按发言降序
function onSortChange({
  prop,
  order,
}: {
  prop: string;
  order: 'ascending' | 'descending' | null;
}): void {
  if (!prop || !order) {
    sortState.value = { by: 'message_count', dir: 'DESC' };
  } else {
    sortState.value = { by: prop, dir: order === 'ascending' ? 'ASC' : 'DESC' };
  }
  page.value = 1;
  void load();
}

// 搜索防抖：停顿 250ms 再请求；过滤与排序变化即时刷新并回到第一页
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(searchText, () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    page.value = 1;
    void load();
  }, 250);
});
watch(activeWithin, () => {
  page.value = 1;
  void load();
});

// -------------------- 展示辅助 --------------------

// 长 hex 外部 ID 对人不可读：超长截成“前 8 … 后 4”，全文悬浮可见
function extIdDisplay(id: string): string {
  if (!id) return '—';
  if (id.length <= 18) return id;
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

// 圆徽取昵称首字（按码点切，兼容 emoji 昵称）；未留名用问号
function discChar(name: string): string {
  const first = Array.from((name || '').trim())[0];
  return first ?? '？';
}

// 圆徽底色按观众 ID 从既有色板轮转（5 组 -bg token），同一位观众颜色稳定
function discClass(viewerId: number): string {
  return `disc-h${viewerId % 5}`;
}

// 活跃档位：今日零点后 / 近 7 天 / 更久；口径与后端活跃分桶一致
function activityTier(ms: number): 'today' | 'week' | 'older' {
  if (!ms) return 'older';
  const now = new Date();
  const todayStartMs = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (ms >= todayStartMs) return 'today';
  if (ms >= Date.now() - 7 * 86_400_000) return 'week';
  return 'older';
}

function activityLabel(ms: number): string {
  const tier = activityTier(ms);
  if (tier === 'today') return '今日活跃';
  return tier === 'week' ? '近 7 天活跃过' : '超过 7 天未活跃';
}

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
}

.search-input {
  width: 260px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.table-card :deep(.el-table__row) {
  cursor: pointer;
}

/* 观众身份：圆徽 + 昵称 + 待处理标签 */
.identity {
  display: flex;
  align-items: center;
  gap: 10px;
}

.disc {
  position: relative;
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: 50%;
  font-size: 14px;
  font-weight: 600;
}

/* 五组圆徽配色：底/字取自既有语义色板（-bg 与本色成对），暗色主题同样成立 */
.disc-h0 {
  background: var(--color-primary-bg);
  color: var(--color-primary);
}
.disc-h1 {
  background: var(--color-success-bg);
  color: var(--color-success);
}
.disc-h2 {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}
.disc-h3 {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}
.disc-h4 {
  background: var(--color-critical-bg);
  color: var(--color-critical);
}

/* 活跃档位角标：今日绿 / 近7天橙 / 更久灰 */
.dot {
  position: absolute;
  right: -1px;
  bottom: -1px;
  width: 10px;
  height: 10px;
  box-sizing: border-box;
  border: 2px solid var(--bg-card);
  border-radius: 50%;
}

.dot-today {
  background: var(--color-success);
}

.dot-week {
  background: var(--color-warning);
}

.dot-older {
  background: var(--color-info);
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

/* 零值降权：0 与空态用占位灰，让非零数字自然成为视觉落点 */
.num-dim {
  color: var(--text-placeholder);
}

/* 有付费的金额加粗并落语义警示色，付费行为本身是稀缺信号 */
.paid-strong {
  color: var(--color-warning);
  font-weight: 650;
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
