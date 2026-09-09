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

        <!-- 对象/数组项：用 JSON 文本框呈现；解析失败时显示红字并保留上一次合法值 -->
        <template v-else-if="isJsonItemType(itemSchema.type)">
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
import { Plus, Delete } from '@element-plus/icons-vue';
import type { ConfigFieldSchema } from '@/types/settings';

const props = defineProps<{
  modelValue: unknown;
  field: ConfigFieldSchema;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: unknown];
  change: [];
}>();

// 数组项 Schema
const itemSchema = computed(() => props.field.items);

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
  // 索引错位会导致残留的 error 标记指向旧位置，统一清掉
  const shifted: Record<number, boolean> = {};
  for (const [k, v] of Object.entries(jsonErrors.value)) {
    const ki = Number(k);
    if (ki < index) shifted[ki] = v;
    else if (ki > index) shifted[ki - 1] = v;
  }
  jsonErrors.value = shifted;
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
</style>
