<template>
  <div class="array-editor">
    <div class="array-items">
      <div v-for="(_, index) in items" :key="index" class="array-item">
        <!-- 简单类型数组项 -->
        <template v-if="!itemSchema || itemSchema.type === 'string'">
          <el-input
            v-model="items[index]"
            size="small"
            :placeholder="String(itemSchema?.default || '值')"
            @input="handleUpdate"
          />
        </template>

        <template v-else-if="itemSchema.type === 'integer'">
          <el-input-number
            v-model="items[index]"
            size="small"
            :min="itemSchema.validation?.min"
            :max="itemSchema.validation?.max"
            controls-position="right"
            @change="handleUpdate"
          />
        </template>

        <template v-else-if="itemSchema.type === 'float'">
          <el-input-number
            v-model="items[index]"
            size="small"
            :min="itemSchema.validation?.min"
            :max="itemSchema.validation?.max"
            :step="0.1"
            :precision="2"
            controls-position="right"
            @change="handleUpdate"
          />
        </template>

        <template v-else-if="itemSchema.type === 'boolean'">
          <el-switch v-model="items[index]" @change="handleUpdate" />
        </template>

        <template v-else-if="itemSchema.type === 'select'">
          <el-select v-model="items[index]" size="small" @change="handleUpdate">
            <el-option
              v-for="option in itemOptions"
              :key="option"
              :label="option"
              :value="option"
            />
          </el-select>
        </template>

        <!-- 对象项（元素子字段树可用）：折叠卡片 + 递归字段渲染；无子字段树时退化为 JSON 文本框 -->
        <template v-if="isObjectWithFields(itemSchema)">
          <div class="object-item" :class="{ 'is-expanded': expandedItems.has(index) }">
            <button
              type="button"
              class="object-item-header"
              :aria-expanded="expandedItems.has(index)"
              @click="toggleItem(index)"
            >
              <el-icon class="object-item-arrow"
                ><component :is="expandedItems.has(index) ? ArrowDown : ArrowRight"
              /></el-icon>
              <span class="object-item-title">{{ itemTitle(index) }}</span>
              <el-button
                type="danger"
                size="small"
                text
                :icon="Delete"
                class="object-item-delete"
                @click.stop="removeItem(index)"
              />
            </button>
            <div v-if="expandedItems.has(index)" class="object-item-body">
              <FieldRenderer
                v-for="sub in itemSchema?.fields"
                :key="sub.key"
                :field="sub"
                :model-value="getItemValue(items[index], sub.key)"
                :original-value="getItemValue(originalItems[index], sub.key)"
                @update:model-value="setItemValue(index, sub.key, $event)"
              />
            </div>
          </div>
        </template>

        <template v-else-if="isJsonItemType(itemSchema?.type)">
          <div class="json-item-wrapper">
            <el-input
              v-model="jsonDisplays[index]"
              type="textarea"
              :rows="2"
              size="small"
              placeholder="JSON 文本"
              @input="handleJsonEdit(index)"
            />
            <p v-if="jsonErrors[index]" class="json-item-error">JSON 解析失败，保持上一次合法值</p>
          </div>
        </template>

        <!-- 未知类型 -->
        <template v-else>
          <el-input
            v-model="items[index]"
            size="small"
            :placeholder="`类型: ${itemSchema?.type}`"
            @input="handleUpdate"
          />
        </template>

        <!-- 删除按钮 -->
        <el-button type="danger" size="small" text :icon="Delete" @click="removeItem(index)" />
      </div>
    </div>

    <!-- 添加按钮 -->
    <div class="array-actions">
      <el-button type="primary" size="small" :icon="Plus" @click="addItem"> 添加项 </el-button>
      <span v-if="items.length === 0" class="empty-hint">暂无项目</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { Plus, Delete, ArrowRight, ArrowDown } from '@element-plus/icons-vue';
import type { ConfigFieldSchema } from '@/types/settings';
import FieldRenderer from './FieldRenderer.vue';

const props = defineProps<{
  modelValue: unknown;
  field: ConfigFieldSchema;
  originalValue?: unknown;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: unknown];
  change: [];
}>();

// 数组项 Schema
const itemSchema = computed(() => props.field.items);

// 对象项编辑：元素子字段树可用时走卡片编辑器
function isObjectWithFields(schema?: ConfigFieldSchema): boolean {
  return schema?.type === 'object' && Array.isArray(schema.fields) && schema.fields.length > 0;
}

// 展开状态（按索引跟踪；删除项时随索引重排统一重置）
const expandedItems = ref<Set<number>>(new Set([0]));

function toggleItem(index: number): void {
  const next = new Set(expandedItems.value);
  if (next.has(index)) {
    next.delete(index);
  } else {
    next.add(index);
  }
  expandedItems.value = next;
}

// 元素标题：优先取标识性字段（name/title/id）的值，否则 #索引
function itemTitle(index: number): string {
  const item = items.value[index];
  if (item && typeof item === 'object') {
    const record = item as Record<string, unknown>;
    for (const key of ['name', 'title', 'id']) {
      const v = record[key];
      if (typeof v === 'string' && v.trim()) return v;
    }
  }
  return `#${index + 1}`;
}

// 元素字段读写
function getItemValue(item: unknown, name: string): unknown {
  if (item && typeof item === 'object') return (item as Record<string, unknown>)[name];
  return undefined;
}

function setItemValue(index: number, name: string, value: unknown): void {
  const item = items.value[index];
  if (item && typeof item === 'object') {
    (item as Record<string, unknown>)[name] = value;
    handleUpdate();
  }
}

