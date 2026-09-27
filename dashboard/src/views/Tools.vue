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
/**
 * 工具页 —— 提供者分类面板 + 工具清单 + 调试调用 + 视觉捕获面板
 *
 * 页面私有逻辑拆在 composables/tools/：目录加载与分类导航/过滤
 * （useToolCatalog）、开关与重连控制（useToolControls）、详情抽屉与
 * 调试调用（useToolDetail）、WS 熔断状态（useToolHealth）。
 * 本组件只做装配与初始加载。
 */
import { onMounted } from 'vue';
import { Search } from '@element-plus/icons-vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';
import VisionCapturePanel from '@/components/vision/VisionCapturePanel.vue';
import { useToolCatalog } from '@/composables/tools/useToolCatalog';
import { useToolControls } from '@/composables/tools/useToolControls';
import { useToolDetail } from '@/composables/tools/useToolDetail';
import { useToolHealth } from '@/composables/tools/useToolHealth';

// 装配：目录 → 控制（消费选中分类与刷新）→ 详情 → 熔断（消费工具清单）

const {
  tools,
  loading,
  error,
  refreshAll,
  activeCategory,
  categoryNav,
  totalToolCount,
  selectCategory,
  activeMeta,
  activeCategoryData,
  activeSwitchableCount,
  activeEnabledCount,
  pendingRestartCount,
  searchQuery,
  toolsOf,
  visibleProviders,
} = useToolCatalog();

const {
  toggling,
  bulkSwitchable,
  bulkState,
  bulkToggling,
  onBulkToggle,
  toolToggling,
  onToolToggle,
  onToggle,
  reconnecting,
  onReconnect,
} = useToolControls({ activeCategory, activeCategoryData, refreshAll });

const {
  detailOpen,
  activeTool,
  detailTitle,
  openDetail,
  rowClass,
  paramEntries,
  invoking,
  formModel,
  invokeResult,
  waitingAsyncResult,
  JSON_PARAM_PLACEHOLDER,
  isInternalTool,
  descRef,
  descClamped,
  descExpanded,
  toggleDesc,
  onInvoke,
  resultViews,
} = useToolDetail();

const { healthTooltip } = useToolHealth(tools);

onMounted(() => {
  void refreshAll();
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
