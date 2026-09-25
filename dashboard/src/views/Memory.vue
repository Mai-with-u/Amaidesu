<template>
  <div class="memory-page">
    <!-- 页面头：身份 + 总量 + 刷新                                               -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">观众画像</h1>
        <p class="page-subtitle">
          主播对观众的长期认识 · 查看 / 纠正 / 清理
          <template v-if="stats && stats.profile_count > 0">
            （画像 {{ stats.profile_count }} 份 · 事实 {{ stats.fact_count }} 条）</template
          >
        </p>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="reload">刷新</el-button>
      </div>
    </header>

    <!-- 工具条：搜索（防抖）                                                     -->
    <div class="toolbar">
      <el-input
        v-model="searchText"
        class="search-input"
        placeholder="搜索画像内容 / 用户 ID"
        clearable
        :prefix-icon="Search"
      />
      <span class="grow" />
      <span class="count-hint mono">{{ shownRange }} / {{ total }}</span>
    </div>

    <!-- 画像表                                                                  -->
    <section class="table-card">
      <el-table
        v-loading="loading"
        :data="items"
        stripe
        style="width: 100%"
        :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="expand-body">
              <div class="expand-header">
                <span class="expand-title">该观众的事实原料</span>
                <el-button
                  link
                  size="small"
                  :loading="factsLoadingKey === rowKey(row)"
                  @click.stop="loadFacts(row)"
                  >刷新事实</el-button
                >
              </div>
              <div v-if="(factsByViewer[rowKey(row)] || []).length > 0" class="fact-list">
                <div v-for="fact in factsByViewer[rowKey(row)]" :key="fact.id" class="fact-row">
                  <span class="fact-time">{{ relativeAge(fact.created_at_ms) }}</span>
                  <span class="fact-text">{{ fact.fact_text }}</span>
                  <el-button link size="small" type="danger" @click.stop="removeFact(row, fact)"
                    >删除</el-button
                  >
                </div>
              </div>
              <div v-else class="none-hint">暂无事实（展开后可点「刷新事实」加载）</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="观众" width="220">
          <template #default="{ row }">
            <span class="mono viewer-cell">{{ row.platform }}/{{ row.user_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="画像" min-width="320">
          <template #default="{ row }">
            <span class="text-cell">{{ row.profile_text }}</span>
          </template>
        </el-table-column>
        <el-table-column label="压缩水位" width="120">
          <template #default="{ row }">
            <span v-if="row.last_compressed_at_ms > 0" :title="formatTime(row.last_compressed_at_ms)">
              {{ relativeAge(row.last_compressed_at_ms) }}
            </span>
            <span v-else class="none-hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="更新" width="120">
          <template #default="{ row }">
            <span :title="formatTime(row.updated_at_ms)">{{ relativeAge(row.updated_at_ms) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="" width="130" align="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click.stop="openEdit(row)">纠正</el-button>
            <el-button link size="small" type="danger" @click.stop="removeProfile(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && items.length === 0" class="empty-hint">
        {{
          searchText
            ? '没有匹配的画像'
            : '还没有画像——观众互动达到门槛后，后台会从弹幕与付费记录中提炼画像'
        }}
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

    <!-- 画像纠正对话框                                                          -->
    <el-dialog v-model="editVisible" title="纠正画像" width="560px">
      <p class="edit-desc">
        直接编辑画像文本。纠正后主播下次决策即读到新内容；后台压缩会以人工纠正版为旧画像继续增量更新。
      </p>
      <el-input
        v-model="editForm.profileText"
        type="textarea"
        :rows="6"
        maxlength="1000"
        show-word-limit
        placeholder="画像正文"
      />
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveProfile">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 观众画像管理 —— viewer_profiles 的查看 / 纠正 / 清理入口
 *
 * 数据面：/api/v1/memory/*（画像列表 / 纠正 / 删除；事实查看 / 单条删除）。
 * 搜索 250ms 防抖走服务端 LIKE（画像文本 / user_id）；行展开懒加载该观众
 * 的事实原料，提取错误的事实可单条删除，画像不受影响。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { Search } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { memoryApi } from '@/api';
import type {
  ViewerProfileItem,
  ViewerFactItem,
  MemoryStatsResponse,
} from '@/types';
import { relativeAge } from '@/utils/format';
import { confirmAction } from '@/utils/confirmAction';

const pageSize = 20;

const searchText = ref('');
const page = ref(1);

const items = ref<ViewerProfileItem[]>([]);
const total = ref(0);
const loading = ref(false);
const stats = ref<MemoryStatsResponse | null>(null);

const shownRange = computed(() => {
  if (total.value === 0) return '0';
  const start = (page.value - 1) * pageSize + 1;
  const end = Math.min(page.value * pageSize, total.value);
  return `${start}-${end}`;
});

function formatTime(ms: number): string {
  return new Date(ms).toLocaleString('zh-CN', { hour12: false });
}

function rowKey(row: ViewerProfileItem): string {
  return `${row.platform}/${row.user_id}`;
}

async function loadProfiles(): Promise<void> {
  loading.value = true;
  try {
    const response = await memoryApi.listProfiles({
      search: searchText.value.trim() || undefined,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    });
    items.value = response.data.items;
    total.value = response.data.total;
  } catch {
    ElMessage.error('画像列表加载失败');
  } finally {
    loading.value = false;
  }
}

async function loadStats(): Promise<void> {
  try {
    stats.value = (await memoryApi.getStats()).data;
  } catch {
    stats.value = null;
  }
}

function reload(): void {
  void loadProfiles();
  void loadStats();
}

function onPageChange(next: number): void {
  page.value = next;
  void loadProfiles();
}

// 搜索防抖：停顿 250ms 再请求
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(searchText, () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    page.value = 1;
    void loadProfiles();
  }, 250);
});

// ===== 行展开：事实原料懒加载 =====

const factsByViewer = ref<Record<string, ViewerFactItem[]>>({});
const factsLoadingKey = ref('');

async function loadFacts(profile: ViewerProfileItem): Promise<void> {
  factsLoadingKey.value = rowKey(profile);
  try {
    const response = await memoryApi.listFacts({
      platform: profile.platform,
      user_id: profile.user_id,
      limit: 50,
    });
    factsByViewer.value[rowKey(profile)] = response.data.items;
  } catch {
    ElMessage.error('事实加载失败');
  } finally {
    factsLoadingKey.value = '';
  }
}

async function removeFact(profile: ViewerProfileItem, fact: ViewerFactItem): Promise<void> {
  const preview = fact.fact_text.length > 40 ? `${fact.fact_text.slice(0, 37)}...` : fact.fact_text;
  if (!(await confirmAction(`删除这条事实？「${preview}」`, '删除事实'))) return;
  try {
    await memoryApi.deleteFact(fact.id);
    ElMessage.success('已删除');
    await loadFacts(profile);
    void loadStats();
  } catch {
    ElMessage.error('删除失败');
  }
}

// ===== 画像纠正 =====

const editVisible = ref(false);
const saving = ref(false);
const editing = ref<ViewerProfileItem | null>(null);
const editForm = ref({ profileText: '' });

function openEdit(profile: ViewerProfileItem): void {
  editing.value = profile;
  editForm.value = { profileText: profile.profile_text };
  editVisible.value = true;
}

async function saveProfile(): Promise<void> {
  const target = editing.value;
  const text = editForm.value.profileText.trim();
  if (!target) return;
  if (!text) {
    ElMessage.warning('画像内容不能为空');
    return;
  }
  saving.value = true;
  try {
    await memoryApi.updateProfile(target.platform, target.user_id, { profile_text: text });
    ElMessage.success('已纠正画像');
    editVisible.value = false;
    reload();
  } catch {
    ElMessage.error('保存失败');
  } finally {
    saving.value = false;
  }
}

async function removeProfile(profile: ViewerProfileItem): Promise<void> {
  if (
    !(await confirmAction(
      `删除「${rowKey(profile)}」的画像？删除后该观众回到无画像态（随新事实重新生成）。`,
      '删除画像',
    ))
  )
    return;
  try {
    await memoryApi.deleteProfile(profile.platform, profile.user_id);
    ElMessage.success('已删除');
    reload();
  } catch {
    ElMessage.error('删除失败');
  }
}

onMounted(() => {
  reload();
});
</script>

<style scoped>
.memory-page {
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

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.search-input {
  width: 260px;
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

.text-cell {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.viewer-cell {
  font-size: 12px;
}

.none-hint {
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

.expand-body {
  padding: var(--spacing-sm) var(--spacing-md);
}

.expand-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--spacing-sm);
}

.expand-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.fact-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.fact-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: 4px 8px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-hover);
}

.fact-time {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-placeholder);
}

.fact-text {
  flex: 1;
  font-size: 12px;
  color: var(--text-primary);
}

.edit-desc {
  margin: 0 0 var(--spacing-md);
  font-size: 12px;
  color: var(--text-secondary);
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
