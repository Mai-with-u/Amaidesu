<template>
  <div class="array-editor">
    <!-- 字符串/枚举数组：单控件多选标签（回车创建、下拉候选、标签可删），不逐行编辑 -->
    <el-select
      v-if="isTagSelect"
      :model-value="tagValues"
      multiple
      filterable
      :allow-create="!hasClosedOptionPool"
      default-first-option
      :placeholder="tagPlaceholder"
      class="tag-select"
      style="width: 100%"
      @update:model-value="setTagValues($event as string[])"
    >
      <el-option v-for="option in optionPool" :key="option" :label="option" :value="option" />
    </el-select>

    <!-- 其余类型：逐项编辑 -->
    <template v-else>
      <div class="array-items">
        <div v-for="(id, index) in itemIds" :key="id" class="array-item">
          <!-- 对象项（元素子字段树可用）：折叠卡片 + 递归字段渲染；无子字段树时退化为 JSON 文本框 -->
          <template v-if="isObjectWithFields(itemSchema)">
            <div class="object-item" :class="{ 'is-expanded': expandedIds.has(id) }">
              <button
                type="button"
                class="object-item-header"
                :aria-expanded="expandedIds.has(id)"
                @click="toggleItem(id)"
              >
                <el-icon class="object-item-arrow"
                  ><component :is="expandedIds.has(id) ? ArrowDown : ArrowRight"
                /></el-icon>
                <span class="object-item-title">{{ itemTitle(index) }}</span>
                <el-button
                  type="danger"
                  size="small"
                  text
                  :icon="Delete"
                  class="object-item-delete"
                  @click.stop="removeItem(id)"
                />
              </button>
              <div v-if="expandedIds.has(id)" class="object-item-body">
                <FieldRenderer
                  v-for="sub in normalItemFields"
                  :key="sub.key"
                  :field="sub"
                  :model-value="getItemValue(items[index], sub.key)"
                  :original-value="getItemValue(originalItems[index], sub.key)"
                  @update:model-value="setItemValue(index, sub.key, $event)"
                />
                <!-- 高级参数：鉴权细节、重试参数等低频字段，默认折叠 -->
                <div v-if="advancedItemFields.length > 0" class="advanced-block">
                  <button
                    type="button"
                    class="advanced-toggle"
                    :aria-expanded="advancedIds.has(id)"
                    @click="toggleAdvanced(id)"
                  >
                    <el-icon class="advanced-arrow">
                      <component :is="advancedIds.has(id) ? ArrowDown : ArrowRight" />
                    </el-icon>
                    高级参数（{{ advancedItemFields.length }}）
                  </button>
                  <div v-show="advancedIds.has(id)" class="advanced-body">
                    <FieldRenderer
                      v-for="sub in advancedItemFields"
                      :key="sub.key"
                      :field="sub"
                      :model-value="getItemValue(items[index], sub.key)"
                      :original-value="getItemValue(originalItems[index], sub.key)"
                      @update:model-value="setItemValue(index, sub.key, $event)"
                    />
                  </div>
                </div>
              </div>
            </div>
          </template>

          <template v-else-if="itemSchema?.type === 'integer'">
            <el-input-number
              v-model="items[index]"
              size="small"
              :min="itemSchema.validation?.min"
              :max="itemSchema.validation?.max"
              controls-position="right"
              @change="handleUpdate"
            />
          </template>

          <template v-else-if="itemSchema?.type === 'float'">
            <el-input-number
              v-model="items[index]"
              size="small"
              :min="itemSchema.validation?.min"
              :max="itemSchema.validation?.max"
              :step="0.1"
              :precision="itemSchema.precision"
              controls-position="right"
              @change="handleUpdate"
            />
          </template>

          <template v-else-if="itemSchema?.type === 'boolean'">
            <el-switch v-model="items[index]" @change="handleUpdate" />
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
              <p v-if="jsonErrors[id]" class="json-item-error">JSON 解析失败，保持上一次合法值</p>
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

          <!-- 删除按钮：对象项的卡头已内联删除，此处只服务标量/JSON 项，避免一行双删除 -->
          <el-button
            v-if="!isObjectWithFields(itemSchema)"
            type="danger"
            size="small"
            text
            :icon="Delete"
            @click="removeItem(id)"
          />
        </div>
      </div>

      <!-- 添加按钮 -->
      <div class="array-actions">
        <el-button type="primary" size="small" :icon="Plus" @click="addItem"> 添加项 </el-button>
        <span v-if="items.length === 0" class="empty-hint">暂无项目</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { Plus, Delete, ArrowRight, ArrowDown } from '@element-plus/icons-vue';
