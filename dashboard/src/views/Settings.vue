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

    <!-- 主内容：左侧导航 + 右侧内容 -->
    <div v-else class="settings-content">
      <!-- 左侧分组导航 -->
      <nav class="settings-nav">
        <!-- 搜索框 -->
        <div class="nav-search">
          <el-input
            v-model="searchQuery"
            placeholder="搜索配置项..."
            :prefix-icon="Search"
            clearable
            size="small"
          />
        </div>

        <!-- 分组列表 -->
        <div class="nav-groups">
          <button
            v-for="group in filteredGroups"
            :key="group.key"
            :class="['nav-item', { active: activeGroup === group.key }]"
            @click="activeGroup = group.key"
          >
            <el-icon class="nav-icon"><component :is="getIcon(group.icon)" /></el-icon>
            <span class="nav-label">{{ group.label }}</span>
            <el-badge
              v-if="getGroupChangeCount(group.key) > 0"
              :value="getGroupChangeCount(group.key)"
              type="warning"
              class="nav-badge"
            />
          </button>
        </div>
      </nav>

      <!-- 右侧配置内容 -->
      <div class="settings-main">
        <div class="main-header">
          <div class="main-title">
            <h2>{{ currentGroup?.label }}</h2>
            <p v-if="currentGroup?.description">{{ currentGroup.description }}</p>
          </div>
        </div>

        <div class="main-content">
          <!-- 当前分组的字段列表 -->
          <div v-if="currentGroupFields.length > 0" class="fields-list">
            <FieldRenderer
              v-for="field in currentGroupFields"
              :key="field.key"
              :field="field"
              :model-value="getFieldValue(field.key)"
              :original-value="getOriginalValue(field.key)"
              @update:model-value="updateFieldValue(field, $event)"
            />
          </div>

          <!-- 空状态 -->
          <el-empty v-else-if="searchQuery" description="没有找到匹配的配置项" :image-size="100" />
        </div>
      </div>
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
import {
  Check,
  RefreshLeft,
  Loading,
  Search,
  // 常用图标（用于配置分组动态显示）
  Setting,
  User,
  Connection,
  Monitor,
  Document,
  ChatDotRound,
  Microphone,
  Headset,
  VideoCamera,
  Picture,
  Film,
  Notification,
  Bell,
  Message,
  ChatLineSquare,
  Cpu,
  DataAnalysis,
  Grid,
  List,
  Tools,
  Key,
  Lock,
  Unlock,
  Link,
  House,
  OfficeBuilding,
  School,
  ShoppingCart,
  Wallet,
  Money,
  Timer,
  Clock,
  Calendar,
  Sunrise,
  Sunny,
  Moon,
  Cloudy,
  Compass,
  Location,
  MapLocation,
  Guide,
  Aim,
  Pointer,
  Position,
  FullScreen,
  Expand,
  Fold,
  Rank,
  Share,
  Upload,
  Download,
  Top,
  Bottom,
  Back as Left,
  Right,
  Plus,
  Minus,
  Close,
  Check as CheckIcon,
  Delete,
  Edit,
  Refresh,
  RefreshRight,
  Search as SearchIcon,
  Filter,
  Sort,
  More,
  MoreFilled,
  Operation,
  Switch,
  TurnOff,
  Open,
  View,
  Hide,
  Star,
  StarFilled,
  Collection,
  CollectionTag,
  Folder,
  FolderOpened,
  Files,
  DocumentCopy,
  DocumentChecked,
  DocumentAdd,
  DocumentRemove,
  Tickets,
  Postcard,
  Notebook,
  Reading,
  Memo,
  Menu,
  Histogram,
  PieChart,
  DataLine,
  TrendCharts,
  DataBoard,
  SetUp,
  SwitchButton,
  Management,
  Promotion,
  Platform,
  Opportunity as Opportunities,
  Medal,
  Trophy,
  Present,
  Box,
  Goods,
  GoodsFilled,
  ShoppingCartFull,
  Handbag,
  Shop as ShopIcon,
  TakeawayBox,
  Van,
  Bicycle,
  Ship,
  Cherry,
  Apple,
  Orange,
  Pear,
  Watermelon,
  Coffee,
  CoffeeCup,
  MilkTea,
  Goblet,
  GobletFull,
  GobletSquare,
  GobletSquareFull,
  IceCream,
  IceCreamRound,
  IceCreamSquare,
  Lollipop,
} from '@element-plus/icons-vue';
import { useSettingsStore } from '@/stores/settings';
import type { ConfigFieldSchema, ConfigGroupSchema, PendingChange } from '@/types/settings';
import FieldRenderer from '@/components/settings/FieldRenderer.vue';

