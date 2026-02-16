<template>
  <div class="dynamic-form">
    <!-- 搜索框 -->
    <div class="form-search">
      <el-input v-model="searchQuery" placeholder="搜索配置项..." :prefix-icon="Search" clearable />
    </div>

    <!-- 分组列表 -->
    <div class="form-groups">
      <div v-for="group in filteredGroups" :key="group.key" class="form-group">
        <div class="group-header">
          <div class="group-title">
            <el-icon class="group-icon"><component :is="getIcon(group.icon)" /></el-icon>
            <span>{{ group.label }}</span>
          </div>
          <span v-if="group.description" class="group-description">{{ group.description }}</span>
        </div>

        <div class="group-fields">
          <FieldRenderer
            v-for="field in group.fields"
            :key="field.key"
            :field="field"
            :model-value="getFieldValue(field.key)"
            :original-value="getOriginalValue(field.key)"
            @update:model-value="updateFieldValue(field, $event)"
          />
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <el-empty
      v-if="filteredGroups.length === 0"
      description="没有找到匹配的配置项"
      :image-size="100"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import {
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
  Shop,
  ShoppingCart,
  Wallet,
  Money,
  Timer,
  Clock,
  Calendar,
  SetUp,
  SwitchButton,
  Management,
} from '@element-plus/icons-vue';
import type { ConfigGroupSchema, ConfigFieldSchema, PendingChange } from '@/types/settings';
import FieldRenderer from './FieldRenderer.vue';

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
  Shop,
  ShoppingCart,
  Wallet,
  Money,
  Timer,
  Clock,
  Calendar,
  SetUp,
  SwitchButton,
  Management,
};

const props = defineProps<{
  groups: ConfigGroupSchema[];
  modelValue: Record<string, unknown>;
  originalValues: Record<string, unknown>;
  pendingChanges: PendingChange[];
}>();

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>];
  'update:pendingChanges': [value: PendingChange[]];
}>();

// 搜索查询
const searchQuery = ref('');

// 过滤分组
const filteredGroups = computed(() => {
  if (!searchQuery.value) {
    return props.groups;
  }

  const query = searchQuery.value.toLowerCase();
  return props.groups
    .map((group) => {
      // 过滤匹配的字段
      const matchedFields = group.fields.filter(
        (field) =>
          field.label.toLowerCase().includes(query) ||
          field.key.toLowerCase().includes(query) ||
          (field.description?.toLowerCase().includes(query) ?? false),
      );

      if (matchedFields.length > 0) {
        return { ...group, fields: matchedFields };
      }
      return null;
    })
    .filter((g): g is ConfigGroupSchema => g !== null);
});

// 获取图标组件
function getIcon(iconName?: string) {
  if (!iconName) return Setting;
  return iconMap[iconName] || Setting;
}

// 获取字段值
function getFieldValue(key: string): unknown {
  const keys = key.split('.');
  let current: unknown = props.modelValue;

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
  let current: unknown = props.originalValues;

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
  const newValues = { ...props.modelValue };

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
  emit('update:modelValue', newValues);

  // 更新待保存变更
  updatePendingChanges(field, value);
}

// 更新待保存变更
function updatePendingChanges(field: ConfigFieldSchema, newValue: unknown) {
  const oldValue = getOriginalValue(field.key);
  const existingIndex = props.pendingChanges.findIndex((c) => c.key === field.key);

  // 如果新值等于原始值，移除变更记录
  if (JSON.stringify(newValue) === JSON.stringify(oldValue)) {
    if (existingIndex >= 0) {
      const newChanges = [...props.pendingChanges];
      newChanges.splice(existingIndex, 1);
      emit('update:pendingChanges', newChanges);
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
    const newChanges = [...props.pendingChanges];
    newChanges[existingIndex] = change;
    emit('update:pendingChanges', newChanges);
  } else {
    emit('update:pendingChanges', [...props.pendingChanges, change]);
  }
}
</script>

<style scoped>
.dynamic-form {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.form-search {
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color-light);
}

.form-groups {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-md);
}

.form-group {
  margin-bottom: var(--spacing-lg);
}

.group-header {
  margin-bottom: var(--spacing-md);
  padding-bottom: var(--spacing-sm);
  border-bottom: 1px solid var(--border-color-light);
}

.group-title {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.group-icon {
  color: var(--color-primary);
}

.group-description {
  display: block;
  margin-top: var(--spacing-xs);
  font-size: 13px;
  color: var(--text-secondary);
}

.group-fields {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

/* 滚动条样式 */
.form-groups::-webkit-scrollbar {
  width: 8px;
}

.form-groups::-webkit-scrollbar-track {
  background: var(--bg-elevated);
}

.form-groups::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 4px;
}

.form-groups::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}
</style>
