<template>
  <div class="settings-page">
    <!-- 页面头部 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">系统设置</h1>
        <p class="page-subtitle">配置管理</p>
      </div>
      <div class="header-actions">
        <el-badge :value="settingsStore.changeCount" :hidden="!settingsStore.hasChanges">
          <el-button
            type="primary"
            :loading="settingsStore.saving"
            :disabled="!settingsStore.hasChanges"
            @click="handleSave"
          >
            <el-icon><Check /></el-icon>
            保存更改
          </el-button>
        </el-badge>
        <el-button :disabled="!settingsStore.hasChanges" @click="handleDiscard">
          <el-icon><RefreshLeft /></el-icon>
          重置
        </el-button>
      </div>
    </header>

    <!-- 加载状态 -->
    <div v-if="settingsStore.loading" class="loading-container">
      <el-icon class="is-loading" :size="48"><Loading /></el-icon>
      <p>加载配置中...</p>
    </div>

    <!-- 错误状态 -->
    <el-alert
      v-else-if="settingsStore.error"
      type="error"
      :title="settingsStore.error"
      show-icon
      class="error-alert"
    />

    <!-- 主内容 -->
    <div v-else class="settings-content">
      <!-- 顶部栏：搜索 + Tab -->
      <div class="settings-toolbar">
        <el-input
          v-model="searchQuery"
          placeholder="搜索配置项..."
          :prefix-icon="Search"
          clearable
          size="default"
          class="search-input"
        />
      </div>

      <!-- 文件 Tab 导航 -->
      <el-tabs
        v-model="activeFileTab"
        class="file-tabs"
        :stretch="false"
        @tab-click="handleTabClick"
      >
        <el-tab-pane
          v-for="tab in FILE_TABS"
          :key="tab.key"
          :label="getTabLabel(tab)"
          :name="tab.key"
          lazy
        >
          <!-- 搜索模式：跨文件结果 -->
          <div v-if="searchQuery" class="search-results">
            <div
              v-for="match in globalSearchResults"
              :key="match.section.key"
              class="search-section"
            >
              <div class="search-section-header">
                <el-icon class="section-header-icon"
                  ><component :is="getIcon(match.section.icon)"
                /></el-icon>
                <span class="search-section-title">{{ match.section.label }}</span>
                <el-tag size="small" type="info">{{ tab.label }}</el-tag>
              </div>
              <div class="section-fields">
                <SubFieldGroup
                  :fields="match.fields"
                  :get-value="getFieldValue"
                  :get-original="getOriginalValue"
                  :update-value="updateFieldValue"
                />
              </div>
            </div>
            <el-empty
              v-if="globalSearchResults.length === 0"
              description="没有找到匹配的配置项"
              :image-size="80"
            />
          </div>

          <!-- 正常浏览模式：分区卡片 -->
          <div v-else class="section-cards">
            <div
              v-for="section in getFileSections(tab.key)"
              :key="section.key"
              class="section-card"
            >
              <div class="section-header">
                <div class="section-header-left">
                  <div class="section-icon-wrapper">
                    <el-icon class="section-icon"
                      ><component :is="getIcon(section.icon)"
                    /></el-icon>
                  </div>
                  <div class="section-title-area">
                    <h3 class="section-title">{{ section.label }}</h3>
                    <p v-if="section.description" class="section-desc">{{ section.description }}</p>
                  </div>
                </div>
                <el-badge
                  v-if="getSectionChangeCount(section.key) > 0"
                  :value="getSectionChangeCount(section.key)"
                  type="warning"
                />
              </div>
              <div class="section-fields">
                <ComponentCardList
                  v-if="['collectors', 'deciders', 'handlers'].includes(section.key)"
                  :fields="section.fields"
                  :enabled-field-key="`${section.key}.enabled`"
                  :get-value="getFieldValue"
                  :get-original="getOriginalValue"
                  :update-value="updateFieldValue"
                />
                <SubFieldGroup
                  v-else
                  :fields="section.fields"
                  :get-value="getFieldValue"
                  :get-original="getOriginalValue"
                  :update-value="updateFieldValue"
                />
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 重启确认对话框 -->
    <el-dialog
      v-model="showRestartDialog"
      title="重启服务"
      width="400px"
      :close-on-click-modal="false"
    >
      <el-alert type="warning" title="部分配置更改需要重启服务才能生效" show-icon :closable="false">
        <p>是否立即重启服务？</p>
        <p class="restart-warning">注意：重启期间服务将暂时不可用</p>
      </el-alert>

      <template #footer>
        <el-button @click="showRestartDialog = false">稍后重启</el-button>
        <el-button type="primary" :loading="restarting" @click="handleRestart">
          立即重启
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Check, RefreshLeft, Loading, Search } from '@element-plus/icons-vue';
import {
  Setting,
  User,
  Monitor,
  Document,
  ChatDotRound,
  Microphone,
  Headset,
  VideoCamera,
  Picture,
  Film,
  Notification,
  Cpu,
  DataAnalysis,
  Tools,
  Key,
  Connection,
  Management,
} from '@element-plus/icons-vue';
import { useSettingsStore } from '@/stores/settings';
import type { ConfigFieldSchema, ConfigGroupSchema, PendingChange } from '@/types/settings';
import SubFieldGroup from '@/components/settings/SubFieldGroup.vue';
import ComponentCardList from '@/components/settings/ComponentCardList.vue';

