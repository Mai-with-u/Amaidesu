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
          <div class="sub-card-header-right">
            <!-- 容器自带的布尔 enabled 子字段提为卡头开关（如 streamer.background.enabled） -->
            <el-switch
              v-if="enabledLeaf(field)"
              :model-value="getValue(enabledLeaf(field)!.key) === true"
              size="small"
              active-text="启用"
              inactive-text="禁用"
              @click.stop
              @change="(v: boolean) => toggleSubEnabled(field, v)"
            />
            <el-badge
              v-if="getChildrenChangeCount(field) > 0"
              :value="getChildrenChangeCount(field)"
              type="warning"
              size="small"
            />
          </div>
        </div>
        <div v-if="field.description" class="sub-card-desc">{{ field.description }}</div>
        <div v-show="expanded.has(field.key)" class="sub-card-body">
          <SubFieldGroup
            :fields="bodyFields(field)"
            :get-value="getValue"
            :get-original="getOriginal"
            :update-value="updateValue"
            :get-change-count="getChangeCount"
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
  /**
   * 父级下发的「某字段 key 下的待保存变更条数」查询函数；
   * 用于子卡片徽标。未提供时徽标隐藏（保留向后兼容）。
   */
  getChangeCount?: (key: string) => number;
}>();

const expanded = ref<Set<string>>(new Set());

function initExpanded() {
  // 默认全部展开：与实体卡一致，内容即页面主体
  for (const f of props.fields) {
    if (f.children && f.children.length > 0) {
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

/** 容器字段自带的布尔 enabled 叶子（key 恰为 `<容器>.enabled`）；无则 undefined */
function enabledLeaf(field: ConfigFieldSchema): ConfigFieldSchema | undefined {
  return (field.children ?? []).find(
    c => !c.children?.length && c.type === 'boolean' && c.key === `${field.key}.enabled`,
  );
}

/** 卡体子字段：卡头开关已接管 enabled 叶子时不重复渲染 */
function bodyFields(field: ConfigFieldSchema): ConfigFieldSchema[] {
  const leaf = enabledLeaf(field);
  const children = field.children ?? [];
  return leaf ? children.filter(c => c !== leaf) : children;
}

function toggleSubEnabled(field: ConfigFieldSchema, val: boolean) {
  const leaf = enabledLeaf(field);
  if (leaf) props.updateValue(leaf, val);
}

/**
 * 子卡片徽标：sum 父级注入的 getChangeCount(child.key)——子项的 key 前缀即
 * 「子字段下的待保存变更条数」。父级未注入时徽标隐藏（v-if 自动消失）。
 */
function getChildrenChangeCount(field: ConfigFieldSchema): number {
  if (!props.getChangeCount || !field.children) return 0;
  let count = 0;
  for (const child of field.children) {
    count += props.getChangeCount(child.key);
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

.sub-card-desc {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  padding: 0 var(--spacing-md) var(--spacing-sm);
  margin-top: -4px;
}

.sub-card-header-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.sub-card-header-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.sub-card-toggle {
  font-size: 14px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.sub-card-icon {
  font-size: 16px;
  color: var(--color-primary);
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
