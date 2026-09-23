<template>
  <div class="component-card-list">
    <!-- 元数据字段（叶子与简单数组，如 enabled 名单、disabled_tools）：紧凑横排 -->
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

    <!-- 卡片列表：分类分组 / 对象数组卡 / 实体卡 -->
    <div class="card-list">
      <template v-for="card in entityCards" :key="card.key">
        <!-- 分类容器：子级全为容器（如 tools 的 studio / web 分类）→ 分组标题 + 递归卡列表 -->
        <div v-if="isCategoryGroup(card)" class="category-group">
          <div class="category-header">
            <span class="category-title">{{ card.label }}</span>
            <span v-if="card.description" class="category-desc">{{ card.description }}</span>
          </div>
          <ComponentCardList
            :fields="card.children || []"
            :enabled-field-key="null"
            :get-value="getValue"
            :get-original="getOriginal"
            :update-value="updateValue"
            :get-change-count="getChangeCount"
            :expand-command="expandCommand"
          />
        </div>

        <!-- 对象数组（llm_providers / llm_models 等）：整卡承载一等数组编辑器 -->
        <div v-else-if="isObjectArray(card)" class="array-card">
          <div class="array-card-header">
            <span class="card-title">{{ card.label }}</span>
            <el-badge
              v-if="getChangeCount && getChangeCount(card.key) > 0"
              :value="getChangeCount(card.key)"
              type="warning"
              size="small"
            />
          </div>
          <p v-if="card.description" class="card-desc">{{ card.description }}</p>
          <div class="array-card-body">
            <FieldRenderer
              :field="card"
              :model-value="getValue(card.key)"
              :original-value="getOriginal(card.key)"
              @update:model-value="updateValue(card, $event)"
            />
          </div>
        </div>

        <!-- 实体卡：容器字段（采集器 / Agent / 工具提供者 / 基础设施分段） -->
        <ComponentCard
          v-else
          :label="card.label"
          :description="card.description"
          :component-key="card.key"
          :component-fields="cardFields(card)"
          :enabled-list="enabledList"
          :boolean-enabled-key="booleanEnabledKey(card)"
          :has-list-semantics="enabledFieldKey != null"
          :get-value="getValue"
          :get-original="getOriginal"
          :update-value="updateValue"
          :get-change-count="getChangeCount"
          :on-toggle="handleToggle"
          :expand-command="expandCommand"
        />
      </template>
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
  /** 卡片级启用名单寻址（collectors.enabled / agents.agents.enabled）；null = 走卡片布尔 enabled 子字段 */
  enabledFieldKey: string | null;
  getValue: (key: string) => unknown;
  getOriginal: (key: string) => unknown;
  updateValue: (field: ConfigFieldSchema, value: unknown) => void;
  /** 透传给 ComponentCard → SubFieldGroup 的子卡片徽标查询函数 */
  getChangeCount?: (key: string) => number;
  /** 工具栏「全部展开/全部收起」命令，透传给实体卡 */
  expandCommand?: { action: 'expand' | 'collapse'; seq: number } | null;
}>();

function hasChildren(f: ConfigFieldSchema): boolean {
  return !!f.children && f.children.length > 0;
}

/** 对象数组是一等可编辑字段（元素子字段树在 items.fields），与容器同级成卡 */
function isObjectArray(f: ConfigFieldSchema): boolean {
  return f.type === 'array' && Array.isArray(f.items?.fields) && (f.items?.fields?.length ?? 0) > 0;
}

/** 分类容器：自身是容器且子级全是容器（实例卡集合），渲染为分组而不是单卡 */
function isCategoryGroup(f: ConfigFieldSchema): boolean {
  const children = f.children ?? [];
  return children.length > 0 && children.every(c => hasChildren(c));
}

// 实体卡 = 容器字段 + 对象数组字段；其余叶子与简单数组进紧凑元数据条
const entityCards = computed(() => props.fields.filter(f => hasChildren(f) || isObjectArray(f)));
const metaFields = computed(() => props.fields.filter(f => !hasChildren(f) && !isObjectArray(f)));

/** 卡片布尔开关寻址：无名单语义时，卡片若有布尔型 enabled 子字段则开关绑定之 */
function booleanEnabledKey(card: ConfigFieldSchema): string | null {
  if (props.enabledFieldKey) return null;
  const leaf = (card.children ?? []).find(
    c => !c.children?.length && c.type === 'boolean' && c.key === `${card.key}.enabled`,
  );
  return leaf ? leaf.key : null;
}

/** 卡体子字段：布尔开关已接管 enabled 子字段时不重复渲染 */
function cardFields(card: ConfigFieldSchema): ConfigFieldSchema[] {
  const enabledKey = booleanEnabledKey(card);
  const children = card.children ?? [];
  return enabledKey ? children.filter(c => c.key !== enabledKey) : children;
}

// 当前 enabled list（名单模式；可能为 undefined 或空数组）
const enabledList = computed(() => {
  if (!props.enabledFieldKey) return [];
  const val = props.getValue(props.enabledFieldKey);
  return Array.isArray(val) ? val : [];
});

function handleToggle(componentKey: string, newEnabled: boolean) {
  if (!props.enabledFieldKey) return;
  const leaf = componentKey.split('.').pop();
  if (!leaf) return;
  const current = new Set(enabledList.value);
  if (newEnabled) {
    current.add(leaf);
  } else {
    current.delete(leaf);
  }
  const field = findField(props.fields, props.enabledFieldKey);
  if (field) props.updateValue(field, Array.from(current));
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

/* 分类分组（studio / web 等实例集合）：占满整行，标题下递归卡网格 */
.category-group {
  grid-column: 1 / -1;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  background: var(--bg-elevated);
  padding: var(--spacing-md) var(--spacing-lg) var(--spacing-lg);
}

.category-header {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  margin-bottom: var(--spacing-md);
}

.category-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.category-desc {
  font-size: 12px;
  color: var(--text-secondary);
}

/* 对象数组卡（与实体卡观感一致） */
.array-card {
  border: 1px solid var(--border-color-light);
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-lg);
  background: var(--bg-card);
  overflow: hidden;
}

.array-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-md) var(--spacing-lg) 0;
}

.array-card-header .card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.array-card .card-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  padding: 0 var(--spacing-lg);
  margin-top: -2px;
}

.array-card-body {
  padding: var(--spacing-sm) var(--spacing-lg) var(--spacing-lg);
}

/* 卡头已渲染字段名与描述，内部 FieldRenderer 的同名标题不再重复 */
.array-card-body :deep(.field-header),
.array-card-body :deep(.field-description) {
  display: none;
}
</style>