// ── 文件 Tab 定义 ──────────────────────────────────────────
const FILE_TABS = [
  {
    key: 'core.toml',
    label: '核心',
    icon: Monitor,
    desc: '通用 / 角色 / Dashboard / 日志',
    restart: true,
  },
  { key: 'model.toml', label: '模型', icon: Cpu, desc: 'LLM / VLM 模型配置', restart: true },
  {
    key: 'input.toml',
    label: '输入',
    icon: Microphone,
    desc: '弹幕 / 语音 / 控制台采集',
    restart: false,
  },
  {
    key: 'decision.toml',
    label: '决策',
    icon: ChatDotRound,
    desc: 'MaiBot / LLM 决策',
    restart: false,
  },
  { key: 'output.toml', label: '输出', icon: Film, desc: 'TTS / 字幕 / VTS / OBS', restart: false },
];

// 简化图标映射（按使用频率排序，只保留用到的）
const iconMap: Record<string, unknown> = {
  Setting,
  User,
  Monitor,
  Document,
  ChatDotRound,
  Microphone,
  Headset,
  VideoCamera,
  Picture,
  Film,
  Notification,
  Cpu,
  DataAnalysis,
  Tools,
  Key,
  Connection,
  Management,
};

// ── 状态 ──────────────────────────────────────────────────
const settingsStore = useSettingsStore();
const showRestartDialog = ref(false);
const restarting = ref(false);
const searchQuery = ref('');
const activeFileTab = ref('core.toml');

// 初始化：默认选中第一个非空 Tab
watch(
  () => settingsStore.groups,
  groups => {
    if (groups.length === 0) return;
    // 检查当前 tab 是否有内容，没有则跳到第一个有内容的
    const hasCurrent = getFileSections(activeFileTab.value).length > 0;
    if (!hasCurrent) {
      const firstNonEmpty = FILE_TABS.find(t => getFileSections(t.key).length > 0);
      if (firstNonEmpty) activeFileTab.value = firstNonEmpty.key;
    }
  },
  { immediate: true },
);

// 加载配置
onMounted(async () => {
  await settingsStore.fetchSchema();
});

// ── 计算属性 ──────────────────────────────────────────────
// 所有 group 按 file_name 分组
// 所有 group 按后端返回的 file_name 分组
const groupsByFile = computed(() => {
  const map = new Map<string, ConfigGroupSchema[]>();
  for (const group of settingsStore.groups) {
    const file = group.file_name;
    if (!file) continue; // 后端必须返回 file_name
    if (!map.has(file)) map.set(file, []);
    map.get(file)!.push(group);
  }
  return map;
});

// 获取某个文件下的 sections（排序后）
function getFileSections(fileName: string): ConfigGroupSchema[] {
  return (groupsByFile.value.get(fileName) || []).sort((a, b) => (a.order ?? 99) - (b.order ?? 99));
}

// 搜索模式：全局跨文件结果
const globalSearchResults = computed(() => {
  if (!searchQuery.value) return [];
  const q = searchQuery.value.toLowerCase();
  const results: { section: ConfigGroupSchema; fields: ConfigFieldSchema[]; file: string }[] = [];

  for (const group of settingsStore.groups) {
    const matched = group.fields.filter(
      f =>
        f.label.toLowerCase().includes(q) ||
        f.key.toLowerCase().includes(q) ||
        (f.description?.toLowerCase().includes(q) ?? false),
    );
    if (matched.length > 0) {
      results.push({ section: group, fields: matched, file: group.file_name ?? '' });
    }
  }
  return results;
});

