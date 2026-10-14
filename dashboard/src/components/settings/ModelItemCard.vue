<template>
  <div class="model-item-card" :class="{ 'is-dirty': isDirty }">
    <div class="model-item-header">
      <el-icon class="model-item-icon"><Cpu /></el-icon>
      <span class="model-item-title">{{ displayName || '未命名模型' }}</span>
      <el-tag v-if="isDirty" size="small" type="warning" effect="plain">已修改</el-tag>
      <el-button
        type="danger"
        size="small"
        text
        :icon="Delete"
        class="model-item-delete"
        @click="emit('remove')"
      />
    </div>

    <div class="model-item-body">
      <!-- 身份字段（name）：专用输入，失焦/回车提交——改名触发 profile 引用守护，
           逐键提交会把中间态名字写进引用方 -->
      <div class="name-field">
        <label class="name-field-label">
          模型名称<span class="required-mark">*</span>
          <el-tooltip content="模型唯一名，被 profile.model_list 引用；改名时可级联更新引用方" placement="top">
            <el-icon class="description-icon"><InfoFilled /></el-icon>
          </el-tooltip>
        </label>
        <el-input
          :model-value="displayName"
          placeholder="模型唯一名"
          @change="emit('commitName', $event)"
        />
      </div>

      <!-- 结构性绑定：api_provider 用 el-select（选项 = provider 名），仍写字符串字段 -->
      <div class="provider-bind-field">
        <label class="name-field-label">所属提供商</label>
        <el-select
          :model-value="providerBinding"
          filterable
          allow-create
          default-first-option
          placeholder="选择提供商"
          style="width: 100%"
          @update:model-value="emit('updateField', { relKey: 'api_provider', value: $event })"
        >
          <el-option v-for="name in providerNames" :key="name" :label="name" :value="name" />
        </el-select>
        <p v-if="bindingDangling" class="field-hint dangling">
          指向的提供商不存在（可能是编辑中途的悬空引用，可改选或保留）
        </p>
      </div>

      <!-- 其余常规字段：一律复用 FieldRenderer（合成 key，整值写回） -->
      <div class="field-grid">
        <FieldRenderer
          v-for="sub in normalFields"
          :key="`${MODELS_KEY}.${entry.index}.${sub.key}`"
          :field="makeItemField(sub, MODELS_KEY, entry.index)"
          :model-value="entry.model[sub.key]"
          :original-value="originalValueAt(sub.key)"
          @update:model-value="emit('updateField', { relKey: sub.key, value: $event })"
        />
      </div>

      <!-- 高级字段：跟随页面高级总开关 -->
      <details v-if="showAdvanced && advancedFields.length > 0" class="advanced-block">
        <summary class="advanced-toggle">高级参数（{{ advancedFields.length }}）</summary>
        <div class="field-grid">
          <FieldRenderer
            v-for="sub in advancedFields"
            :key="`${MODELS_KEY}.${entry.index}.${sub.key}`"
            :field="makeItemField(sub, MODELS_KEY, entry.index)"
            :model-value="entry.model[sub.key]"
            :original-value="originalValueAt(sub.key)"
            @update:model-value="emit('updateField', { relKey: sub.key, value: $event })"
          />
        </div>
      </details>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 模型子卡：provider 节内「名下模型」与「未归属」列表共用的单模型编辑卡。
 * 身份字段（name / api_provider）为结构性绑定走专用控件，其余叶字段一律
 * FieldRenderer；写路径由父组件统一整值写回，本组件不直接持有 store。
 */
import { computed } from 'vue';
import { Cpu, Delete, InfoFilled } from '@element-plus/icons-vue';
import FieldRenderer from './FieldRenderer.vue';
import type { ConfigFieldSchema } from '@/types/settings';
import { MODELS_KEY, diffCountAgainst, makeItemField, type ConfigItem } from '@/utils/modelConfig';

const props = defineProps<{
  /** 在 llm_models 扁平数组中的下标与当前值 */
  entry: { index: number; model: ConfigItem };
  providerNames: string[];
  modelItemFields: ConfigFieldSchema[];
  showAdvanced: boolean;
  getOriginal: (key: string) => unknown;
}>();

const emit = defineEmits<{
  updateField: [payload: { relKey: string; value: unknown }];
  commitName: [name: string];
  remove: [];
}>();

const displayName = computed(() => String(props.entry.model?.name ?? ''));

const providerBinding = computed(() => String(props.entry.model?.api_provider ?? ''));

/** api_provider 指向不在当前 provider 名单的名字 = 悬空引用（不藏，明示提示） */
const bindingDangling = computed(
  () => providerBinding.value !== '' && !props.providerNames.includes(providerBinding.value),
);

const normalFields = computed(() =>
  props.modelItemFields.filter(f => f.key !== 'name' && f.key !== 'api_provider' && !f.advanced),
);

const advancedFields = computed(() =>
  props.modelItemFields.filter(
    f => f.key !== 'name' && f.key !== 'api_provider' && f.advanced === true,
  ),
);

function originalValueAt(relKey: string): unknown {
  return props.getOriginal(`${MODELS_KEY}.${props.entry.index}.${relKey}`);
}

const isDirty = computed(() => {
  const original = props.getOriginal(`${MODELS_KEY}.${props.entry.index}`) as
    | ConfigItem
    | undefined;
  return diffCountAgainst(original, props.entry.model) > 0;
});
</script>

<style scoped>
.model-item-card {
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  overflow: hidden;
  transition: opacity var(--transition-fast);
}

.model-item-card.is-dirty {
  border-color: var(--color-warning);
}

.model-item-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  padding: var(--spacing-xs) var(--spacing-sm);
  background: var(--bg-hover);
  border-bottom: 1px solid var(--border-color-light);
}

.model-item-icon {
  font-size: 14px;
  color: var(--color-primary);
}

.model-item-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-item-delete {
  margin-left: auto;
  flex-shrink: 0;
}

.model-item-body {
  padding: var(--spacing-sm);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.name-field,
.provider-bind-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.name-field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.required-mark {
  color: var(--color-danger);
  margin-left: 2px;
}

.description-icon {
  margin-left: 4px;
  font-size: 13px;
  color: var(--text-secondary);
  vertical-align: middle;
  cursor: help;
}

.field-hint {
  margin: 0;
  font-size: 11px;
  color: var(--text-secondary);
}

.field-hint.dangling {
  color: var(--color-warning);
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--spacing-sm);
  align-items: start;
}

.advanced-block {
  border-top: 1px dashed var(--border-color-light);
  padding-top: var(--spacing-xs);
}

.advanced-toggle {
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.advanced-toggle:hover {
  color: var(--color-primary);
}
</style>