// 提交整列表时的原始值参照（元素字段的"已修改"判断用）
const originalItems = computed<unknown[]>(() => {
  return Array.isArray(props.originalValue) ? props.originalValue : [];
});

// 选择选项
const itemOptions = computed(() => {
  return itemSchema.value?.validation?.options || [];
});

/** 对象/数组项的 JSON 展示串和错误标记，索引对齐 items。 */
const jsonDisplays = ref<string[]>([]);
const jsonErrors = ref<Record<number, boolean>>({});

// 本地数组
const items = ref<unknown[]>([]);

/** 对象与数组类型走 JSON 文本框编辑；其余走专用控件。 */
function isJsonItemType(type?: string): boolean {
  return type === 'object' || type === 'array';
}

/** 初始化：数组项与 JSON 展示串按当前 modelValue 重置。 */
watch(
  () => props.modelValue,
  newVal => {
    if (Array.isArray(newVal)) {
      items.value = [...newVal];
      // 重建 JSON 展示串：按当前 items 对齐；之前合法的展示串会被覆盖，避免错位
      jsonDisplays.value = items.value.map(v =>
        isJsonItemType(itemSchema.value?.type) && typeof v === 'object' && v !== null
          ? JSON.stringify(v, null, 2)
          : String(v ?? ''),
      );
      jsonErrors.value = {};
    } else {
      items.value = [];
      jsonDisplays.value = [];
      jsonErrors.value = {};
    }
  },
  { immediate: true },
);

/** 类型变化时（如 schema 重新加载）也要重建展示串；用 unref 风格直接读 computed。 */
watch(
  () => itemSchema.value?.type,
  () => {
    jsonDisplays.value = items.value.map(v =>
      isJsonItemType(itemSchema.value?.type) && typeof v === 'object' && v !== null
        ? JSON.stringify(v, null, 2)
        : String(v ?? ''),
    );
    jsonErrors.value = {};
  },
);

// 添加项
function addItem() {
  if (isObjectWithFields(itemSchema.value)) {
    // 对象项：按元素子字段树的 default 构造空对象
    const newItem: Record<string, unknown> = {};
    for (const sub of itemSchema.value?.fields ?? []) {
      newItem[sub.key] = sub.default ?? getDefaultValueForType(sub.type);
    }
    items.value.push(newItem);
    expandedItems.value = new Set([...expandedItems.value, items.value.length - 1]);
    handleUpdate();
    return;
  }
  const defaultValue = itemSchema.value?.default;
  items.value.push(defaultValue ?? getDefaultValueForType(itemSchema.value?.type));
  if (isJsonItemType(itemSchema.value?.type)) {
    jsonDisplays.value.push(
      typeof items.value[items.value.length - 1] === 'object'
        ? JSON.stringify(items.value[items.value.length - 1], null, 2)
        : '',
    );
  }
  handleUpdate();
}

// 获取类型默认值
function getDefaultValueForType(type?: string): unknown {
  switch (type) {
    case 'string':
      return '';
    case 'integer':
    case 'float':
      return 0;
    case 'boolean':
      return false;
    case 'object':
      return {};
    case 'array':
      return [];
    default:
      return '';
  }
}

// 移除项
function removeItem(index: number) {
  items.value.splice(index, 1);
  jsonDisplays.value.splice(index, 1);
  // 索引错位会导致残留的 error/展开标记指向旧位置，统一重排
  const shiftedErrors: Record<number, boolean> = {};
  for (const [k, v] of Object.entries(jsonErrors.value)) {
    const ki = Number(k);
    if (ki < index) shiftedErrors[ki] = v;
    else if (ki > index) shiftedErrors[ki - 1] = v;
  }
  jsonErrors.value = shiftedErrors;
  const shiftedExpanded = new Set<number>();
  for (const ki of expandedItems.value) {
    if (ki < index) shiftedExpanded.add(ki);
    else if (ki > index) shiftedExpanded.add(ki - 1);
  }
  expandedItems.value = shiftedExpanded;
  handleUpdate();
}

// 更新
function handleUpdate() {
  emit('update:modelValue', [...items.value]);
  emit('change');
}

/** JSON 项编辑：解析失败时保留上一次合法值，仅展示错误提示。 */
function handleJsonEdit(index: number) {
  const text = jsonDisplays.value[index] ?? '';
  try {
    const parsed = JSON.parse(text);
    jsonErrors.value[index] = false;
    items.value[index] = parsed;
    handleUpdate();
  } catch {
    // 解析失败：不修改 items，保留上一次合法值，仅标记错误状态
    jsonErrors.value[index] = true;
  }
}
</script>

<style scoped>
.array-editor {
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
  padding: var(--spacing-sm);
}

.array-items {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  margin-bottom: var(--spacing-sm);
}

.array-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
}

.array-item :deep(.el-input),
.array-item :deep(.el-select),
.array-item :deep(.el-input-number) {
  flex: 1;
}

.array-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.empty-hint {
  font-size: 12px;
  color: var(--text-placeholder);
}

.json-item-wrapper {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.json-item-error {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
}

/* 对象项折叠卡片 */
.object-item {
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  overflow: hidden;
}

.object-item-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  width: 100%;
  padding: var(--spacing-xs) var(--spacing-sm);
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
}

.object-item-header:hover {
  background: var(--bg-hover);
}

.object-item-arrow {
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.object-item-title {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.object-item-delete {
  flex-shrink: 0;
}

.object-item-body {
  padding: var(--spacing-sm);
  border-top: 1px solid var(--border-color-light);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}
</style>