import type { ConfigFieldSchema } from '@/types/settings';
import { getDefaultValueForType } from '@/utils/configField';
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

/** 字符串/枚举元素的多选标签形态：单控件承载增删，替代逐行编辑 */
function isTagSelectArray(): boolean {
  if (isObjectWithFields(itemSchema.value)) return false;
  const type = itemSchema.value?.type;
  return !type || type === 'string' || type === 'select';
}
const isTagSelect = computed(isTagSelectArray);

// 候选池：字段/元素级 options + 现值（现值不在候选内时标签仍可正常显示与反选）
const optionPool = computed<string[]>(() => {
  const options = itemSchema.value?.validation?.options || props.field.validation?.options || [];
  const current = Array.isArray(props.modelValue)
    ? props.modelValue.filter((v): v is string => typeof v === 'string')
    : [];
  return [...new Set([...options.map(String), ...current])];
});

// 封闭候选集：字段/元素级声明了 options 名单时禁用 allow-create（只能从名单选，手敲非法名被拒）
const hasClosedOptionPool = computed(() => {
  const options = itemSchema.value?.validation?.options || props.field.validation?.options || [];
  return options.length > 0;
});

const tagValues = computed<string[]>(() =>
  Array.isArray(props.modelValue)
    ? props.modelValue.map(v => (typeof v === 'string' ? v : String(v)))
    : [],
);

function setTagValues(values: string[]): void {
  items.value = [...values];
  handleUpdate();
}

const tagPlaceholder = computed(() =>
  optionPool.value.length > 0 ? '选择或输入后回车添加' : '输入后回车添加',
);

// 每项的稳定标识：进入列表时分配自增 id，此后删除/重排不变——展开态、错误态
// 等「项的本地状态」都按项 id 记录，删除中间项后其余项的状态自然保持原位，无需平移
const itemIds = ref<number[]>([]);
let nextItemId = 0;
function allocateItemId(): number {
  nextItemId += 1;
  return nextItemId;
}

// 展开状态（按项 id 记录；重建列表时默认展开第一项）
const expandedIds = ref<Set<number>>(new Set());

/** 高级参数折叠状态（按项 id 记录；x-ui-advanced 字段收进此处，默认收起） */
const advancedIds = ref<Set<number>>(new Set());

function toggleAdvanced(id: number): void {
  const next = new Set(advancedIds.value);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  advancedIds.value = next;
}

/** 元素子字段按 x-ui-advanced 拆分：常规字段直出，高级字段进折叠区 */
const normalItemFields = computed(() => (itemSchema.value?.fields ?? []).filter(f => !f.advanced));
const advancedItemFields = computed(() => (itemSchema.value?.fields ?? []).filter(f => f.advanced));

