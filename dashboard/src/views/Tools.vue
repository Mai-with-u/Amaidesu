<template>
  <div class="tools-page">
    <!-- LEFT：分类列表（narrow, 240px）                                   -->
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

    <!-- RIGHT：分类详情 + 提供者分组                                      -->
    <main class="detail-panel" aria-label="分类详情">
      <template v-if="activeCategoryData">
        <!-- 分类头：名称 + 描述 + 搜索 -->
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

        <!-- 元信息条 -->
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

        <!-- 提供者分组 -->
        <section class="provider-panel" aria-label="提供者列表">
          <!-- 视觉分类专属：显示器选择 + 预览叠框 + 拖框落盘（独立组件） -->
          <VisionCapturePanel v-if="activeCategory === 'vision'" class="vision-panel-mount" />

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
                  v-if="unit.degraded"
                  :content="
                    unit.last_error ||
                    'Provider 已登记但 0 个工具（连接失败降级登记，恢复后自动补注册）'
                  "
                  placement="top"
                  :show-after="100"
                >
                  <el-tag size="small" type="danger" effect="light">连接失败</el-tag>
                </el-tooltip>
                <el-tooltip
                  v-else-if="unit.switchable && unit.enabled && unit.registered === false"
                  content="配置已启用但 Provider 未装配——重启后生效"
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
                <el-button
                  v-if="unit.supports_reconnect"
                  size="small"
                  :loading="reconnecting.has(unit.key)"
                  @click="onReconnect(unit.key)"
                >
                  重连
                </el-button>
                <el-switch
                  v-if="unit.switchable"
                  :model-value="unit.enabled"
                  :loading="toggling.has(unit.key)"
                  @change="onToggle(unit, $event as boolean)"
                />
                <el-tag v-else size="small" effect="plain">随 Agent 启用</el-tag>
              </div>
            </header>
            <p v-if="unit.notice" class="provider-notice">{{ unit.notice }}</p>

            <el-table
              v-if="toolsOf(unit).length > 0"
              :data="toolsOf(unit)"
              size="small"
              class="provider-table"
              :row-class-name="rowClass"
              @row-click="openDetail"
            >
              <el-table-column label="工具名" width="170">
                <template #default="{ row }">
                  <code class="tool-name mono">{{ row.name }}</code>
                </template>
              </el-table-column>
              <el-table-column label="描述" min-width="360">
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
              <el-table-column label="状态" width="90" align="center">
                <template #default="{ row }">
                  <div class="health-cell" @click.stop>
                    <el-tooltip
                      v-if="row.health && row.health.state === 'tripped'"
                      placement="top"
                      :show-after="100"
                      :content="healthTooltip(row)"
                    >
                      <el-tag size="small" type="danger" effect="dark">已熔断</el-tag>
                    </el-tooltip>
                    <span v-else class="health-dot" aria-label="健康" />
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="170" align="center">
                <template #default="{ row }">
                  <div class="cell-action" @click.stop>
                    <el-button link type="primary" size="small" @click="openDetail(row)">
                      详情 / 调试
                    </el-button>
                    <el-tooltip
                      v-if="row.supports_reconnect"
                      content="重连其所属工具提供者并恢复熔断工具"
                      placement="top"
                      :show-after="100"
                    >
                      <el-button
                        size="small"
                        type="warning"
                        link
                        :loading="row.provider ? reconnecting.has(row.provider) : false"
                        :disabled="!row.provider"
                        @click="onReconnect(row.provider ?? '')"
                      >
                        重连
                      </el-button>
                    </el-tooltip>
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

    <!-- 工具详情对话框 -->
    <el-dialog
      v-model="detailOpen"
      :title="detailTitle"
      width="min(1100px, 95%)"
      :close-on-click-modal="false"
      destroy-on-close
      class="tool-detail-dialog"
    >
      <div v-if="activeTool" class="detail-pane">
        <section class="detail-section">
          <div class="detail-title-row">
            <code class="tool-name detail-name">{{ activeTool.name }}</code>
            <code class="detail-fullname mono">{{ activeTool.full_name }}</code>
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
          <p v-if="activeTool.kind === 'async'" class="detail-result-event">
            结果回传：
            <code class="mono">{{
              activeTool.result_event ?? `tool.result.${activeTool.full_name}`
            }}</code>
          </p>
          <p
            ref="descRef"
            class="detail-desc"
            :class="{ 'is-expanded': descExpanded, 'is-toggleable': descClamped || descExpanded }"
            @click="toggleDesc"
          >
            {{ activeTool.description || '（无描述）' }}
          </p>
          <span v-if="descClamped || descExpanded" class="desc-toggle" @click="toggleDesc">
            {{ descExpanded ? '收起' : '展开全文' }}
          </span>
        </section>

        <section class="detail-section params-section">
          <h4 class="detail-h">参数</h4>
          <div class="params-scroll">
            <div v-if="paramEntries.length === 0" class="detail-none">
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
                <p v-if="entry.spec.description" class="param-desc" :title="entry.spec.description">
                  {{ entry.spec.description }}
                </p>
                <div class="param-input">
                  <el-switch v-if="entry.spec.type === 'boolean'" v-model="formModel[entry.key]" />
                  <el-input-number
                    v-else-if="entry.spec.type === 'integer' || entry.spec.type === 'number'"
                    v-model="formModel[entry.key]"
                    class="param-number"
                    :step="entry.spec.type === 'integer' ? 1 : 0.1"
                    :precision="entry.spec.type === 'integer' ? 0 : undefined"
                    :min="entry.spec.minimum"
                    :max="entry.spec.maximum"
                  />
                  <el-input
                    v-else
                    v-model="formModel[entry.key]"
                    type="textarea"
                    :autosize="{ minRows: 1, maxRows: 6 }"
                    :placeholder="
                      entry.spec.type === 'json' ? JSON_PARAM_PLACEHOLDER : '字符串参数'
                    "
                  />
                </div>
              </li>
            </ul>
          </div>
        </section>
        <!-- 执行按钮在参数滚动区之外：填多少参数都常驻可见 -->
        <div class="invoke-bar">
          <el-button type="primary" size="small" :loading="invoking" @click="onInvoke">
            执行调用
          </el-button>
          <span v-if="isInternalTool" class="invoke-hint">内部工具，调用真实生效</span>
        </div>

        <!-- 结果区：面板内自行滚动，对话框高度不随之增长 -->
        <section v-if="invokeResult" class="detail-section result-section">
          <h4 class="detail-h">执行结果</h4>
          <div class="result-head">
            <el-tag :type="invokeResult.success ? 'success' : 'danger'" size="small">
              {{ invokeResult.success ? '成功' : '失败' }}
            </el-tag>
            <span class="result-meta mono">{{ invokeResult.duration_ms }} ms</span>
          </div>
          <p v-if="invokeResult.error_message" class="result-error">
            {{ invokeResult.error_message }}
          </p>
          <template v-for="view in resultViews" :key="view">
            <img
              v-if="view.kind === 'image'"
              class="result-image"
              :src="`data:${view.mime || 'image/png'};base64,${view.data}`"
              alt="工具返回图像"
            />
            <!-- deep=2：更深层默认折叠、点击展开（同 LLM 历史详情的可折叠约定） -->
            <div v-else-if="view.isJson" class="result-json-tree">
              <VueJsonPretty :data="view.jsonData" :deep="2" theme="dark" show-line />
            </div>
            <pre v-else class="result-block mono">{{ view.text }}</pre>
          </template>
          <p v-if="waitingAsyncResult" class="result-waiting">
            异步工具：以上为受理回执，等待事件回传…
          </p>
        </section>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { Search } from '@element-plus/icons-vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';
