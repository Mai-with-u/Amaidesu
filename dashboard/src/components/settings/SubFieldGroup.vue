<template>
  <div class="sub-field-group">
    <template v-for="field in fields" :key="field.key">
      <!-- 有子字段 → 增强版子卡片（accent bar + 卡片化） -->
      <div v-if="field.children && field.children.length > 0" class="sub-card">
        <div class="sub-card-header" @click="toggleCard(field.key)">
          <div class="sub-card-header-left">
            <el-icon class="sub-card-toggle">
              <ArrowRight v-if="!expanded.has(field.key)" />
              <ArrowDown v-else />
            </el-icon>
            <el-icon class="sub-card-icon"><FolderOpened /></el-icon>
            <span class="sub-card-title">{{ field.label || field.key }}</span>
          </div>
          <el-badge
            v-if="getChildrenChangeCount(field) > 0"
            :value="getChildrenChangeCount(field)"
            type="warning"
            size="small"
          />
        </div>
        <div v-show="expanded.has(field.key)" class="sub-card-body">
          <SubFieldGroup
            :fields="field.children"
            :get-value="getValue"
            :get-original="getOriginal"
            :update-value="updateValue"
          />
        </div>
      </div>

      <!-- 叶子字段 → 卡片化渲染（grid 布局） -->
      <div v-else class="field-card">
        <FieldRenderer
          :field="field"
          :model-value="getValue(field.key)"
          :original-value="getOriginal(field.key)"
          @update:model-value="updateValue(field, $event)"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { ArrowRight, ArrowDown, FolderOpened } from '@element-plus/icons-vue';
import type { ConfigFieldSchema } from '@/types/settings';
import FieldRenderer from './FieldRenderer.vue';

const props = defineProps<{
  fields: ConfigFieldSchema[];
  getValue: (key: string) => unknown;
  getOriginal: (key: string) => unknown;
  updateValue: (field: ConfigFieldSchema, value: unknown) => void;
}>();

const expanded = ref<Set<string>>(new Set());

function initExpanded() {
  for (const f of props.fields) {
    if (f.children && f.children.length <= 3 && f.children.length > 0) {
      expanded.value.add(f.key);
    }
  }
}
initExpanded();

function toggleCard(key: string) {
  if (expanded.value.has(key)) {
    expanded.value.delete(key);
  } else {
    expanded.value.add(key);
  }
}

function getChildrenChangeCount(field: ConfigFieldSchema): number {
  if (!field.children) return 0;
  let count = 0;
  for (const child of field.children) {
    if (child.children && child.children.length > 0) {
      count += getChildrenChangeCount(child);
    }
  }
  return count;
}
</script>

<style scoped>
.sub-field-group {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

/* ── 叶子字段卡片（grid 布局） ── */
.field-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-sm) var(--spacing-md);
  transition:
    box-shadow var(--transition-fast),
    border-color var(--transition-fast);
}

.field-card:hover {
  box-shadow: var(--shadow-sm);
}

.field-card :deep(.field-renderer) {
  border: none;
  padding: 0;
  background: transparent;
  border-radius: 0;
}

.field-card :deep(.field-label) {
  font-size: 13px;
  font-weight: 500;
}

.field-card :deep(.field-description) {
  font-size: 11px;
  margin-bottom: var(--spacing-xs);
}

/* ── 子卡片（accent bar + 卡片化） ── */
.sub-card {
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--bg-card);
  transition:
    box-shadow var(--transition-fast),
    border-color var(--transition-fast);
}

.sub-card:hover {
  box-shadow: var(--shadow-sm);
}

.sub-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-sm) var(--spacing-md);
  cursor: pointer;
  user-select: none;
  transition: background var(--transition-fast);
}

.sub-card-header:hover {
  background: var(--bg-hover);
}

.sub-card-header-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.sub-card-toggle {
  font-size: 14px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.sub-card-icon {
  font-size: 16px;
  color: var(--color-primary);
  flex-shrink: 0;
}

.sub-card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.sub-card-body {
  padding: var(--spacing-sm) var(--spacing-md) var(--spacing-md);
  border-top: 1px solid var(--border-color-light);
  background: var(--bg-elevated);
  max-height: 360px;
  overflow-y: auto;
}

/* 子卡片内部的 field-card 调整 */
.sub-card-body :deep(.field-card) {
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border-color-light);
  border-radius: 0;
  padding: var(--spacing-xs) 0;
}

.sub-card-body :deep(.field-card:last-child) {
  border-bottom: none;
}

.sub-card-body :deep(.field-card:hover) {
  box-shadow: none;
}
</style>
