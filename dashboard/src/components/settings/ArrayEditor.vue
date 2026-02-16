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

// 本地数组
const items = ref<unknown[]>([]);

// 初始化
watch(
  () => props.modelValue,
  (newVal) => {
    if (Array.isArray(newVal)) {
      items.value = [...newVal];
    } else {
      items.value = [];
    }
  },
  { immediate: true },
);

// 添加项
function addItem() {
  const defaultValue = itemSchema.value?.default;
  items.value.push(defaultValue ?? getDefaultValueForType(itemSchema.value?.type));
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
    default:
      return '';
  }
}

// 移除项
function removeItem(index: number) {
  items.value.splice(index, 1);
  handleUpdate();
}

// 更新
function handleUpdate() {
  emit('update:modelValue', [...items.value]);
  emit('change');
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
</style>
