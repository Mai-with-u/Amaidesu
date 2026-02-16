<template>
  <div class="field-renderer" :class="{ 'is-modified': isModified, 'has-error': error }">
    <!-- 字段标签 -->
    <div class="field-header">
      <label class="field-label">
        {{ field.label }}
        <span v-if="field.required" class="required-mark">*</span>
      </label>
      <el-tag v-if="isModified" size="small" type="warning" effect="plain">已修改</el-tag>
    </div>

    <!-- 字段描述 -->
    <p v-if="field.description" class="field-description">{{ field.description }}</p>

    <!-- 字段输入 -->
    <div class="field-input">
      <!-- 字符串类型 -->
      <template v-if="field.type === 'string'">
        <el-input
          v-if="field.sensitive"
          v-model="localValue"
          type="password"
          show-password
          :placeholder="String(field.default || '')"
          @input="handleChange"
        />
        <el-input
          v-else-if="isLongText"
          v-model="localValue"
          type="textarea"
          :rows="3"
          :placeholder="String(field.default || '')"
          @input="handleChange"
        />
        <el-input
          v-else
          v-model="localValue"
          :placeholder="String(field.default || '')"
          @input="handleChange"
        />
      </template>

      <!-- 整数类型 -->
      <template v-else-if="field.type === 'integer'">
        <el-input-number
          v-model="localValue"
          :min="field.validation?.min"
          :max="field.validation?.max"
          :step="1"
          controls-position="right"
          @change="handleChange"
        />
      </template>

      <!-- 浮点数类型 -->
      <template v-else-if="field.type === 'float'">
        <el-input-number
          v-model="localValue"
          :min="field.validation?.min"
          :max="field.validation?.max"
          :step="0.1"
          :precision="2"
          controls-position="right"
          @change="handleChange"
        />
      </template>

      <!-- 布尔类型 -->
      <template v-else-if="field.type === 'boolean'">
        <el-switch v-model="localValue" @change="handleChange" />
      </template>

      <!-- 选择类型 -->
      <template v-else-if="field.type === 'select'">
        <el-select v-model="localValue" :placeholder="'请选择'" @change="handleChange">
          <el-option
            v-for="option in selectOptions"
            :key="option"
            :label="option"
            :value="option"
          />
        </el-select>
      </template>

      <!-- 数组类型 -->
      <template v-else-if="field.type === 'array'">
        <ArrayEditor v-model="localValue" :field="field" @change="handleChange" />
      </template>

      <!-- 对象类型 -->
      <template v-else-if="field.type === 'object'">
        <div class="nested-object">
          <FieldRenderer
            v-for="(propField, propKey) in field.properties"
            :key="propKey"
            :field="propField"
            :model-value="getObjectValue(propKey)"
            :original-value="getObjectOriginalValue(propKey)"
            @update:model-value="setObjectValue(propKey, $event)"
          />
        </div>
      </template>

      <!-- 未知类型 -->
      <template v-else>
        <el-input
          v-model="localValue"
          :placeholder="`未知类型: ${field.type}`"
          @input="handleChange"
        />
      </template>
    </div>

    <!-- 错误提示 -->
    <p v-if="error" class="field-error">{{ error }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import type { ConfigFieldSchema } from '@/types/settings';
import ArrayEditor from './ArrayEditor.vue';

const props = defineProps<{
  field: ConfigFieldSchema;
  modelValue: unknown;
  originalValue?: unknown;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: unknown];
}>();

// 本地值
const localValue = ref<unknown>(props.modelValue ?? props.field.default);

// 监听外部值变化
watch(
  () => props.modelValue,
  (newVal) => {
    localValue.value = newVal ?? props.field.default;
  },
);

// 是否为长文本
const isLongText = computed(() => {
  if (props.field.type !== 'string') return false;
  const desc = props.field.description || '';
  return desc.length > 50 || (props.field.validation?.max_length ?? 0) > 100;
});

// 选择选项
const selectOptions = computed(() => {
  return props.field.validation?.options || [];
});

// 是否已修改
const isModified = computed(() => {
  return (
    JSON.stringify(localValue.value) !== JSON.stringify(props.originalValue ?? props.field.default)
  );
});

// 错误信息
const error = ref<string | null>(null);

// 处理变更
function handleChange() {
  // 验证
  error.value = validateValue(localValue.value);

  // 如果验证通过，发送更新
  if (!error.value) {
    emit('update:modelValue', localValue.value);
  }
}

// 验证值
function validateValue(value: unknown): string | null {
  const { field } = props;

  // 必填检查
  if (field.required && (value === null || value === undefined || value === '')) {
    return '此字段为必填项';
  }

  // 类型特定验证
  if (field.validation) {
    const { min, max, min_length, max_length, pattern } = field.validation;

    if (field.type === 'integer' || field.type === 'float') {
      if (min !== undefined && (value as number) < min) {
        return `最小值为 ${min}`;
      }
      if (max !== undefined && (value as number) > max) {
        return `最大值为 ${max}`;
      }
    }

    if (field.type === 'string') {
      const str = value as string;
      if (min_length !== undefined && str.length < min_length) {
        return `最少 ${min_length} 个字符`;
      }
      if (max_length !== undefined && str.length > max_length) {
        return `最多 ${max_length} 个字符`;
      }
      if (pattern) {
        const regex = new RegExp(pattern);
        if (!regex.test(str)) {
          return '格式不正确';
        }
      }
    }
  }

  return null;
}

// 对象类型辅助方法
function getObjectValue(key: string): unknown {
  const obj = (localValue.value as Record<string, unknown>) || {};
  return obj[key];
}

function getObjectOriginalValue(key: string): unknown {
  const obj = (props.originalValue as Record<string, unknown>) || {};
  return obj[key];
}

function setObjectValue(key: string, value: unknown) {
  const obj = (localValue.value as Record<string, unknown>) || {};
  obj[key] = value;
  localValue.value = { ...obj };
  handleChange();
}
</script>

<style scoped>
.field-renderer {
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  transition: all var(--transition-fast);
}

.field-renderer.is-modified {
  border-color: var(--color-warning);
  background: rgba(230, 162, 60, 0.05);
}

.field-renderer.has-error {
  border-color: var(--color-danger);
}

.field-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-xs);
}

.field-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.required-mark {
  color: var(--color-danger);
  margin-left: 2px;
}

.field-description {
  font-size: 12px;
  color: var(--text-secondary);
  margin: 0 0 var(--spacing-sm);
  line-height: 1.5;
}

.field-input {
  width: 100%;
}

.field-input :deep(.el-input),
.field-input :deep(.el-select),
.field-input :deep(.el-input-number) {
  width: 100%;
}

.field-input :deep(.el-textarea__inner) {
  font-family: var(--font-mono);
}

.field-error {
  margin: var(--spacing-xs) 0 0;
  font-size: 12px;
  color: var(--color-danger);
}

.nested-object {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm);
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
}
</style>