// 图标映射表
const iconMap: Record<string, unknown> = {
  Setting,
  User,
  Connection,
  Monitor,
  Document,
  ChatDotRound,
  Microphone,
  Headset,
  VideoCamera,
  Picture,
  Film,
  Notification,
  Bell,
  Message,
  ChatLineSquare,
  Cpu,
  DataAnalysis,
  Grid,
  List,
  Tools,
  Key,
  Lock,
  Unlock,
  Link,
  House,
  OfficeBuilding,
  School,
  ShoppingCart,
  Wallet,
  Money,
  Timer,
  Clock,
  Calendar,
  Sunrise,
  Sunny,
  Moon,
  Cloudy,
  Compass,
  Location,
  MapLocation,
  Guide,
  Aim,
  Pointer,
  Position,
  FullScreen,
  Expand,
  Fold,
  Rank,
  Share,
  Upload,
  Download,
  Top,
  Bottom,
  Left,
  Right,
  Plus,
  Minus,
  Close,
  Check: CheckIcon,
  Delete,
  Edit,
  Refresh,
  RefreshRight,
  Search: SearchIcon,
  Filter,
  Sort,
  More,
  MoreFilled,
  Operation,
  Switch,
  TurnOff,
  Open,
  View,
  Hide,
  Star,
  StarFilled,
  Collection,
  CollectionTag,
  Folder,
  FolderOpened,
  Files,
  DocumentCopy,
  DocumentChecked,
  DocumentAdd,
  DocumentRemove,
  Tickets,
  Postcard,
  Notebook,
  Reading,
  Memo,
  Menu,
  Histogram,
  PieChart,
  DataLine,
  TrendCharts,
  DataBoard,
  SetUp,
  SwitchButton,
  Management,
  Promotion,
  Platform,
  Opportunities,
  Medal,
  Trophy,
  Present,
  Box,
  Goods,
  GoodsFilled,
  ShoppingCartFull,
  Handbag,
  Shop: ShopIcon,
  TakeawayBox,
  Van,
  Bicycle,
  Ship,
  Cherry,
  Apple,
  Orange,
  Pear,
  Watermelon,
  Coffee,
  CoffeeCup,
  MilkTea,
  Goblet,
  GobletFull,
  GobletSquare,
  GobletSquareFull,
  IceCream,
  IceCreamRound,
  IceCreamSquare,
  Lollipop,
};

const settingsStore = useSettingsStore();
const showRestartDialog = ref(false);
const restarting = ref(false);
const searchQuery = ref('');
const activeGroup = ref('');

// 初始化：设置默认分组
watch(
  () => settingsStore.groups,
  (groups) => {
    if (groups.length > 0 && !activeGroup.value) {
      activeGroup.value = groups[0].key;
    }
  },
  { immediate: true },
);

// 加载配置
onMounted(async () => {
  await settingsStore.fetchSchema();
});

// 过滤分组（搜索时）
const filteredGroups = computed(() => {
  if (!searchQuery.value) {
    return settingsStore.groups;
  }

  const query = searchQuery.value.toLowerCase();
  return settingsStore.groups.filter((group) => {
    // 检查分组标题或描述是否匹配
    if (
      group.label.toLowerCase().includes(query) ||
      (group.description?.toLowerCase().includes(query) ?? false)
    ) {
      return true;
    }
    // 检查字段是否匹配
    return group.fields.some(
      (field) =>
        field.label.toLowerCase().includes(query) ||
        field.key.toLowerCase().includes(query) ||
        (field.description?.toLowerCase().includes(query) ?? false),
    );
  });
});

// 当前分组
const currentGroup = computed((): ConfigGroupSchema | undefined => {
  return filteredGroups.value.find((g) => g.key === activeGroup.value);
});

// 当前分组的字段（搜索时过滤）
const currentGroupFields = computed((): ConfigFieldSchema[] => {
  const group = currentGroup.value;
  if (!group) return [];

  if (!searchQuery.value) {
    return group.fields;
  }

  const query = searchQuery.value.toLowerCase();
  return group.fields.filter(
    (field) =>
      field.label.toLowerCase().includes(query) ||
      field.key.toLowerCase().includes(query) ||
      (field.description?.toLowerCase().includes(query) ?? false),
  );
});

