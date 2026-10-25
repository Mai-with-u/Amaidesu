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
        <!-- 描述图标模式：ⓘ 悬浮展示，替代内联段落（密集字段列表节省纵向空间） -->
        <el-tooltip
          v-if="field.description && showDescriptionIcon"
          :content="field.description"
          placement="top"
        >
          <el-icon class="description-icon"><InfoFilled /></el-icon>
        </el-tooltip>
      </label>
      <el-tag v-if="isModified" size="small" type="warning" effect="plain">已修改</el-tag>
    </div>

    <!-- 字段描述（icon 模式改走上方 ⓘ 悬浮，不重复渲染内联段落） -->
    <p v-if="field.description && !showDescriptionIcon" class="field-description">
      {{ field.description }}
    </p>

    <!-- 字段输入 -->
    <div class="field-input">
      <!-- 滑条控件：slider 拖拽 + 数字框精调绑同一值。置于链头：slider 字段的 type 多为
           integer/float，若排在 type 分支之后会被整数/浮点分支先拦截，滑条永不渲染 -->
      <template v-if="field.widget === 'slider'">
        <div class="slider-control">
          <el-slider
            v-model="sliderModel"
            :min="field.validation?.min"
            :max="field.validation?.max"
            :disabled="field.readonly"
          />
          <el-input-number
            v-model="sliderModel"
            :min="field.validation?.min"
            :max="field.validation?.max"
            :disabled="field.readonly"
            controls-position="right"
          />
        </div>
      </template>

      <!-- 字符串类型 -->
      <template v-else-if="field.type === 'string'">
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

      <!-- 定长数值元组：一行并排数字输入（如截图区域 [x, y, w, h]），替代逐项添加的通用数组编辑器。
           widget 优先于 type（挂在同一条 v-if 链上）——脱链的 v-if 会让元组字段同时渲染
           type 控件与元组控件两套输入，且都绑 localValue，一处编辑另一处同步 -->
      <template v-else-if="field.widget === 'fixed-tuple'">
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
import { InfoFilled } from '@element-plus/icons-vue';
import type { ConfigFieldSchema } from '@/types/settings';
import {
  defaultPlaceholder as defaultPlaceholderOf,
  isEqualJSON,
  isLongTextOf,
  tupleJoin,
  tupleSplit,
  validateValue as validateField,
} from '@/utils/configField';
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

// 是否为长文本（判定规则见 utils/configField.ts 的 isLongTextOf）
const isLongText = computed(() => isLongTextOf(props.field, props.modelValue));

// 描述展示形态：icon = 标签旁 ⓘ 悬浮（缺省 inline = 内联段落）
const showDescriptionIcon = computed(() => (props.field.descriptionDisplay ?? 'inline') === 'icon');

// 滑条 + 数字框共用的数值视图：localValue 缺失时回退字段默认值，非数值输入不驱动控件
const sliderModel = computed<number>({
  get: () => {
    if (typeof localValue.value === 'number') return localValue.value;
    const d = props.field.default;
    return typeof d === 'number' ? d : (props.field.validation?.min ?? 0);
  },
  set: val => {
    localValue.value = val;
    handleChange();
  },
});

// 占位提示（规则见 utils/configField.ts 的 defaultPlaceholder）
const defaultPlaceholder = computed(() => defaultPlaceholderOf(props.field));

// 选择选项
const selectOptions = computed(() => {
  return props.field.validation?.options || [];
});

// 定长数值元组：按标记长度拆成并排数字输入（拆合规则见 utils/configField.ts）
const tupleLength = computed(() =>
  props.field.widget === 'fixed-tuple' ? (props.field.tupleLength ?? 4) : 0,
);

const tupleValues = computed<(number | undefined)[]>(() =>
  tupleSplit(localValue.value, tupleLength.value),
);

function setTupleValue(index: number, value: number | undefined) {
  const next = [...tupleValues.value];
  next[index] = value;
  localValue.value = tupleJoin(next);
  handleChange();
}

// 是否已修改：JSON 序列化比较（语义与原实现一致，非深比较）
const isModified = computed(() => {
  return !isEqualJSON(localValue.value, props.originalValue ?? props.field.default);
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
  error.value = validateField(props.field, localValue.value);

  // 如果验证通过，发送更新
  if (!error.value) {
    emit('update:modelValue', localValue.value);
  }
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
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast);
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

.readonly-tag {
  margin-left: 6px;
  vertical-align: middle;
}

.description-icon {
  margin-left: 4px;
  font-size: 13px;
  color: var(--text-secondary);
  vertical-align: middle;
  cursor: help;
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

/* 滑条 + 数字框：并排布局，数字框固定宽精调 */
.slider-control {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.slider-control :deep(.el-slider) {
  flex: 1;
}

.slider-control :deep(.el-input-number) {
  width: 130px;
  flex-shrink: 0;
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
