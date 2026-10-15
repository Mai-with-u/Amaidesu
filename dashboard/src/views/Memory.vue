<template>
  <div class="memory-page">
    <!-- 页面头：身份 + 总量 + 刷新                                               -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">常客名册</h1>
        <p class="page-subtitle">
          主播对常客的印象 · 查看 / 纠正 / 清理
          <template v-if="stats && stats.profile_count > 0">
            （常客 {{ stats.profile_count }} 位）</template
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
        placeholder="搜索印象内容 / 用户 ID"
        clearable
        :prefix-icon="Search"
      />
      <span class="grow" />
      <span class="count-hint mono">{{ shownRange }} / {{ total }}</span>
    </div>

    <!-- 名册表                                                                  -->
    <section class="table-card">
      <el-table
        v-loading="loading"
        :data="items"
        stripe
        style="width: 100%"
        :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
      >
        <el-table-column label="观众" width="220">
          <template #default="{ row }">
            <span class="mono viewer-cell">{{ row.platform }}/{{ row.user_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="印象" min-width="320">
          <template #default="{ row }">
            <span class="text-cell">{{ row.profile_text }}</span>
          </template>
        </el-table-column>
        <el-table-column label="原料水位" width="120">
          <template #default="{ row }">
            <span
              v-if="row.last_compressed_at_ms > 0"
              :title="formatTime(row.last_compressed_at_ms)"
            >
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
            <el-button link size="small" type="danger" @click.stop="removeProfile(row)"
              >删除</el-button
            >
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && items.length === 0" class="empty-hint">
        {{
          searchText
            ? '没有匹配的常客'
            : '还没有常客印象——每场收播后，会为本场最活跃且互动达门槛的观众整理印象'
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

    <!-- 印象纠正对话框                                                          -->
    <el-dialog v-model="editVisible" title="纠正印象" width="560px">
      <p class="edit-desc">
        直接编辑印象文本。纠正后主播下次决策即读到新内容；之后收播整理会以人工纠正版为旧印象继续更新。
      </p>
      <el-input
        v-model="editForm.profileText"
        type="textarea"
        :rows="6"
        maxlength="1000"
        show-word-limit
        placeholder="印象正文"
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
 * 常客名册管理 —— viewer_profiles 的查看 / 纠正 / 清理入口
 *
 * 数据面：/api/v1/memory/*（名册列表 / 纠正 / 删除 / 统计）。印象由主播在每场
 * 收播时为本场活跃的常客整理，这里只提供人工查看与纠正。
 * 搜索 250ms 防抖走服务端 LIKE（印象文本 / user_id）。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { Search } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { memoryApi } from '@/api';
import type { ViewerProfileItem, MemoryStatsResponse } from '@/types';
import { formatDateTimeCn as formatTime, relativeAge } from '@/utils/format';
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
    ElMessage.error('常客名册加载失败');
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

// ===== 印象纠正 =====

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
    ElMessage.warning('印象内容不能为空');
    return;
  }
  saving.value = true;
  try {
    await memoryApi.updateProfile(target.platform, target.user_id, { profile_text: text });
    ElMessage.success('已纠正印象');
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
      `删除「${rowKey(profile)}」的印象？删除后该观众回到无印象态（之后收播时若仍活跃会重新整理）。`,
      '删除印象',
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