// 获取图标组件
function getIcon(iconName?: string) {
  if (!iconName) return Setting;
  return iconMap[iconName] || Setting;
}

// 获取分组的变更数量
function getGroupChangeCount(groupKey: string): number {
  return settingsStore.pendingChanges.filter((change) => change.key.startsWith(groupKey + '.'))
    .length;
}

// 获取字段值
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

// 获取原始值
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

// 更新字段值
function updateFieldValue(field: ConfigFieldSchema, value: unknown) {
  const keys = field.key.split('.');
  const newValues = { ...settingsStore.currentValues };

  // 嵌套更新
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

  // 更新待保存变更
  updatePendingChanges(field, value);
}

// 更新待保存变更
function updatePendingChanges(field: ConfigFieldSchema, newValue: unknown) {
  const oldValue = getOriginalValue(field.key);
  const existingIndex = settingsStore.pendingChanges.findIndex((c) => c.key === field.key);

  // 如果新值等于原始值，移除变更记录
  if (JSON.stringify(newValue) === JSON.stringify(oldValue)) {
    if (existingIndex >= 0) {
      const newChanges = [...settingsStore.pendingChanges];
      newChanges.splice(existingIndex, 1);
      settingsStore.updatePendingChanges(newChanges);
    }
    return;
  }

  // 添加或更新变更记录
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

// 保存更改
async function handleSave() {
  if (!settingsStore.hasChanges) {
    return;
  }

  try {
    const result = await settingsStore.saveChanges();

    if (result.success) {
      ElMessage.success(result.message);

      // 如果需要重启，显示重启对话框
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

// 丢弃更改
async function handleDiscard() {
  if (!settingsStore.hasChanges) {
    return;
  }

  try {
    await ElMessageBox.confirm('确定要丢弃所有未保存的更改吗？', '确认丢弃', {
      confirmButtonText: '丢弃',
      cancelButtonText: '取消',
      type: 'warning',
    });
    settingsStore.discardChanges();
    ElMessage.info('已丢弃所有更改');
  } catch {
    // 用户取消
  }
}

// 重启服务
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

/* 主内容布局 */
.settings-content {
  flex: 1;
  display: flex;
  gap: var(--spacing-md);
  overflow: hidden;
}

/* 左侧导航 */
.settings-nav {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.nav-search {
  padding: var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
}

.nav-groups {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-sm);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  width: 100%;
  padding: var(--spacing-sm) var(--spacing-md);
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  color: var(--text-regular);
  text-align: left;
  transition: all var(--transition-fast);
  border-radius: var(--radius-md);
  margin-bottom: 2px;
}

.nav-item:hover {
  background: var(--bg-hover);
  color: var(--color-primary);
}

.nav-item.active {
  background: var(--color-primary);
  color: var(--text-inverse);
  font-weight: 500;
}

.nav-icon {
  font-size: 18px;
  flex-shrink: 0;
}

.nav-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-badge {
  flex-shrink: 0;
}

/* 右侧主内容 */
.settings-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.main-header {
  padding: var(--spacing-lg);
  border-bottom: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

.main-title h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.main-title p {
  margin: var(--spacing-xs) 0 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.main-content {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-md);
}

.fields-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

/* 滚动条样式 */
.nav-groups::-webkit-scrollbar,
.main-content::-webkit-scrollbar {
  width: 6px;
}

.nav-groups::-webkit-scrollbar-track,
.main-content::-webkit-scrollbar-track {
  background: transparent;
}

.nav-groups::-webkit-scrollbar-thumb,
.main-content::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.nav-groups::-webkit-scrollbar-thumb:hover,
.main-content::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

.restart-warning {
  margin-top: var(--spacing-sm);
  font-size: 12px;
  color: var(--color-warning);
}

/* 响应式 */
@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-md);
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .settings-content {
    flex-direction: column;
  }

  .settings-nav {
    width: 100%;
    max-height: 200px;
  }

  .nav-groups {
    flex-direction: row;
    flex-wrap: wrap;
    gap: var(--spacing-xs);
  }

  .nav-item {
    flex-shrink: 0;
    width: auto;
  }
}
</style>