import { toolsApi } from '@/api';
import { useWebSocketStore } from '@/stores/websocket';
import VisionCapturePanel from '@/components/vision/VisionCapturePanel.vue';
import type {
  ToolCategoryView,
  ToolEntry,
  ToolHealth,
  ToolHealthEventData,
  ToolInvokeResult,
  ToolProviderUnit,
  WebSocketMessage,
} from '@/types';

// 分类元数据

const CATEGORY_META: Record<string, { label: string; description: string }> = {
  avatar: {
    label: '虚拟形象',
    description: 'VTubeStudio / VRChat / Warudo 等虚拟形象后端提供的工具',
  },
  studio: { label: '演播室', description: 'OBS 等演播室控制后端提供的工具' },
  vision: { label: '视觉', description: '屏幕感知能力（look_at_screen）' },
  memory: { label: '记忆', description: '观众事实与画像查询（query_memory / query_viewer_profile）' },
  mcp: { label: 'MCP', description: '外部 MCP server 提供的工具' },
  game: { label: '游戏 Agent', description: '游戏 Agent 自声明的工具（text_adv 等）' },
  framework: { label: '框架', description: '框架内置工具（AgentControl 等）' },
};

function categoryMeta(category: string) {
  return CATEGORY_META[category] ?? { label: category, description: '' };
}