function toggleItem(id: number): void {
  const next = new Set(expandedIds.value);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  expandedIds.value = next;
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

/** 对象/数组项的 JSON 展示串和错误标记：展示串与 items 索引对齐，错误标记按项 id 记录。 */
const jsonDisplays = ref<string[]>([]);
const jsonErrors = ref<Record<number, boolean>>({});

// 本地数组
const items = ref<unknown[]>([]);

/** 对象与数组类型走 JSON 文本框编辑；其余走专用控件。 */
function isJsonItemType(type?: string): boolean {
  return type === 'object' || type === 'array';
}

/** 重建 JSON 展示串：按当前 items 对齐；之前合法的展示串会被覆盖，避免错位 */
function rebuildJsonDisplays(): void {
  jsonDisplays.value = items.value.map(v =>
    isJsonItemType(itemSchema.value?.type) && typeof v === 'object' && v !== null
      ? JSON.stringify(v, null, 2)
      : String(v ?? ''),
  );
}

/** 初始化：数组项、稳定 id 与 JSON 展示串按当前 modelValue 重建，项的本地状态全部清零。 */
watch(
  () => props.modelValue,
  newVal => {
    if (Array.isArray(newVal)) {
      items.value = [...newVal];
      itemIds.value = items.value.map(() => allocateItemId());
      rebuildJsonDisplays();
      jsonErrors.value = {};
      expandedIds.value = itemIds.value.length > 0 ? new Set([itemIds.value[0]!]) : new Set();
      advancedIds.value = new Set();
    } else {
      items.value = [];
      itemIds.value = [];
      jsonDisplays.value = [];
      jsonErrors.value = {};
      expandedIds.value = new Set();
      advancedIds.value = new Set();
    }
  },
  { immediate: true },
);

/** 类型变化时（如 schema 重新加载）也要重建展示串 */
watch(
  () => itemSchema.value?.type,
  () => {
    rebuildJsonDisplays();
    jsonErrors.value = {};
  },
);

// 添加项
function addItem() {
  const newId = allocateItemId();
  if (isObjectWithFields(itemSchema.value)) {
    // 对象项：按元素子字段树的 default 构造空对象
    const newItem: Record<string, unknown> = {};
    for (const sub of itemSchema.value?.fields ?? []) {
      newItem[sub.key] = sub.default ?? getDefaultValueForType(sub.type);
    }
    items.value.push(newItem);
    itemIds.value.push(newId);
    expandedIds.value = new Set([...expandedIds.value, newId]);
    handleUpdate();
    return;
  }
  const defaultValue = itemSchema.value?.default;
  items.value.push(defaultValue ?? getDefaultValueForType(itemSchema.value?.type));
  itemIds.value.push(newId);
  if (isJsonItemType(itemSchema.value?.type)) {
    jsonDisplays.value.push(
      typeof items.value[items.value.length - 1] === 'object'
        ? JSON.stringify(items.value[items.value.length - 1], null, 2)
        : '',
    );
  }
  handleUpdate();
}

// 移除项：本地状态按项 id 记录，删掉对应条目即可，其余项状态不受影响
function removeItem(id: number) {
  const index = itemIds.value.indexOf(id);
  if (index === -1) return;
  items.value.splice(index, 1);
  jsonDisplays.value.splice(index, 1);
  itemIds.value.splice(index, 1);
  delete jsonErrors.value[id];
  const nextExpanded = new Set(expandedIds.value);
  nextExpanded.delete(id);
  expandedIds.value = nextExpanded;
  const nextAdvanced = new Set(advancedIds.value);
  nextAdvanced.delete(id);
  advancedIds.value = nextAdvanced;
  handleUpdate();
}

// 更新
function handleUpdate() {
  emit('update:modelValue', [...items.value]);
  emit('change');
}

/** JSON 项编辑：解析失败时保留上一次合法值，仅展示错误提示（错误标记按项 id 记录）。 */
function handleJsonEdit(index: number) {
  const text = jsonDisplays.value[index] ?? '';
  const id = itemIds.value[index];
  if (id === undefined) return;
  try {
    const parsed = JSON.parse(text);
    jsonErrors.value[id] = false;
    items.value[index] = parsed;
    handleUpdate();
  } catch {
    // 解析失败：不修改 items，保留上一次合法值，仅标记错误状态
    jsonErrors.value[id] = true;
  }
}
</script>

<style scoped>
.array-editor {
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
  padding: var(--spacing-sm);
}

/* 多选标签形态：单控件占满行宽 */
.tag-select {
  width: 100%;
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

/* 对象项折叠卡片：占满行宽（flex 容器内默认收缩到内容宽，13 字段的 provider 卡会被挤成窄条） */
.object-item {
  flex: 1 1 auto;
  min-width: 0;
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

/* 高级参数折叠区 */

.advanced-toggle {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  padding: var(--spacing-xs) 0;
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
}

.advanced-toggle:hover {
  color: var(--color-primary);
}

.advanced-arrow {
  font-size: 12px;
}

.advanced-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding-top: var(--spacing-xs);
}
</style>
