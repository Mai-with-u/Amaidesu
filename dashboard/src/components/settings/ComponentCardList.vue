<template>
  <div class="component-card-list">
    <!-- 元数据字段（concurrent_rendering, error_handling, render_timeout_ms 等） -->
    <div v-if="metaFields.length > 0" class="meta-fields">
      <FieldRenderer
        v-for="field in metaFields"
        :key="field.key"
        :field="field"
        :model-value="getValue(field.key)"
        :original-value="getOriginal(field.key)"
        @update:model-value="(v: unknown) => updateValue(field, v)"
      />
    </div>

    <!-- 组件卡片列表 -->
    <div class="card-list">
      <ComponentCard
        v-for="card in componentCards"
        :key="card.key"
        :label="card.label"
        :component-key="card.key"
        :component-fields="card.children || []"
        :enabled-list="enabledList"
        :get-value="getValue"
        :get-original="getOriginal"
        :update-value="updateValue"
        :on-toggle="handleToggle"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { ConfigFieldSchema } from '@/types/settings';
import FieldRenderer from './FieldRenderer.vue';
import ComponentCard from './ComponentCard.vue';

const props = defineProps<{
  fields: ConfigFieldSchema[];
  enabledFieldKey: string;
  getValue: (key: string) => unknown;
  getOriginal: (key: string) => unknown;
  updateValue: (field: ConfigFieldSchema, value: unknown) => void;
}>();

function emitValue(key: string, value: unknown) {
  // 模拟 updateValue 调用：从 store 用 field 对象调用
  const field = findField(props.fields, key);
  if (field) props.updateValue(field, value);
}

function findField(fields: ConfigFieldSchema[], key: string): ConfigFieldSchema | undefined {
  for (const f of fields) {
    if (f.key === key) return f;
    if (f.children) {
      const found = findField(f.children, key);
      if (found) return found;
    }
  }
  return undefined;
}

// 元数据字段排除列表（这些 field 不作为组件卡片）
const META_KEYS = new Set([
  'enabled',
  'concurrent_rendering',
  'error_handling',
  'render_timeout_ms',
]);

// 提取元数据字段：顶层非组件的裸字段（enabled 由卡片开关控制，不展示）
const metaFields = computed(() => {
  return props.fields.filter(f => {
    if (f.children) return false;
    const leaf = f.key.split('.').pop() || '';
    if (META_KEYS.has(leaf)) return false;
    return true;
  });
});

// 识别组件卡片：有 children 且 key 最后一段不在 META_KEYS 中
const componentCards = computed(() => {
  return props.fields.filter(f => {
    if (!f.children || f.children.length === 0) return false;
    const leaf = f.key.split('.').pop() || '';
    return !META_KEYS.has(leaf);
  });
});

// 当前 enabled list（可能为 undefined 或空数组）
const enabledList = computed(() => {
  const val = props.getValue(props.enabledFieldKey);
  return Array.isArray(val) ? val : [];
});

function handleToggle(componentKey: string, newEnabled: boolean) {
  const leaf = componentKey.split('.').pop();
  if (!leaf) return;
  const current = new Set(enabledList.value);
  if (newEnabled) {
    current.add(leaf);
  } else {
    current.delete(leaf);
  }
  emitValue(props.enabledFieldKey, Array.from(current));
}
</script>

<style scoped>
.component-card-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

/* 元数据字段用紧凑横排 */
.meta-fields {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
}

.meta-fields :deep(.field-renderer) {
  border: none;
  padding: var(--spacing-xs) var(--spacing-sm);
  min-width: 180px;
  flex: 1 1 auto;
  background: transparent;
}

.meta-fields :deep(.field-label) {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
}

.meta-fields :deep(.field-description) {
  display: none;
}

.meta-fields :deep(.el-switch) {
  --el-switch-on-color: var(--color-primary);
}

.meta-fields :deep(.el-input-number) {
  width: 120px;
}

.meta-fields :deep(.el-select) {
  width: 140px;
}

.card-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: var(--spacing-md);
}
</style>