// 数据加载

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

// 分类列表（左侧）

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
      p => p.switchable && (p.enabled ? p.registered === false : p.tool_count > 0),
    ).length,
);

// 提供者开关

const toggling = reactive(new Set<string>());

// 分类总开关（聚合操作：一键开/关全部提供者）

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
      ElMessage.success(`${next ? '启用' : '停用'} ${units.length} 个提供者，重启后生效`);
    }
  } finally {
    bulkToggling.value = false;
    await refreshAll();
  }
}

// 工具级停用

const toolToggling = reactive(new Set<string>());

async function onToolToggle(row: ToolEntry, next: boolean) {
  toolToggling.add(row.name);
  try {
    // 停用集合按注册表全名索引，必须传 full_name（裸名会被 apply_disabled 过滤丢弃）
    const response = await toolsApi.controlTool(row.full_name, next ? 'enable' : 'disable');
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

// 工具提供者手动重连
//
// 按 provider 维度防重：同一 Provider 下多行触发同一调用，按行名防重会出现
// loading 不同步；用 provider_id 做 Set 键，保证任意一行触发都共享 loading。
const reconnecting = reactive(new Set<string>());

function extractReconnectDetail(err: unknown): string {
  // axios 错误：后端 404/409 返回 {detail: "..."}，需要穿透 axios 默认 message
  const ax = err as { response?: { data?: { detail?: string } } };
  return ax?.response?.data?.detail ?? (err instanceof Error ? err.message : '重连失败');
}

async function onReconnect(providerId: string) {
  if (!providerId || reconnecting.has(providerId)) return;
  reconnecting.add(providerId);
  try {
    const resp = await toolsApi.reconnectProvider(providerId);
    const recoveredCount = resp.data.recovered.length;
    const stillTrippedCount = resp.data.still_tripped.length;
    const addedCount = resp.data.refreshed?.added.length ?? 0;
    if (stillTrippedCount > 0) {
      ElMessage.warning(
        `重连成功但 ${stillTrippedCount} 个工具探活未通过：${resp.data.still_tripped.join('、')}`,
      );
    } else if (addedCount > 0) {
      ElMessage.success(`重连成功，补注册 ${addedCount} 个工具（降级装配已恢复）`);
    } else if (recoveredCount > 0) {
      ElMessage.success(`已恢复 ${recoveredCount} 个工具`);
    } else {
      ElMessage.success('重连完成（当前无熔断工具）');
    }
    await refreshAll();
  } catch (e) {
    ElMessage.error(extractReconnectDetail(e));
  } finally {
    reconnecting.delete(providerId);
  }
}

// 工具过滤

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

// 抽屉详情

const detailOpen = ref(false);
const activeTool = ref<ToolEntry | null>(null);

const detailTitle = computed(() =>
  activeTool.value ? `工具详情 · ${activeTool.value.name}` : '工具详情',
);

function openDetail(row: ToolEntry) {
  activeTool.value = row;
  detailOpen.value = true;
  // 等 DOM 渲染后测描述是否溢出（决定"展开全文"入口是否显示）
  void nextTick(() => {
    descExpanded.value = false;
    measureDescClamp();
  });
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

// 调试调用（与 Agent 同路径经 registry 真实执行）
//
// formModel 形状由工具的 parameters 决定：default 预填，boolean 落 false，
// 数字型落 undefined（el-input-number 空态）、字符串落空串。
const invoking = ref(false);
const formModel = ref<Record<string, unknown>>({});
const invokeResult = ref<ToolInvokeResult | null>(null);
// async 工具受理后等待 WS tool.result 回传：true 期间监听 asyncEventName
const waitingAsyncResult = ref(false);
const asyncEventName = ref('');

const JSON_PARAM_PLACEHOLDER = 'JSON 对象/数组，如 {"k": 1}';

const isInternalTool = computed(() => {
  const cat = activeTool.value?.category;
  return cat === 'framework' || cat === 'game';
});

function resetInvokeState() {
  const next: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(activeTool.value?.parameters ?? {})) {
    if (spec.type === 'json') {
      // JSON 参数以文本承载：default 对象序列化预填，提交时解析回值
      next[key] =
        spec.default !== undefined && spec.default !== null
          ? JSON.stringify(spec.default, null, 2)
          : '';
    } else if (spec.default !== undefined && spec.default !== null) {
      next[key] = spec.default;
    } else if (spec.type === 'boolean') next[key] = false;
    else if (spec.type === 'integer' || spec.type === 'number') next[key] = undefined;
    else next[key] = '';
  }
  formModel.value = next;
  invokeResult.value = null;
  waitingAsyncResult.value = false;
  asyncEventName.value = '';
}

watch(activeTool, resetInvokeState);

// 工具描述折叠/展开：默认两行省略，溢出时点击或点"展开全文"看全文
const descRef = ref<HTMLElement | null>(null);
const descClamped = ref(false);
const descExpanded = ref(false);

function measureDescClamp(): void {
  // scrollHeight > clientHeight = 两行放不下、出现了省略
  descClamped.value = descRef.value
    ? descRef.value.scrollHeight > descRef.value.clientHeight + 1
    : false;
}

async function toggleDesc(): Promise<void> {
  if (!descClamped.value && !descExpanded.value) return;
  descExpanded.value = !descExpanded.value;
  if (!descExpanded.value) {
    // 收起后重测：窗口尺寸变化可能已不再溢出
    await nextTick();
    measureDescClamp();
  }
}

function extractInvokeDetail(err: unknown): string {
  // axios 错误：后端 400/404 返回 {detail: "..."}，穿透 axios 默认 message
  const ax = err as { response?: { data?: { detail?: string } } };
  return ax?.response?.data?.detail ?? (err instanceof Error ? err.message : '调用失败');
}

async function onInvoke() {
  const tool = activeTool.value;
  if (!tool || invoking.value) return;
  // 组装 arguments：json 参数解析文本为值（MCP 复杂参数），其余原样透传
  const args: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(tool.parameters ?? {})) {
    const raw = formModel.value[key];
    if (spec.type === 'json') {
      const text = typeof raw === 'string' ? raw.trim() : '';
      if (!text) {
        if (spec.required) {
          ElMessage.warning(`必填参数 ${key} 未填写`);
          return;
        }
        continue;
      }
      try {
        args[key] = JSON.parse(text);
      } catch {
        ElMessage.warning(`参数 ${key} 不是合法 JSON`);
        return;
      }
      continue;
    }
    if (spec.required && (raw === undefined || raw === null || raw === '')) {
      ElMessage.warning(`必填参数 ${key} 未填写`);
      return;
    }
    args[key] = raw;
  }
  invoking.value = true;
  try {
    const resp = await toolsApi.invoke(tool.full_name, args);
    invokeResult.value = resp.data;
    if (tool.kind === 'async') {
      waitingAsyncResult.value = true;
      asyncEventName.value = tool.result_event ?? `tool.result.${tool.full_name}`;
    }
  } catch (e) {
    ElMessage.error(extractInvokeDetail(e));
  } finally {
    invoking.value = false;
  }
}

// WS 回传（tool.result.<full_name>）覆盖受理回执（async 工具专用）
function handleToolResultMessage(msg: WebSocketMessage): void {
  const tool = activeTool.value;
  if (!tool || !waitingAsyncResult.value || msg.type !== asyncEventName.value) return;
  const d = msg.data;
  const name = typeof d.tool_name === 'string' ? d.tool_name : '';
  if (name && name !== tool.full_name && name !== tool.name) return;
  waitingAsyncResult.value = false;
  invokeResult.value = {
    success: d.status === 'success',
    content: '',
    blocks: [],
    error_message: typeof d.error_message === 'string' ? d.error_message : '',
    structured_content: d.result ?? null,
    duration_ms: 0,
    timestamp_ms: typeof d.timestamp_ms === 'number' ? d.timestamp_ms : 0,
  };
}

// 结果区视图合成
//
// ToolExecutionResult 的 content / blocks / structured_content 三处可能携带
// 同源内容（如 MCP mapper 把同一段 JSON 同时填进三处），按规范化形态去重；
// 能解析为 JSON 的文本走 vue-json-pretty 树渲染（同 LLM 历史详情页约定），
// 其余原样。
// vue-json-pretty data prop 的容许类型（包内 JSONDataType 的等价内联）
type JsonTreeData = string | number | boolean | unknown[] | Record<string, unknown> | null;

interface ResultView {
  kind: 'text' | 'image';
  /** JSON 视图的数据源（isJson 为 true 时有效） */
  jsonData: JsonTreeData;
  isJson: boolean;
  /** 非 JSON 文本原样内容 */
  text: string;
  /** 图像 base64 与 MIME（kind = 'image' 时有效） */
  data: string;
  mime: string;
}

function normalizeForResult(t: string): string {
  try {
    return JSON.stringify(JSON.parse(t));
  } catch {
    return t.trim();
  }
}

function tryParseJson(t: string): { ok: boolean; value: JsonTreeData } {
  try {
    return { ok: true, value: JSON.parse(t) as JsonTreeData };
  } catch {
    return { ok: false, value: null };
  }
}

const resultViews = computed<ResultView[]>(() => {
  const result = invokeResult.value;
  if (!result) return [];
  const views: ResultView[] = [];
  const seen = new Set<string>();
  const pushText = (raw: string) => {
    if (!raw || !raw.trim() || seen.has(normalizeForResult(raw))) return;
    seen.add(normalizeForResult(raw));
    const parsed = tryParseJson(raw);
    if (parsed.ok) {
      views.push({
        kind: 'text',
        jsonData: parsed.value,
        isJson: true,
        text: '',
        data: '',
        mime: '',
      });
    } else {
      views.push({ kind: 'text', jsonData: null, isJson: false, text: raw, data: '', mime: '' });
    }
  };
  pushText(result.content);
  for (const block of result.blocks) {
    if (block.kind === 'image' && block.data) {
      views.push({
        kind: 'image',
        jsonData: null,
        isJson: false,
        text: '',
        data: block.data,
        mime: block.mime_type,
      });
    } else {
      pushText(block.text);
    }
  }
  if (result.structured_content !== null && result.structured_content !== undefined) {
    pushText(JSON.stringify(result.structured_content));
  }
  return views;
});

watch(activeCategory, () => {
  searchQuery.value = '';
});

// 实时熔断状态（WS tool.health.*）

function applyHealthUpdate(toolName: string, next: ToolHealth | null): void {
  const target = tools.value.find(t => t.name === toolName);
  if (!target) return;
  target.health = next;
}

function isToolHealthEventData(data: unknown): data is ToolHealthEventData {
  if (!data || typeof data !== 'object') return false;
  const d = data as Record<string, unknown>;
  return (
    typeof d.tool_name === 'string' &&
    typeof d.state === 'string' &&
    typeof d.timestamp_ms === 'number'
  );
}

function handleHealthMessage(msg: WebSocketMessage): void {
  if (!msg.type.startsWith('tool.health.')) return;
  if (!isToolHealthEventData(msg.data)) return;
  const payload = msg.data;
  if (payload.state === 'open') {
    applyHealthUpdate(payload.tool_name, {
      state: 'tripped',
      failure_count: payload.failure_count,
      last_error: payload.last_error,
      tripped_at_ms: payload.timestamp_ms,
    });
  } else if (payload.state === 'closed') {
    applyHealthUpdate(payload.tool_name, null);
  }
}

function healthTooltip(row: ToolEntry): string {
  const h = row.health;
  if (!h) return '';
  // el-tooltip 默认按纯文本渲染，\n 不会换行，用分号分隔两段信息
  return `${h.last_error}；连续失败 ${h.failure_count} 次`;
}

const wsStore = useWebSocketStore();

onMounted(() => {
  wsStore.subscribe(handleHealthMessage);
  wsStore.subscribe(handleToolResultMessage);
  void refreshAll();
});

onBeforeUnmount(() => {
  wsStore.unsubscribe(handleHealthMessage);
  wsStore.unsubscribe(handleToolResultMessage);
});
</script>

<style scoped>
/* 页面布局：左 240 + 右 flex-1（与采集器 / Agent 页同构）          */
.tools-page {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: var(--spacing-md);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
  min-height: 640px;
}

/* LEFT：分类列表                                                */
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

/* RIGHT：详情面板                                               */
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

.cell-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 22px;
}

