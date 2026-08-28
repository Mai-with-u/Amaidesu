<template>
  <div class="tools-page">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">工具目录</h1>
        <p class="page-subtitle">
          被动能力契约 · 由 ToolRegistry 统一调度 · 本页为只读目录，调用端点由工具体系团队提供
        </p>
      </div>
      <div class="header-actions">
        <el-button :icon="Refresh" :loading="loadingComponents" @click="refreshAll">
          刷新
        </el-button>
      </div>
    </header>

    <!-- 双计数卡片 -->
    <section class="totals-row">
      <article class="total-card total-providers">
        <div class="total-label">注册提供方</div>
        <div class="total-value mono">{{ providersTotal }}</div>
        <div class="total-sub">来自 tools.toml [tools.output.config] 段</div>
      </article>
      <article class="total-card total-actions">
        <div class="total-label">能力条目</div>
        <div class="total-value mono">{{ capabilitiesCount }}</div>
        <div class="total-sub">来自 ToolRegistry.list_tools() 运行时实例</div>
      </article>
      <article class="total-card total-hint">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="两个数为何不一致？"
          description="注册提供方是配置段声明的 Provider 数量；能力条目是 Provider 展开后向 Registry 注册的所有 ToolSpec 数量（同一个 Provider 可暴露多个 spec）。"
        />
      </article>
    </section>

    <!-- 过滤区 -->
    <section class="filter-row">
      <el-input
        v-model="searchQuery"
        placeholder="按能力名搜索（支持模糊匹配，如 speak / tool.* ）"
        clearable
        :prefix-icon="Search"
        class="search-input"
      />
      <div class="provider-chips">
        <span class="chips-label">提供方：</span>
        <el-check-tag
          v-for="provider in providers"
          :key="provider"
          :checked="activeProviders.has(provider)"
          @change="toggleProvider(provider)"
        >
          {{ provider }}
          <span class="chip-count">{{ providerCounts[provider] ?? 0 }}</span>
        </el-check-tag>
      </div>
    </section>

    <!-- 加载 / 错误状态 -->
    <div v-if="loadingCapabilities" class="state-block">
      <el-skeleton :rows="4" animated />
    </div>
    <el-alert
      v-else-if="capabilitiesError"
      :title="capabilitiesError"
      type="error"
      :closable="false"
      show-icon
      class="state-block"
    >
      <el-button size="small" type="primary" @click="fetchCapabilities">重试</el-button>
    </el-alert>

    <!-- 能力表格 -->
    <section v-else class="catalog-block">
      <div class="catalog-meta">
        共 <strong class="mono">{{ filteredActions.length }}</strong> / {{ capabilitiesCount }} 项
        <span v-if="searchQuery || activeProviders.size > 0" class="filter-suffix">
          （已应用过滤）
        </span>
      </div>
      <el-table
        :data="pagedActions"
        stripe
        size="default"
        class="catalog-table"
        @row-click="openDetail"
      >
        <el-table-column label="能力名" min-width="220">
          <template #default="{ row }">
            <code class="action-name-cell">
              <span class="provider-prefix">{{ providerOf(row.name) }}</span>
              <span class="dot-sep">·</span>
              <span class="local-name">{{ localName(row.name) }}</span>
            </code>
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="240">
          <template #default="{ row }">
            <span class="action-desc">{{ row.description || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="参数" width="88" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" type="info">
              {{ Object.keys(row.parameters ?? {}).length }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="right">
          <template #default>
            <el-tooltip content="工具调用端点规划中（工具体系）" placement="top" :show-after="100">
              <el-button size="small" disabled>执行</el-button>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="filteredActions.length > pageSize" class="pager-row">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :total="filteredActions.length"
          layout="prev, pager, next, total"
          background
          small
        />
      </div>
      <div v-if="filteredActions.length === 0" class="empty-block">
        <el-empty description="没有匹配的能力 — 调整搜索词或提供方筛选" />
      </div>
    </section>

    <!-- 参数详情抽屉 -->
    <el-drawer
      v-model="drawerOpen"
      direction="rtl"
      size="420px"
      :with-header="true"
      :title="drawerTitle"
      class="param-drawer"
    >
      <div v-if="activeAction" class="drawer-body">
        <section class="drawer-section">
          <h4 class="drawer-h">名称</h4>
          <code class="action-name-cell drawer-name">
            <span class="provider-prefix">{{ providerOf(activeAction.name) }}</span>
            <span class="dot-sep">·</span>
            <span class="local-name">{{ localName(activeAction.name) }}</span>
          </code>
        </section>

        <section class="drawer-section">
          <h4 class="drawer-h">描述</h4>
          <p class="drawer-desc">{{ activeAction.description || '（无描述）' }}</p>
        </section>

        <section class="drawer-section">
          <h4 class="drawer-h">参数（只读）</h4>
          <div v-if="paramEntries.length === 0" class="empty-block mini">
            <el-empty description="该能力无参数声明" :image-size="60" />
          </div>
          <ul v-else class="param-list">
            <li v-for="entry in paramEntries" :key="entry.key" class="param-item">
              <div class="param-item-head">
                <span class="param-key mono">{{ entry.key }}</span>
                <el-tag v-if="entry.spec.required" size="small" type="danger" effect="plain">
                  必填
                </el-tag>
                <el-tag size="small" effect="plain" type="info">{{ entry.spec.type }}</el-tag>
              </div>
              <p v-if="entry.spec.description" class="param-desc">
                {{ entry.spec.description }}
              </p>
              <dl v-if="hasConstraints(entry.spec)" class="param-constraints">
                <template v-if="entry.spec.default !== undefined && entry.spec.default !== null">
                  <dt>默认值</dt>
                  <dd class="mono">{{ formatDefault(entry.spec.default) }}</dd>
                </template>
                <template v-if="entry.spec.minimum !== undefined && entry.spec.minimum !== null">
                  <dt>最小值</dt>
                  <dd class="mono">{{ entry.spec.minimum }}</dd>
                </template>
                <template v-if="entry.spec.maximum !== undefined && entry.spec.maximum !== null">
                  <dt>最大值</dt>
                  <dd class="mono">{{ entry.spec.maximum }}</dd>
                </template>
              </dl>
            </li>
          </ul>
        </section>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { Refresh, Search } from '@element-plus/icons-vue';
import { useComponentsStore } from '@/stores';
import { capabilitiesApi } from '@/api';
import type { ParameterSpec, UnifiedActionEntry } from '@/types';

const componentsStore = useComponentsStore();
const { toolsList, loading: loadingComponents } = storeToRefs(componentsStore);

const providersTotal = computed(() => toolsList.value.length);

const capabilities = ref<UnifiedActionEntry[]>([]);
const loadingCapabilities = ref(false);
const capabilitiesError = ref<string | null>(null);

async function fetchCapabilities() {
  loadingCapabilities.value = true;
  capabilitiesError.value = null;
  try {
    const response = await capabilitiesApi.list();
    capabilities.value = response.data.actions ?? [];
  } catch (e) {
    capabilitiesError.value = e instanceof Error ? e.message : '无法加载能力列表';
    capabilities.value = [];
  } finally {
    loadingCapabilities.value = false;
  }
}

const capabilitiesCount = computed(() => capabilities.value.length);

// ===== Provider 聚合 =====

function providerOf(fullName: string): string {
  const dot = fullName.indexOf('.');
  return dot > 0 ? fullName.slice(0, dot) : '(unnamed)';
}

function localName(fullName: string): string {
  const dot = fullName.indexOf('.');
  return dot > 0 ? fullName.slice(dot + 1) : fullName;
}

const providers = computed(() => {
  const set = new Set<string>();
  for (const action of capabilities.value) {
    set.add(providerOf(action.name));
  }
  return [...set].sort();
});

const providerCounts = computed<Record<string, number>>(() => {
  const counts: Record<string, number> = {};
  for (const action of capabilities.value) {
    const p = providerOf(action.name);
    counts[p] = (counts[p] ?? 0) + 1;
  }
  return counts;
});

// ===== 过滤 =====

const searchQuery = ref('');
const activeProviders = ref<Set<string>>(new Set());

function toggleProvider(p: string) {
  // el-check-tag change 事件传入目标 checked 状态
  const next = new Set(activeProviders.value);
  if (next.has(p)) {
    next.delete(p);
  } else {
    next.add(p);
  }
  activeProviders.value = next;
}

const filteredActions = computed<UnifiedActionEntry[]>(() => {
  const q = searchQuery.value.trim().toLowerCase();
  const providersFilter = activeProviders.value;
  return capabilities.value.filter(a => {
    if (providersFilter.size > 0 && !providersFilter.has(providerOf(a.name))) {
      return false;
    }
    if (q) {
      const hay = `${a.name} ${a.description ?? ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
});

// ===== 分页 =====

const pageSize = 20;
const currentPage = ref(1);
const pagedActions = computed<UnifiedActionEntry[]>(() => {
  const start = (currentPage.value - 1) * pageSize;
  return filteredActions.value.slice(start, start + pageSize);
});

// ===== 抽屉详情 =====

const drawerOpen = ref(false);
const activeAction = ref<UnifiedActionEntry | null>(null);

const drawerTitle = computed(() =>
  activeAction.value ? `能力详情 · ${activeAction.value.name}` : '能力详情',
);

function openDetail(row: UnifiedActionEntry) {
  activeAction.value = row;
  drawerOpen.value = true;
}

const paramEntries = computed(() => {
  if (!activeAction.value) return [];
  return Object.entries(activeAction.value.parameters ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, spec]) => ({ key, spec }));
});

function hasConstraints(spec: ParameterSpec): boolean {
  return (
    (spec.default !== undefined && spec.default !== null) ||
    spec.minimum !== undefined ||
    spec.maximum !== undefined
  );
}

function formatDefault(value: unknown): string {
  if (typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'boolean' || typeof value === 'number') return String(value);
  return JSON.stringify(value);
}

// ===== 生命周期 =====

function refreshAll() {
  componentsStore.fetchComponents();
  void fetchCapabilities();
}

onMounted(() => {
  refreshAll();
});
</script>

<style scoped>
.tools-page {
  max-width: 1600px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-md);
  gap: var(--spacing-md);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  min-width: 0;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.page-subtitle {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex-shrink: 0;
}

/* ===== 双计数 ===== */

.totals-row {
  display: grid;
  grid-template-columns: 1fr 1fr 2fr;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-lg);
}

.total-card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: relative;
  overflow: hidden;
}

.total-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--border-color);
}

.total-providers::before {
  background: var(--color-tool);
}

.total-actions::before {
  background: var(--color-tool);
}

.total-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.total-value {
  font-size: 32px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.total-sub {
  font-size: 11px;
  color: var(--text-placeholder);
}

.total-hint :deep(.el-alert__title) {
  font-size: 13px;
  font-weight: 600;
}

.total-hint :deep(.el-alert__description) {
  font-size: 12px;
  line-height: 1.6;
}

/* ===== 过滤区 ===== */

.filter-row {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  margin-bottom: var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md);
}

.search-input {
  max-width: 420px;
}

.provider-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-xs);
}

.chips-label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-right: 4px;
}

.chip-count {
  margin-left: 4px;
  font-size: 10px;
  font-family: var(--font-mono);
  opacity: 0.7;
}

/* ===== 状态块 ===== */

.state-block {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-lg);
}

/* ===== 表格 ===== */

.catalog-block {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md);
}

.catalog-meta {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: var(--spacing-sm);
}

.filter-suffix {
  margin-left: 4px;
  color: var(--text-placeholder);
}

.catalog-table {
  cursor: pointer;
}

.action-name-cell {
  font-family: var(--font-mono);
  font-size: 12px;
}

.provider-prefix {
  color: var(--color-tool);
  font-weight: 600;
}

.dot-sep {
  color: var(--text-placeholder);
  margin: 0 2px;
}

.local-name {
  color: var(--text-primary);
}

.action-desc {
  color: var(--text-regular);
  font-size: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.pager-row {
  display: flex;
  justify-content: center;
  margin-top: var(--spacing-md);
}

.empty-block {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.empty-block.mini {
  border: none;
  background: var(--bg-hover);
}

/* ===== 抽屉 ===== */

.drawer-body {
  padding: 0 var(--spacing-md) var(--spacing-md);
}

.drawer-section {
  margin-bottom: var(--spacing-lg);
}

.drawer-h {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 var(--spacing-xs);
}

.drawer-name {
  font-size: 14px;
}

.drawer-desc {
  font-size: 13px;
  color: var(--text-regular);
  margin: 0;
  line-height: 1.6;
}

.param-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.param-item {
  background: var(--bg-hover);
  border-radius: var(--radius-md);
  padding: var(--spacing-sm) var(--spacing-md);
}

.param-item-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  margin-bottom: 4px;
}

.param-key {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.param-desc {
  font-size: 12px;
  color: var(--text-secondary);
  margin: 4px 0 0;
  line-height: 1.5;
}

.param-constraints {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px var(--spacing-xs);
  margin: var(--spacing-xs) 0 0;
  font-size: 12px;
}

.param-constraints dt {
  color: var(--text-placeholder);
}

.param-constraints dd {
  margin: 0;
  color: var(--text-regular);
}

@media (max-width: 900px) {
  .totals-row {
    grid-template-columns: 1fr;
  }
}
</style>