// ── 图标 ──────────────────────────────────────────────────
function getIcon(iconName?: string) {
  if (!iconName) return Setting;
  return iconMap[iconName] || Setting;
}

// Tab 标签渲染
function getTabLabel(tab: (typeof FILE_TABS)[0]) {
  const hasChanges = getFileChangeCount(tab.key) > 0;
  const restartNeeded = tab.restart && hasChanges;
  return `${tab.label}${restartNeeded ? ' ⚠️' : ''}`;
}

// Tab 点击：搜索时清除搜索
function handleTabClick() {
  // 不做特殊处理，保持 tab 切换
}

// ── 变更统计 ──────────────────────────────────────────────
// 某个文件下的变更数
function getFileChangeCount(fileName: string): number {
  const sections = getFileSections(fileName);
  const keys = new Set(sections.flatMap(s => s.fields.map(f => f.key)));
  return settingsStore.pendingChanges.filter(c => keys.has(c.key)).length;
}

// 某个 section 的变更数
function getSectionChangeCount(sectionKey: string): number {
  return settingsStore.pendingChanges.filter(c => c.key.startsWith(sectionKey + '.')).length;
}

// ── 字段读写 ──────────────────────────────────────────────
function getFieldValue(key: string): unknown {
  const keys = key.split('.');
  let current: unknown = settingsStore.currentValues;
  for (const k of keys) {
    if (current && typeof current === 'object' && k in current) {
      current = (current as Record<string, unknown>)[k];
    } else {
      return undefined;
    }
  }
  return current;
}

function getOriginalValue(key: string): unknown {
  const keys = key.split('.');
  let current: unknown = settingsStore.originalValues;
  for (const k of keys) {
    if (current && typeof current === 'object' && k in current) {
      current = (current as Record<string, unknown>)[k];
    } else {
      return undefined;
    }
  }
  return current;
}

function updateFieldValue(field: ConfigFieldSchema, value: unknown) {
  const keys = field.key.split('.');
  const newValues = { ...settingsStore.currentValues };
  let current: Record<string, unknown> = newValues;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (!current[k] || typeof current[k] !== 'object') {
      current[k] = {};
    }
    current[k] = { ...(current[k] as Record<string, unknown>) };
    current = current[k] as Record<string, unknown>;
  }
  current[keys[keys.length - 1]] = value;
  settingsStore.updateCurrentValues(newValues);
  updatePendingChanges(field, value);
}

function updatePendingChanges(field: ConfigFieldSchema, newValue: unknown) {
  const oldValue = getOriginalValue(field.key);
  const existingIndex = settingsStore.pendingChanges.findIndex(c => c.key === field.key);
  if (JSON.stringify(newValue) === JSON.stringify(oldValue)) {
    if (existingIndex >= 0) {
      const newChanges = [...settingsStore.pendingChanges];
      newChanges.splice(existingIndex, 1);
      settingsStore.updatePendingChanges(newChanges);
    }
    return;
  }
  const change: PendingChange = {
    key: field.key,
    oldValue,
    newValue,
    field,
  };
  if (existingIndex >= 0) {
    const newChanges = [...settingsStore.pendingChanges];
    newChanges[existingIndex] = change;
    settingsStore.updatePendingChanges(newChanges);
  } else {
    settingsStore.updatePendingChanges([...settingsStore.pendingChanges, change]);
  }
}

// ── 保存 / 重置 / 重启 ──────────────────────────────────
async function handleSave() {
  if (!settingsStore.hasChanges) return;
  try {
    const result = await settingsStore.saveChanges();
    if (result.success) {
      ElMessage.success(result.message);
      if (result.requires_restart) {
        showRestartDialog.value = true;
      }
    } else {
      ElMessage.error(result.message);
    }
  } catch (error) {
    console.error('Save failed:', error);
    ElMessage.error('保存配置失败');
  }
}

async function handleDiscard() {
  if (!settingsStore.hasChanges) return;
  try {
    await ElMessageBox.confirm('确定要丢弃所有未保存的更改吗？', '确认丢弃', {
      confirmButtonText: '丢弃',
      cancelButtonText: '取消',
      type: 'warning',
    });
    settingsStore.discardChanges();
    ElMessage.info('已丢弃所有更改');
  } catch {
    /* 用户取消 */
  }
}

