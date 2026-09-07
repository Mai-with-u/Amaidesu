<template>
  <div class="tools-page">
    <!-- ============================================================== -->
    <!-- LEFT：分类列表（narrow, 240px）                                   -->
    <!-- ============================================================== -->
    <aside class="list-panel" aria-label="工具提供者分类">
      <header class="list-header">
        <div class="list-header-main">
          <h2 class="list-title">工具分类</h2>
          <span class="list-count">
            <span class="list-count-on">{{ totalToolCount }}</span>
            <span class="list-count-suffix">个工具</span>
          </span>
        </div>
        <div class="list-header-actions">
          <el-button size="small" :loading="loading" @click="refreshAll">刷新</el-button>
        </div>
      </header>

      <div v-if="loading" class="list-empty">
        <el-skeleton :rows="4" animated />
      </div>
      <el-alert
        v-else-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
        class="list-error"
      >
        <el-button size="small" type="primary" @click="refreshAll">重试</el-button>
      </el-alert>
      <div v-else-if="categoryNav.length === 0" class="list-empty">
        <el-empty :image-size="64" description="暂无分类数据" />
      </div>
      <ul v-else class="category-list" role="listbox">
        <li
          v-for="cat in categoryNav"
          :key="cat.category"
          class="category-row"
          :class="{
            'is-selected': cat.category === activeCategory,
            'is-on': cat.enabledCount > 0,
            'is-off': cat.enabledCount === 0,
          }"
          role="option"
          :aria-selected="cat.category === activeCategory"
          @click="selectCategory(cat.category)"
        >
          <span class="status-dot" aria-hidden="true" />
          <span class="category-name">{{ cat.label }}</span>
          <el-tag size="small" effect="plain" type="info" class="count-tag">
            {{ cat.toolCount }}
          </el-tag>
        </li>
      </ul>
    </aside>

    <!-- ============================================================== -->
    <!-- RIGHT：分类详情 + 提供者分组                                      -->
    <!-- ============================================================== -->
    <main class="detail-panel" aria-label="分类详情">
      <template v-if="activeCategoryData">
        <!-- 1. 分类头：名称 + 描述 + 搜索 -->
        <header class="detail-header">
          <div class="detail-title-block">
            <div class="detail-title-row">
              <h1 class="detail-name">{{ activeMeta.label }}</h1>
              <span class="type-chip mono">{{ activeCategory }}</span>
            </div>
            <p class="detail-description">{{ activeMeta.description }}</p>
          </div>
          <div class="detail-actions">
            <div v-if="bulkSwitchable" class="bulk-switch">
              <span class="bulk-label">全部启用</span>
              <el-switch
                :model-value="bulkState === 'all'"
                :indeterminate="bulkState === 'mixed'"
                :loading="bulkToggling"
                @change="onBulkToggle($event as boolean)"
              />
            </div>
            <el-input
              v-model="searchQuery"
              placeholder="搜索工具名或描述"
              clearable
              :prefix-icon="Search"
              class="search-input"
            />
          </div>
        </header>

        <!-- 2. 元信息条 -->
        <div class="details-strip" aria-label="状态摘要">
          <div class="stat-chip">
            <span class="chip-label">工具</span>
            <span class="chip-value mono">{{ totalToolCount }}</span>
          </div>
          <div class="stat-chip">
            <span class="chip-label">启用提供者</span>
            <span class="chip-value">
              <span class="chip-yes">{{ activeEnabledCount }}</span>
              <span class="chip-divider">/</span>
              <span>{{ activeSwitchableCount }}</span>
            </span>
          </div>
          <div v-if="pendingRestartCount > 0" class="stat-chip stat-chip--warning">
            <span class="chip-label">待重启</span>
            <span class="chip-value">{{ pendingRestartCount }}</span>
          </div>
        </div>

        <!-- 3. 提供者分组：THE MAIN SPACE -->
        <section class="provider-panel" aria-label="提供者列表">
          <div v-if="(activeCategoryData?.providers ?? []).length === 0" class="detail-empty">
            <el-empty description="该分类下没有提供者" />
          </div>
          <div v-else-if="visibleProviders.length === 0" class="detail-empty">
            <el-empty description="没有匹配的工具" :image-size="64" />
          </div>
          <article v-for="unit in visibleProviders" v-else :key="unit.key" class="provider-card">
            <header class="provider-head">
              <div class="provider-title">
                <span class="provider-name mono">{{ unit.key }}</span>
                <span class="provider-desc">{{ unit.description }}</span>
              </div>
              <div class="provider-status">
                <el-tag size="small" effect="plain" type="info" class="count-tag">
                  {{ unit.tool_count }} 个工具
                </el-tag>
                <el-tooltip
                  v-if="unit.switchable && unit.enabled && unit.tool_count === 0"
                  content="配置已启用但运行时没有工具——改动后尚未重启，重启后才会装配"
                  placement="top"
                  :show-after="100"
                >
                  <el-tag size="small" type="warning" effect="light">未生效，待重启</el-tag>
                </el-tooltip>
                <el-tooltip
                  v-else-if="unit.switchable && !unit.enabled && unit.tool_count > 0"
                  content="配置已停用但运行时仍有工具——重启后卸载"
                  placement="top"
                  :show-after="100"
                >
                  <el-tag size="small" type="warning" effect="light">重启后卸载</el-tag>
                </el-tooltip>
                <el-switch
                  v-if="unit.switchable"
                  :model-value="unit.enabled"
                  :loading="toggling.has(unit.key)"
                  @change="onToggle(unit, $event as boolean)"
                />
                <el-tag v-else size="small" effect="plain">随 Agent 启用</el-tag>
              </div>
            </header>

            <el-table
              v-if="toolsOf(unit).length > 0"
              :data="toolsOf(unit)"
              size="small"
              class="provider-table"
              :row-class-name="rowClass"
              @row-click="openDetail"
            >
              <el-table-column label="工具名" min-width="220">
                <template #default="{ row }">
                  <code class="tool-name mono">{{ row.name }}</code>
                </template>
              </el-table-column>
              <el-table-column label="描述" min-width="260">
                <template #default="{ row }">
                  <span class="tool-desc">{{ row.description || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="类型" width="90" align="center">
                <template #default="{ row }">
                  <el-tooltip
                    v-if="row.kind === 'async'"
                    :content="`结果回传事件：${row.result_event ?? 'tool.result.' + row.name}`"
                    placement="top"
                    :show-after="100"
                  >
                    <el-tag size="small" type="warning" effect="plain">async</el-tag>
                  </el-tooltip>
                  <el-tag v-else size="small" effect="plain">sync</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="参数" width="70" align="center">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" type="info">
                    {{ Object.keys(row.parameters ?? {}).length }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="启停" width="80" align="center">
                <template #default="{ row }">
                  <div class="cell-switch" @click.stop>
                    <el-switch
                      size="small"
                      :model-value="!row.disabled"
                      :loading="toolToggling.has(row.name)"
                      @change="onToolToggle(row, $event as boolean)"
                    />
                  </div>
                </template>
              </el-table-column>
            </el-table>
            <div v-else class="provider-table-empty">没有匹配的工具</div>
          </article>
        </section>
      </template>

      <div v-else class="detail-empty full">
        <el-empty description="从左侧选择一个分类查看提供者与工具" />
      </div>
    </main>

    <!-- 参数详情抽屉 -->
    <el-drawer
      v-model="drawerOpen"
      direction="rtl"
      size="420px"
      :with-header="true"
      :title="drawerTitle"
      class="param-drawer"
    >
      <div v-if="activeTool" class="drawer-body">
        <section class="drawer-section">
          <h4 class="drawer-h">名称</h4>
          <code class="tool-name drawer-name">{{ activeTool.name }}</code>
          <div class="drawer-tags">
            <el-tag v-if="activeTool.provider" size="small" effect="plain" type="info" class="mono">
              {{ activeTool.provider }}
            </el-tag>
            <el-tag
              size="small"
              :type="activeTool.kind === 'async' ? 'warning' : 'info'"
              effect="plain"
            >
              {{ activeTool.kind ?? 'sync' }}
            </el-tag>
          </div>
          <p v-if="activeTool.kind === 'async'" class="drawer-result-event">
            结果回传：
            <code class="mono">{{
              activeTool.result_event ?? `tool.result.${activeTool.name}`
            }}</code>
          </p>
        </section>

        <section class="drawer-section">
          <h4 class="drawer-h">描述</h4>
          <p class="drawer-desc">{{ activeTool.description || '（无描述）' }}</p>
        </section>

        <section class="drawer-section">
          <h4 class="drawer-h">参数（只读）</h4>
          <div v-if="paramEntries.length === 0" class="drawer-empty">
            <el-empty description="该工具无参数声明" :image-size="60" />
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
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { Search } from '@element-plus/icons-vue';
import { toolsApi } from '@/api';
import type { ParameterSpec, ToolCategoryView, ToolEntry, ToolProviderUnit } from '@/types';

// ===== 分类元数据 =====

const CATEGORY_META: Record<string, { label: string; description: string }> = {
  avatar: {
    label: '虚拟形象',
    description: 'VTubeStudio / VRChat / Warudo 等虚拟形象后端提供的工具',
  },
  studio: { label: '演播室', description: 'OBS 等演播室控制后端提供的工具' },
  vision: { label: '视觉', description: '屏幕感知能力（look_at_screen）' },
  memory: { label: '记忆', description: '长期记忆检索（query_memory）' },
  mcp: { label: 'MCP', description: '外部 MCP server 提供的工具' },
  game: { label: '游戏 Agent', description: '游戏 Agent 自声明的工具（text_adv 等）' },
  framework: { label: '框架', description: '框架内置工具（AgentControl 等）' },
};

function categoryMeta(category: string) {
  return CATEGORY_META[category] ?? { label: category, description: '' };
}

// ===== 数据加载 =====

const categories = ref<ToolCategoryView[]>([]);
const tools = ref<ToolEntry[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

async function refreshAll() {
  loading.value = true;
  error.value = null;
  try {
    const [catResp, toolResp] = await Promise.all([toolsApi.listCategories(), toolsApi.list()]);
    categories.value = catResp.data.categories ?? [];
    tools.value = toolResp.data.tools ?? [];
    ensureActiveCategory();
  } catch (e) {
    error.value = e instanceof Error ? e.message : '无法加载工具数据';
    categories.value = [];
    tools.value = [];
  } finally {
    loading.value = false;
  }
}

// ===== 分类列表（左侧） =====

const activeCategory = ref('');

const categoryNav = computed(() =>
  categories.value.map(cat => {
    const switchable = cat.providers.filter(p => p.switchable);
    return {
      category: cat.category,
      label: categoryMeta(cat.category).label,
      toolCount: cat.providers.reduce((sum, p) => sum + p.tool_count, 0),
      enabledCount: switchable.filter(p => p.enabled).length,
    };
  }),
);

const totalToolCount = computed(() =>
  categoryNav.value.reduce((sum, cat) => sum + cat.toolCount, 0),
);

function ensureActiveCategory() {
  if (!categories.value.some(cat => cat.category === activeCategory.value)) {
    activeCategory.value = categories.value[0]?.category ?? '';
  }
}

function selectCategory(category: string): void {
  activeCategory.value = category;
}

const activeMeta = computed(() => categoryMeta(activeCategory.value));

const activeCategoryData = computed(
  () => categories.value.find(cat => cat.category === activeCategory.value) ?? null,
);

const activeSwitchableCount = computed(
  () => (activeCategoryData.value?.providers ?? []).filter(p => p.switchable).length,
);

const activeEnabledCount = computed(
  () => (activeCategoryData.value?.providers ?? []).filter(p => p.switchable && p.enabled).length,
);

const pendingRestartCount = computed(
  () =>
    (activeCategoryData.value?.providers ?? []).filter(
      p => p.switchable && (p.enabled ? p.tool_count === 0 : p.tool_count > 0),
    ).length,
);

// ===== 提供者开关 =====

const toggling = reactive(new Set<string>());

// ===== 分类总开关（聚合操作：一键开/关全部提供者） =====

const bulkSwitchable = computed(() =>
  (activeCategoryData.value?.providers ?? []).some(p => p.switchable),
);

const bulkState = computed<'all' | 'none' | 'mixed'>(() => {
  const switchable = (activeCategoryData.value?.providers ?? []).filter(p => p.switchable);
  if (switchable.length === 0) return 'none';
  const on = switchable.filter(p => p.enabled).length;
  if (on === switchable.length) return 'all';
  if (on === 0) return 'none';
  return 'mixed';
});

const bulkToggling = ref(false);

async function onBulkToggle(next: boolean) {
  const units = (activeCategoryData.value?.providers ?? []).filter(p => p.switchable);
  if (units.length === 0) return;
  bulkToggling.value = true;
  try {
    const results = await Promise.allSettled(
      units.map(u =>
        toolsApi.controlProvider(activeCategory.value, u.key, next ? 'enable' : 'disable'),
      ),
    );
    const failed = results.filter(r => r.status === 'rejected').length;
    if (failed > 0) {
      ElMessage.warning(`部分提供者写回失败（${failed}/${units.length}），请重试`);
    } else {
      ElMessage.success(
        `${next ? '启用' : '停用'} ${units.length} 个提供者（写入 tools.toml），重启后生效`,
      );
    }
  } finally {
    bulkToggling.value = false;
    await refreshAll();
  }
}

// ===== 工具级停用 =====

const toolToggling = reactive(new Set<string>());

async function onToolToggle(row: ToolEntry, next: boolean) {
  toolToggling.add(row.name);
  try {
    const response = await toolsApi.controlTool(row.name, next ? 'enable' : 'disable');
    row.disabled = !next;
    ElMessage.success(response.data.message ?? '已写回配置，重启后生效');
  } catch (e) {
    const detail = e instanceof Error ? e.message : `开关写回失败（${row.name}）`;
    ElMessage.error(detail);
  } finally {
    toolToggling.delete(row.name);
  }
}

async function onToggle(unit: ToolProviderUnit, next: boolean) {
  toggling.add(unit.key);
  try {
    const response = await toolsApi.controlProvider(
      activeCategory.value,
      unit.key,
      next ? 'enable' : 'disable',
    );
    unit.enabled = next;
    ElMessage.success(response.data.message ?? '已写回配置，重启后生效');
  } catch (e) {
    const detail = e instanceof Error ? e.message : `开关写回失败（${unit.key}）`;
    ElMessage.error(detail);
  } finally {
    toggling.delete(unit.key);
  }
}

// ===== 工具过滤 =====

const searchQuery = ref('');

function toolsOf(unit: ToolProviderUnit): ToolEntry[] {
  const q = searchQuery.value.trim().toLowerCase();
  return tools.value.filter(t => {
    if (t.category !== activeCategory.value || t.provider !== unit.provider_name) return false;
    if (q && !`${t.name} ${t.description ?? ''}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

const visibleProviders = computed<ToolProviderUnit[]>(() => {
  const units = activeCategoryData.value?.providers ?? [];
  if (!searchQuery.value.trim()) return units;
  return units.filter(unit => toolsOf(unit).length > 0);
});

// ===== 抽屉详情 =====

const drawerOpen = ref(false);
const activeTool = ref<ToolEntry | null>(null);

const drawerTitle = computed(() =>
  activeTool.value ? `工具详情 · ${activeTool.value.name}` : '工具详情',
);

function openDetail(row: ToolEntry) {
  activeTool.value = row;
  drawerOpen.value = true;
}

function rowClass({ row }: { row: ToolEntry }): string {
  return row.disabled ? 'is-disabled-row' : '';
}

const paramEntries = computed(() => {
  if (!activeTool.value) return [];
  return Object.entries(activeTool.value.parameters ?? {})
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

watch(activeCategory, () => {
  searchQuery.value = '';
});

onMounted(() => {
  void refreshAll();
});
</script>

<style scoped>
/* ============================================================ */
/* 页面布局：左 240 + 右 flex-1（与采集器 / Agent 页同构）          */
/* ============================================================ */
.tools-page {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: var(--spacing-md);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
  min-height: 640px;
}

/* ============================================================ */
/* LEFT：分类列表                                                */
/* ============================================================ */
.list-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.list-header {
  padding: var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  background: var(--bg-card);
  flex-shrink: 0;
}

.list-header-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--spacing-sm);
}

.list-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

.list-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
}

.list-count-on {
  color: var(--color-tool);
  font-weight: 600;
  font-size: 13px;
}

.list-count-suffix {
  color: var(--text-placeholder);
}

.list-header-actions {
  display: flex;
  gap: 6px;
}

.list-header-actions :deep(.el-button) {
  margin-left: 0;
  width: 100%;
}

.list-error {
  margin: var(--spacing-sm);
  flex-shrink: 0;
}

.list-empty {
  padding: var(--spacing-lg) var(--spacing-sm);
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.category-list {
  list-style: none;
  margin: 0;
  padding: var(--spacing-xs);
  overflow-y: auto;
  flex: 1;
}

.category-list::-webkit-scrollbar {
  width: 6px;
}
.category-list::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.category-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition:
    background var(--transition-fast),
    transform var(--transition-fast);
  margin-bottom: 2px;
  user-select: none;
}

.category-row:hover {
  background: var(--bg-hover);
}

.category-row.is-selected {
  background: var(--color-tool-bg);
  box-shadow: inset 3px 0 0 0 var(--color-tool);
}

.category-row.is-selected .category-name {
  color: var(--text-primary);
  font-weight: 600;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  border: 1.5px solid var(--text-placeholder);
  background: transparent;
  transition: background var(--transition-normal);
}

.category-row.is-on .status-dot {
  background: var(--color-tool);
  border-color: var(--color-tool);
}

.category-row.is-off .status-dot {
  border-style: solid;
  border-color: var(--text-placeholder);
  background: transparent;
}

.category-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.count-tag {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 10px;
  height: 18px;
  padding: 0 6px;
  line-height: 16px;
}

/* ============================================================ */
/* RIGHT：详情面板                                               */
/* ============================================================ */
.detail-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  min-width: 0;
  overflow: hidden;
}

/* ----- 1. 分类头 ----- */
.detail-header {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md) var(--spacing-lg);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--spacing-md);
  flex-shrink: 0;
}

.detail-title-block {
  flex: 1;
  min-width: 0;
}

.detail-title-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.detail-name {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
  letter-spacing: -0.01em;
}

.type-chip {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 8px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-page);
}

.detail-description {
  font-size: 13px;
  color: var(--text-regular);
  margin: var(--spacing-sm) 0 0;
  line-height: 1.6;
  max-width: 720px;
}

.detail-actions {
  display: flex;
  gap: var(--spacing-sm);
  flex-shrink: 0;
  align-items: center;
}

.search-input {
  width: 240px;
}

.bulk-switch {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex-shrink: 0;
}

.bulk-label {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.cell-switch {
  display: inline-flex;
  align-items: center;
}

/* ----- 2. 元信息条 ----- */
.details-strip {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
  flex-shrink: 0;
}

.stat-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 6px 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  font-size: 12px;
}

.stat-chip--warning {
  background: var(--color-warning-bg, rgba(230, 162, 60, 0.1));
  border-color: transparent;
}

.chip-label {
  color: var(--text-secondary);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.chip-value {
  color: var(--text-primary);
  font-weight: 600;
}

.chip-yes {
  color: var(--color-tool);
}

.chip-divider {
  color: var(--text-placeholder);
  margin: 0 2px;
}

/* ----- 3. 提供者分组：主角 ----- */
.provider-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--spacing-sm);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.provider-panel::-webkit-scrollbar {
  width: 8px;
}

.provider-panel::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 4px;
}

.provider-card {
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  overflow: hidden;
  flex-shrink: 0;
}

.provider-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-hover);
  border-bottom: 1px solid var(--border-color-light);
}

.provider-title {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  min-width: 0;
}

.provider-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.provider-desc {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.provider-status {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  flex-shrink: 0;
}

.provider-table {
  cursor: pointer;
}

.provider-table :deep(.el-table__row) {
  height: 40px;
}

.provider-table :deep(.is-disabled-row) .tool-name,
.provider-table :deep(.is-disabled-row) .tool-desc {
  color: var(--text-placeholder);
  text-decoration: line-through;
}

.provider-table-empty {
  padding: var(--spacing-md);
  font-size: 12px;
  color: var(--text-placeholder);
  text-align: center;
}

.tool-name {
  font-size: 12px;
  color: var(--text-primary);
}

.drawer-name {
  font-size: 14px;
}

.tool-desc {
  color: var(--text-regular);
  font-size: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ============================================================ */
/* Empty                                                         */
/* ============================================================ */
.detail-empty {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}

.detail-empty.full {
  min-height: 320px;
}

/* ============================================================ */
/* 抽屉                                                          */
/* ============================================================ */

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

.drawer-tags {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
  margin-top: var(--spacing-xs);
}

.drawer-result-event {
  font-size: 12px;
  color: var(--text-secondary);
  margin: var(--spacing-xs) 0 0;
}

.drawer-desc {
  font-size: 13px;
  color: var(--text-regular);
  margin: 0;
  line-height: 1.6;
}

.drawer-empty {
  background: var(--bg-hover);
  border-radius: var(--radius-md);
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

/* ============================================================ */
/* Responsive                                                    */
/* ============================================================ */
@media (max-width: 1023px) {
  .tools-page {
    grid-template-columns: 200px minmax(0, 1fr);
  }

  .detail-name {
    font-size: 20px;
  }
}

@media (max-width: 768px) {
  .tools-page {
    grid-template-columns: 1fr;
    height: auto;
    min-height: 0;
  }

  .list-panel {
    max-height: 280px;
  }

  .detail-header {
    flex-direction: column;
    align-items: stretch;
  }

  .search-input {
    width: 100%;
  }

  .provider-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .provider-status {
    flex-wrap: wrap;
  }
}
</style>
