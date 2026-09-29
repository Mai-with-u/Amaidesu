<template>
  <div
    class="field-renderer"
    :class="{ 'is-modified': isModified, 'has-error': error }"
    :data-config-key="field.key"
  >
    <!-- 字段标签 -->
    <div class="field-header">
      <label class="field-label">
        {{ field.label }}
        <span v-if="field.required" class="required-mark">*</span>
        <el-tag v-if="field.readonly" size="small" type="info" effect="plain" class="readonly-tag"
          >只读</el-tag
        >
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
          :placeholder="defaultPlaceholder"
          :disabled="field.readonly"
          @input="handleChange"
        />
        <el-input
          v-else-if="isLongText"
          v-model="localValue"
          type="textarea"
          :rows="3"
          :placeholder="defaultPlaceholder"
          :disabled="field.readonly"
          @input="handleChange"
        />
        <el-input
          v-else
          v-model="localValue"
          :placeholder="defaultPlaceholder"
          :disabled="field.readonly"
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
          :placeholder="defaultPlaceholder"
          controls-position="right"
          :disabled="field.readonly"
          @change="handleChange"
        />
      </template>

      <!-- 定长数值元组：一行并排数字输入（如截图区域 [x, y, w, h]），替代逐项添加的通用数组编辑器 -->
      <template v-if="field.widget === 'fixed-tuple'">
        <div class="fixed-tuple" :disabled="field.readonly">
          <el-input-number
            v-for="(_, i) in tupleLength"
            :key="i"
            :model-value="tupleValues[i]"
            size="small"
            controls-position="right"
            :disabled="field.readonly"
            @update:model-value="setTupleValue(i, $event)"
          />
        </div>
      </template>

      <!-- 浮点数类型 -->
      <template v-else-if="field.type === 'float'">
        <el-input-number
          v-model="localValue"
          :min="field.validation?.min"
          :max="field.validation?.max"
          :step="0.1"
          :precision="field.precision"
          :placeholder="defaultPlaceholder"
          controls-position="right"
          :disabled="field.readonly"
          @change="handleChange"
        />
      </template>

      <!-- 布尔类型 -->
      <template v-else-if="field.type === 'boolean'">
        <el-switch v-model="localValue" :disabled="field.readonly" @change="handleChange" />
      </template>

      <!-- 选择类型 -->
      <template v-else-if="field.type === 'select'">
        <el-select
          v-model="localValue"
          :placeholder="'请选择'"
          :disabled="field.readonly"
          @change="handleChange"
        >
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
        <ArrayEditor
          v-model="localValue"
          :field="field"
          :original-value="originalValue"
          @change="handleChange"
        />
      </template>

      <!-- 对象类型：有 properties 时递归渲染子字段 -->
      <template
        v-else-if="
          field.type === 'object' && field.properties && Object.keys(field.properties).length > 0
        "
      >
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

      <!-- 对象类型：无 properties（自由 dict），键值对编辑 -->
      <template v-else-if="field.type === 'object'">
        <DictEditor
          :model-value="localValue"
          @update:model-value="setDictValue"
          @change="handleChange"
        />
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
import DictEditor from './DictEditor.vue';

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
  newVal => {
    localValue.value = newVal ?? props.field.default;
  },
);

// 是否为长文本
const isLongText = computed(() => {
  if (props.field.type !== 'string') return false;
  const desc = props.field.description || '';
  return desc.length > 50 || (props.field.validation?.max_length ?? 0) > 100;
});

// 占位提示：默认值加「默认」前缀，与已填值区分（裸默认值会被误读为来源不明的灰字建议）；
// 敏感字段不回显默认值形态
const defaultPlaceholder = computed(() => {
  if (props.field.sensitive) return '';
  const d = props.field.default;
  if (d === null || d === undefined || d === '') return '';
  return `默认 ${String(d)}`;
});

// 选择选项
const selectOptions = computed(() => {
  return props.field.validation?.options || [];
});

// 定长数值元组：按标记长度拆成并排数字输入；全填齐才产出数组，任一空缺回退 null（= 未指定）
const tupleLength = computed(() =>
  props.field.widget === 'fixed-tuple' ? (props.field.tupleLength ?? 4) : 0,
);

const tupleValues = computed<(number | undefined)[]>(() => {
  const arr = Array.isArray(localValue.value) ? localValue.value : [];
  return Array.from({ length: tupleLength.value }, (_, i) =>
    typeof arr[i] === 'number' ? (arr[i] as number) : undefined,
  );
});

function setTupleValue(index: number, value: number | undefined) {
  const next = [...tupleValues.value];
  next[index] = value;
  const complete = next.every(v => typeof v === 'number');
  localValue.value = complete ? next.map(v => v as number) : null;
  handleChange();
}

// 是否已修改
const isModified = computed(() => {
  return (
    JSON.stringify(localValue.value) !== JSON.stringify(props.originalValue ?? props.field.default)
  );
});

// 错误信息
const error = ref<string | null>(null);

// 自由 dict 字段的值更新
function setDictValue(val: unknown) {
  localValue.value = val;
  handleChange();
}

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

.readonly-tag {
  margin-left: 6px;
  vertical-align: middle;
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

/* 定长数值元组：一行并排的数字输入 */
.fixed-tuple {
  display: flex;
  gap: var(--spacing-xs);
}

.fixed-tuple :deep(.el-input-number) {
  flex: 1;
  min-width: 0;
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