async function handleRestart() {
  restarting.value = true;
  try {
    const result = await settingsStore.restartService();
    if (result.success) {
      ElMessage.success('服务正在重启...');
      showRestartDialog.value = false;
    } else {
      ElMessage.error(result.message);
    }
  } catch (error) {
    console.error('Restart failed:', error);
    ElMessage.error('重启服务失败');
  } finally {
    restarting.value = false;
  }
}
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height) - var(--spacing-lg) * 2);
  overflow: hidden;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.page-title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--text-primary);
}

.page-subtitle {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
}

.header-actions {
  display: flex;
  gap: var(--spacing-sm);
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  color: var(--text-secondary);
}

.loading-container p {
  margin-top: var(--spacing-md);
}

.error-alert {
  flex-shrink: 0;
}

/* ── 主内容区 ──────────────────────────────────────────── */
.settings-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  box-shadow: var(--shadow-sm);
}

/* 顶部栏：搜索框 */
.settings-toolbar {
  flex-shrink: 0;
  padding: var(--spacing-md) var(--spacing-lg) 0;
}

.search-input {
  max-width: 360px;
}

/* ── File Tabs ──────────────────────────────────────────── */
.file-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 0 var(--spacing-lg);
}

.file-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: var(--spacing-sm) 0 0;
  flex-shrink: 0;
}

.file-tabs :deep(.el-tabs__nav-wrap) {
  padding-left: 0;
}

.file-tabs :deep(.el-tabs__item) {
  font-size: 14px;
  font-weight: 500;
  padding: 0 20px;
  height: 40px;
  line-height: 40px;
  transition: color var(--transition-fast);
}

.file-tabs :deep(.el-tabs__item:hover) {
  color: var(--color-primary);
}

.file-tabs :deep(.el-tabs__active-bar) {
  height: 2px;
  background: var(--color-primary);
}

/* Tab 内容区可滚动 */
.file-tabs :deep(.el-tabs__content) {
  flex: 1;
  overflow: hidden;
}

.file-tabs :deep(.el-tab-pane) {
  height: 100%;
  overflow-y: auto;
  padding: var(--spacing-md) 0;
}

/* ── 搜索模式结果 ──────────────────────────────────────── */
.search-results {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.search-section {
  background: var(--bg-elevated);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
}

.search-section-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color-light);
  font-size: 13px;
  color: var(--text-regular);
}

.section-header-icon {
  font-size: 16px;
  color: var(--color-primary);
}

.search-section-title {
  font-weight: 500;
  flex: 1;
}

/* ── 分区卡片列表 ──────────────────────────────────────── */
.section-cards {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.section-card {
  background: var(--bg-elevated);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  overflow: hidden;
  transition: box-shadow var(--transition-fast);
}

.section-card:hover {
  box-shadow: var(--shadow-md);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-md) var(--spacing-lg);
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color-light);
}

.section-header-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.section-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--color-primary-light-9);
  flex-shrink: 0;
}

.section-icon {
  font-size: 18px;
  color: var(--color-primary);
}

.section-title-area {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.section-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.section-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.section-fields {
  padding: var(--spacing-md) var(--spacing-lg);
}

/* ── 重启警告 ──────────────────────────────────────────── */
.restart-warning {
  margin-top: var(--spacing-sm);
  font-size: 12px;
  color: var(--color-warning);
}

/* ── 滚动条 ────────────────────────────────────────────── */
.file-tabs :deep(.el-tab-pane)::-webkit-scrollbar {
  width: 6px;
}

.file-tabs :deep(.el-tab-pane)::-webkit-scrollbar-track {
  background: transparent;
}

.file-tabs :deep(.el-tab-pane)::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.file-tabs :deep(.el-tab-pane)::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

/* ── 响应式 ────────────────────────────────────────────── */
@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-md);
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .settings-toolbar {
    padding: var(--spacing-sm) var(--spacing-md) 0;
  }

  .search-input {
    max-width: 100%;
  }

  .file-tabs {
    padding: 0 var(--spacing-md);
  }

  .file-tabs :deep(.el-tabs__item) {
    padding: 0 12px;
    font-size: 13px;
  }

  .section-header {
    padding: var(--spacing-sm) var(--spacing-md);
  }

  .section-fields {
    padding: var(--spacing-xs) var(--spacing-md);
  }
}
</style>