.health-cell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 22px;
}

.health-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
  opacity: 0.45;
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

.vision-panel-mount {
  flex-shrink: 0;
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

.provider-notice {
  margin: 0;
  padding: var(--spacing-xs) var(--spacing-md);
  font-size: 12px;
  color: var(--text-secondary);
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

/* 行可点击：hover 高亮整行，配合"详情 / 调试"按钮提示可进 */
.provider-table :deep(.el-table__row:hover > td) {
  background: var(--bg-hover) !important;
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

.detail-name {
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

/* Empty                                                         */
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

/* 抽屉                                                          */

/* 对话框主体：限高防纵向滚动——参数区超限时内部滚动，结果区吃满剩余高度 */
.detail-pane {
  display: flex;
  flex-direction: column;
  max-height: calc(90vh - 110px);
  overflow: auto;
}

/* 参数区：高度确定（内部滚动盒 280px 封顶），不参与 flex 压缩——
   压缩会导致 section 与内部滚动盒高度脱钩、视觉溢出叠到相邻区块上 */
.params-section {
  flex: 0 0 auto;
  margin-bottom: var(--spacing-sm);
}

.params-scroll {
  max-height: 280px;
  overflow: auto;
}

.detail-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-xs) var(--spacing-sm);
}

.detail-fullname {
  font-size: 11px;
  color: var(--text-placeholder);
}

.detail-section {
  margin-bottom: var(--spacing-md);
}

.detail-h {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 var(--spacing-xs);
}

.detail-result-event {
  font-size: 12px;
  color: var(--text-secondary);
  margin: var(--spacing-xs) 0 0;
}

.detail-desc {
  font-size: 13px;
  color: var(--text-regular);
  margin: var(--spacing-xs) 0 0;
  line-height: 1.5;
  /* 长描述折叠为两行；可点击展开全文 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.detail-desc.is-expanded {
  display: block;
  -webkit-line-clamp: unset;
}

.detail-desc.is-toggleable {
  cursor: pointer;
}

.desc-toggle {
  font-size: 12px;
  color: var(--color-primary, #409eff);
  cursor: pointer;
  user-select: none;
}

.detail-none {
  background: var(--bg-hover);
  border-radius: var(--radius-md);
}

.param-list {
  list-style: none;
  margin: 0;
  padding: 0;
  /* 双列网格：参数并排，压缩纵向占用 */
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--spacing-sm);
}

.param-item {
  background: var(--bg-hover);
  border-radius: var(--radius-md);
  padding: var(--spacing-xs) var(--spacing-sm);
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
  /* 描述单行省略，全文悬停可见（title） */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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

.param-input {
  margin-top: var(--spacing-xs);
}

.param-number {
  width: 100%;
}

.invoke-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-sm);
}

