<template>
  <div class="memory-page">
    <!-- 页面头：身份 + 总量 + 管理入口                                          -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">记忆</h1>
        <p class="page-subtitle">
          主播的长期事实记忆 · 检索 / 修订 / 清理
          <template v-if="stats && stats.total_facts > 0"
            >（共 {{ stats.total_facts }} 条）</template
          >
          <span v-if="stats && stats.latest_ms > 0" class="scope-note">
            最新写入 {{ relativeAge(stats.latest_ms) }}
          </span>
        </p>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="reload">刷新</el-button>
        <el-button @click="openRecall">召回测试</el-button>
        <el-button type="primary" @click="openCreate">新增记忆</el-button>
      </div>
    </header>

    <!-- 来源分布：哪条链路在往记忆库写                                          -->
    <section v-if="stats && stats.sources.length > 0" class="stats-strip">
      <span class="stats-label">来源分布</span>
      <el-tag
        v-for="s in stats.sources"
        :key="s.source"
        size="small"
        class="source-tag"
        :type="s.source === 'webui' ? 'warning' : 'info'"
      >
        {{ s.source || '（无来源）' }} × {{ s.count }}
      </el-tag>
    </section>

    <!-- 工具条：搜索（防抖）+ 排序                                              -->
    <div class="toolbar">
      <el-input
        v-model="searchText"
        class="search-input"
        placeholder="搜索内容 / 来源 / 标签"
        clearable
        :prefix-icon="Search"
      />
      <el-select v-model="orderBy" class="order-select">
        <el-option label="按写入时间" value="timestamp_ms" />
        <el-option label="按重要度" value="importance" />
      </el-select>
      <span class="grow" />
      <span class="count-hint mono">{{ shownRange }} / {{ total }}</span>
    </div>

    <!-- 记忆表                                                                  -->
    <section class="table-card">
      <el-table
        v-loading="loading"
        :data="items"
        stripe
        style="width: 100%"
        :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
      >
        <el-table-column label="时间" width="120">
          <template #default="{ row }">
            <span :title="formatTime(row.timestamp_ms)">{{ relativeAge(row.timestamp_ms) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="内容" min-width="280">
          <template #default="{ row }">
            <span class="text-cell">{{ row.text }}</span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source === 'webui' ? 'warning' : 'info'">
              {{ row.source || '（无来源）' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标签" min-width="140">
          <template #default="{ row }">
            <template v-if="row.tags.length > 0">
              <el-tag v-for="t in row.tags" :key="t" size="small" class="tag-chip" effect="plain">
                {{ t }}
              </el-tag>
            </template>
            <span v-else class="none-hint">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="importance" label="重要度" width="90" sortable />
        <el-table-column label="" width="120" align="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click.stop="openEdit(row)">编辑</el-button>
            <el-button link size="small" type="danger" @click.stop="removeFact(row)"
              >删除</el-button
            >
          </template>
        </el-table-column>
      </el-table>

      <div v-if="!loading && items.length === 0" class="empty-hint">
        {{
          searchText
            ? '没有匹配的记忆'
            : '还没有记忆——主播 Agent 学到新事实后会写进这里，也可手动新增'
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

    <!-- 新增 / 编辑对话框                                                       -->
    <el-dialog
      v-model="editVisible"
      :title="editingId === null ? '新增记忆' : '编辑记忆'"
      width="520px"
    >
      <el-form label-width="64px">
        <el-form-item label="内容">
          <el-input
            v-model="editForm.text"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="一条独立的事实，如：观众小明喜欢玩 Minecraft"
          />
        </el-form-item>
        <el-form-item label="标签">
          <el-input
            v-model="editForm.tagsInput"
            placeholder="用逗号分隔，如：游戏, 偏好（可留空）"
          />
        </el-form-item>
        <el-form-item label="重要度">
          <el-input-number v-model="editForm.importance" :min="0" :max="999" :step="1" />
          <span class="form-hint">越大越容易被召回</span>
        </el-form-item>
        <el-form-item v-if="editingId !== null" label="来源">
          <el-tag size="small" type="info">{{ editForm.source }}</el-tag>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveFact">保存</el-button>
      </template>
    </el-dialog>

    <!-- 召回测试对话框：与 Agent 侧 query_memory 同链路                          -->
    <el-dialog v-model="recallVisible" title="召回测试" width="640px">
      <p class="recall-desc">
        模拟主播 Agent 的 query_memory 工具：输入观众可能提到的话题，看记忆库会召回什么。
      </p>
      <div class="recall-bar">
        <el-input
          v-model="recallQuery"
          placeholder="如：弹幕互动 / Minecraft / 观众偏好"
          clearable
          @keyup.enter="runRecall"
        />
        <el-input-number v-model="recallTopK" :min="1" :max="20" class="topk-input" />
        <el-button type="primary" :loading="recallLoading" @click="runRecall">查询</el-button>
      </div>
      <div class="recall-results">
        <div v-for="hit in recallHits" :key="hit.memory_id" class="recall-hit">
          <div class="hit-meta">
            <el-tag size="small" type="success">score {{ hit.score.toFixed(1) }}</el-tag>
            <span class="hit-source">{{ hit.source || '（无来源）' }}</span>
            <span class="hit-time">{{ relativeAge(hit.timestamp_ms) }}</span>
          </div>
          <div class="hit-text">{{ hit.text }}</div>
        </div>
        <div v-if="!recallLoading && recallHits.length === 0 && recallRan" class="empty-hint">
          没有召回任何记忆
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 记忆管理 —— _memory_facts 的检索与清理入口
 *
 * 数据面：/api/v1/memory/*（列表 / 手工增改 / 删除 / 召回测试 / 统计）。
 * 搜索 250ms 防抖走服务端 LIKE（text / source / tags）；召回测试与
 * Agent 侧 query_memory 工具共用同一条 recall 链路，所见即 Agent 所得。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { Search } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { memoryApi } from '@/api';
import type { MemoryFactItem, MemoryStatsResponse, MemoryRecallHit } from '@/types';
import { relativeAge } from '@/utils/format';
import { confirmAction } from '@/utils/confirmAction';

const pageSize = 20;

const searchText = ref('');
const orderBy = ref<string>('timestamp_ms');
const page = ref(1);

const items = ref<MemoryFactItem[]>([]);
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

async function loadFacts(): Promise<void> {
  loading.value = true;
  try {
    const response = await memoryApi.listFacts({
      search: searchText.value.trim() || undefined,
      order_by: orderBy.value,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    });
    items.value = response.data.items;
    total.value = response.data.total;
  } catch {
    ElMessage.error('记忆列表加载失败');
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
  void loadFacts();
  void loadStats();
}

function onPageChange(next: number): void {
  page.value = next;
  void loadFacts();
}

// 搜索防抖：停顿 250ms 再请求；排序变化即时刷新并回到第一页
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(searchText, () => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    page.value = 1;
    void loadFacts();
  }, 250);
});
watch(orderBy, () => {
  page.value = 1;
  void loadFacts();
});

// ===== 新增 / 编辑 =====

const editVisible = ref(false);
const editingId = ref<number | null>(null);
const saving = ref(false);
const editForm = ref({ text: '', tagsInput: '', importance: 0, source: '' });

function parseTags(tagsInput: string): string[] {
  return tagsInput
    .split(/[,，]/)
    .map(t => t.trim())
    .filter(t => t.length > 0);
}

function openCreate(): void {
  editingId.value = null;
  editForm.value = { text: '', tagsInput: '', importance: 0, source: '' };
  editVisible.value = true;
}

function openEdit(fact: MemoryFactItem): void {
  editingId.value = fact.id;
  editForm.value = {
    text: fact.text,
    tagsInput: fact.tags.join(', '),
    importance: fact.importance,
    source: fact.source,
  };
  editVisible.value = true;
}

async function saveFact(): Promise<void> {
  const text = editForm.value.text.trim();
  if (!text) {
    ElMessage.warning('内容不能为空');
    return;
  }
  const tags = parseTags(editForm.value.tagsInput);
  saving.value = true;
  try {
    if (editingId.value === null) {
      const result = (
        await memoryApi.createFact({ text, tags, importance: editForm.value.importance })
      ).data;
      if (!result.accepted) {
        ElMessage.error(result.message || '写入被拒绝');
        return;
      }
      ElMessage.success('已新增记忆');
    } else {
      await memoryApi.updateFact(editingId.value, {
        text,
        tags,
        importance: editForm.value.importance,
      });
      ElMessage.success('已更新记忆');
    }
    editVisible.value = false;
    page.value = 1;
    reload();
  } catch {
    ElMessage.error('保存失败');
  } finally {
    saving.value = false;
  }
}

async function removeFact(fact: MemoryFactItem): Promise<void> {
  const preview = fact.text.length > 40 ? `${fact.text.slice(0, 37)}...` : fact.text;
  if (!(await confirmAction(`删除这条记忆？「${preview}」`, '删除记忆'))) return;
  try {
    await memoryApi.deleteFact(fact.id);
    ElMessage.success('已删除');
    reload();
  } catch {
    ElMessage.error('删除失败');
  }
}

// ===== 召回测试 =====

const recallVisible = ref(false);
const recallQuery = ref('');
const recallTopK = ref(5);
const recallHits = ref<MemoryRecallHit[]>([]);
const recallLoading = ref(false);
const recallRan = ref(false);

function openRecall(): void {
  recallHits.value = [];
  recallRan.value = false;
  recallVisible.value = true;
}

async function runRecall(): Promise<void> {
  const query = recallQuery.value.trim();
  if (!query) {
    ElMessage.warning('请输入查询内容');
    return;
  }
  recallLoading.value = true;
  recallRan.value = true;
  try {
    recallHits.value = (await memoryApi.recall({ query, top_k: recallTopK.value })).data.hits;
  } catch {
    ElMessage.error('召回请求失败');
  } finally {
    recallLoading.value = false;
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

.scope-note {
  margin-left: var(--spacing-sm);
  font-size: 11px;
  color: var(--text-placeholder);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.stats-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
}

.stats-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.source-tag {
  font-family: monospace;
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

.text-cell {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary);
}

.tag-chip {
  margin-right: 4px;
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

.form-hint {
  margin-left: var(--spacing-sm);
  font-size: 11px;
  color: var(--text-placeholder);
}

.recall-desc {
  margin: 0 0 var(--spacing-md);
  font-size: 12px;
  color: var(--text-secondary);
}

.recall-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.topk-input {
  width: 100px;
}

.recall-results {
  margin-top: var(--spacing-md);
  max-height: 420px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.recall-hit {
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-hover);
}

.hit-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  margin-bottom: 4px;
}

.hit-source {
  font-size: 11px;
  color: var(--text-secondary);
  font-family: monospace;
}

.hit-time {
  font-size: 11px;
  color: var(--text-placeholder);
}

.hit-text {
  font-size: 13px;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
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
