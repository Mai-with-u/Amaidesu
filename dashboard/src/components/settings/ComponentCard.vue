<template>
  <div class="component-card" :class="{ 'is-disabled': !enabled }">
    <!-- 卡片头部：图标 + 名称 + 开关 -->
    <div class="card-header" @click="toggleExpand">
      <div class="card-header-left">
        <el-icon class="card-toggle">
          <ArrowRight v-if="!expanded" />
          <ArrowDown v-else />
        </el-icon>
        <el-icon class="card-icon"><Coin /></el-icon>
        <span class="card-title">{{ label }}</span>
      </div>
      <div class="card-header-right">
        <el-switch
          :model-value="enabled"
          size="small"
          active-text="启用"
          inactive-text="禁用"
          @click.stop
          @change="toggleEnabled"
        />
        <el-badge v-if="changeCount > 0" :value="changeCount" type="warning" size="small" />
      </div>
    </div>

    <!-- 卡片体：展开时显示子字段，始终可展开（禁用时也可查看配置） -->
    <div v-if="componentFields.length > 0 && expanded" class="card-body">
      <SubFieldGroup
        :fields="componentFields"
        :get-value="getValue"
        :get-original="getOriginal"
        :update-value="updateValue"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';
import { ArrowRight, ArrowDown, Coin } from '@element-plus/icons-vue';
import type { ConfigFieldSchema } from '@/types/settings';
import SubFieldGroup from './SubFieldGroup.vue';

const props = defineProps<{
  label: string;
  componentKey: string;
  enabledList: string[];
  componentFields: ConfigFieldSchema[];
  getValue: (key: string) => unknown;
  getOriginal: (key: string) => unknown;
  updateValue: (field: ConfigFieldSchema, value: unknown) => void;
  onToggle: (name: string, newEnabled: boolean) => void;
}>();

const expanded = ref(true);

const compName = computed(() => props.componentKey.split('.').pop() || '');
const enabled = computed(() => props.enabledList.includes(compName.value));

const changeCount = computed(() => {
  let count = 0;
  const seen = new Set<string>();
  function walk(fields: ConfigFieldSchema[]) {
    for (const f of fields) {
      if (f.children) {
        walk(f.children);
        continue;
      }
      if (seen.has(f.key)) continue;
      seen.add(f.key);
      if (JSON.stringify(props.getValue(f.key)) !== JSON.stringify(props.getOriginal(f.key)))
        count++;
    }
  }
  walk(props.componentFields);
  return count;
});

function toggleExpand() {
  expanded.value = !expanded.value;
}

function toggleEnabled(val: boolean) {
  props.onToggle(props.componentKey, val);
}
</script>

<style scoped>
.component-card {
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--bg-card);
  transition:
    box-shadow var(--transition-fast),
    border-color var(--transition-fast);
}

.component-card:hover {
  box-shadow: var(--shadow-sm);
}

.component-card:not(.is-disabled) {
  border-left: 3px solid var(--color-primary);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-md) var(--spacing-lg);
  cursor: pointer;
  user-select: none;
  transition: background var(--transition-fast);
}

.card-header:hover {
  background: var(--bg-hover);
}

.card-header-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-width: 0;
}

.card-toggle {
  font-size: 14px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.card-icon {
  font-size: 18px;
  color: var(--color-primary);
  flex-shrink: 0;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.card-header-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.card-body {
  border-top: 1px solid var(--border-color-light);
  padding: var(--spacing-md) var(--spacing-lg);
  background: var(--bg-elevated);
  max-height: 400px;
  overflow-y: auto;
}

.card-body :deep(.sub-card) {
  border-color: var(--border-color-light);
}

.card-body :deep(.sub-card-header) {
  padding: var(--spacing-xs) var(--spacing-sm);
}

.card-body :deep(.sub-card-body) {
  padding: var(--spacing-xs) var(--spacing-sm) var(--spacing-sm);
}

.card-body :deep(.field-card) {
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border-color-light);
  border-radius: 0;
  padding: var(--spacing-xs) 0;
}

.card-body :deep(.field-card:last-child) {
  border-bottom: none;
}

.card-body :deep(.field-card:hover) {
  box-shadow: none;
}

.card-body :deep(.field-renderer) {
  border: none;
  padding: var(--spacing-xs) 0;
  background: transparent;
  border-radius: 0;
  border-bottom: 1px solid var(--border-color-light);
}

.card-body :deep(.field-renderer:last-child) {
  border-bottom: none;
}

.card-body :deep(.field-label) {
  font-size: 13px;
}
</style>