.invoke-hint {
  font-size: 12px;
  color: var(--color-warning, #e6a23c);
}

.result-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  margin-bottom: var(--spacing-xs);
}

.result-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.result-error {
  font-size: 12px;
  color: var(--color-danger, #f56c6c);
  margin: 0 0 var(--spacing-xs);
  line-height: 1.5;
  word-break: break-all;
}

.result-block {
  background: var(--bg-hover);
  border-radius: var(--radius-md);
  padding: var(--spacing-sm);
  font-size: 12px;
  line-height: 1.5;
  margin: 0 0 var(--spacing-xs);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow: auto;
}

.result-image {
  max-width: 100%;
  border-radius: var(--radius-md);
  margin-bottom: var(--spacing-xs);
}

/* 结果区：对话框内唯一滚动区——高度增长不推动对话框本身 */
/* 结果区：吃掉面板剩余高度（至少 180px）并在内部滚动，对话框高度不随之增长 */
.result-section {
  flex: 1 1 auto;
  min-height: 180px;
  overflow: auto;
}

/* JSON 树容器（vue-json-pretty，同 LLM 历史详情约定）；滚动交给结果区统一处理 */
.result-json-tree {
  background: #1e1e1e;
  border-radius: var(--radius-md);
  padding: var(--spacing-md);
  font-size: 13px;
  margin-bottom: var(--spacing-xs);
}

.result-waiting {
  font-size: 12px;
  color: var(--text-secondary);
  margin: 0;
}

/* Responsive                                                    */
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
