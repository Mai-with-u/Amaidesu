<template>
  <div class="capabilities-panel">
    <div v-if="loading" class="state-container">
      <el-skeleton :rows="3" animated />
    </div>

    <div v-else-if="error" class="state-container error-state">
      <el-alert :title="error" type="error" :closable="false" show-icon />
      <el-button size="small" type="primary" class="retry-btn" @click="handleRetry">
        重试
      </el-button>
    </div>

    <div v-else-if="filteredActions.length === 0" class="state-container">
      <el-empty description="暂无可测试 action" :image-size="80" />
    </div>

    <div v-else class="panel-content">
      <el-alert
        v-if="feedback"
        :type="feedback.type"
        :title="feedback.message"
        show-icon
        :closable="true"
        class="feedback-alert"
        @close="clearFeedback"
      />

      <el-collapse v-model="activeNames" class="handler-collapse">
        <el-collapse-item
          v-for="group in groupedActions"
          :key="group.handlerPrefix"
          :name="group.handlerPrefix"
        >
          <template #title>
            <span class="handler-title">
              <el-tag size="small" effect="plain" class="handler-tag">
                {{ group.handlerPrefix }}
              </el-tag>
              <span class="handler-count">{{ group.actions.length }}</span>
            </span>
          </template>

          <div class="action-list">
            <div v-for="action in group.actions" :key="action.name" class="action-row">
              <div class="action-header">
                <code class="action-local-name">{{ getLocalName(action.name) }}</code>
                <span v-if="action.description" class="action-description">
                  {{ action.description }}
                </span>
              </div>

              <div v-if="hasParameters(action)" class="param-list">
                <div v-for="(spec, key) in action.parameters" :key="key" class="param-row">
                  <div class="param-label">
                    <span class="param-key">{{ key }}</span>
                    <span v-if="spec.required" class="required-mark" title="必填">*</span>
                    <el-tooltip v-if="spec.description" :content="spec.description" placement="top">
                      <span class="param-help-icon">?</span>
                    </el-tooltip>
                  </div>
                  <div class="param-control">
                    <el-input
                      v-if="spec.type === 'string'"
                      v-model="parameterValues[action.name][key]"
                      size="small"
                      placeholder="string"
                    />
                    <el-input-number
                      v-else-if="spec.type === 'integer'"
                      v-model="parameterValues[action.name][key]"
                      :step="1"
                      :precision="0"
                      :min="spec.minimum"
                      :max="spec.maximum"
                      size="small"
                      controls-position="right"
                      class="param-number"
                    />
                    <el-input-number
                      v-else-if="spec.type === 'number'"
                      v-model="parameterValues[action.name][key]"
                      :step="1"
                      :min="spec.minimum"
                      :max="spec.maximum"
                      size="small"
                      controls-position="right"
                      class="param-number"
                    />
                    <el-switch
                      v-else-if="spec.type === 'boolean'"
                      v-model="parameterValues[action.name][key]"
                      size="small"
                    />
                  </div>
                </div>
              </div>

              <div class="action-footer">
                <el-button
                  size="small"
                  type="primary"
                  :disabled="!hasParameters(action)"
                  :loading="submitting === action.name"
                  @click="handleExecute(action)"
                >
                  执行
                </el-button>
              </div>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { ElCollapse, ElCollapseItem, ElSkeleton } from 'element-plus';
import type { ParameterType, UnifiedActionEntry } from '@/types';
import { maibotApi } from '@/api';

interface Props {
  capabilities: { actions: UnifiedActionEntry[] };
  handlerName: string;
  loading: boolean;
  error: string | null;
}

interface Emits {
  (e: 'retry'): void;
}

interface ActionGroup {
  handlerPrefix: string;
  actions: UnifiedActionEntry[];
}

interface Feedback {
  type: 'success' | 'error';
  message: string;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

const activeNames = ref<string[]>([]);
const submitting = ref<string | null>(null);
const feedback = ref<Feedback | null>(null);
const parameterValues = reactive<Record<string, Record<string, unknown>>>({});

function defaultValueForType(type: ParameterType): unknown {
  switch (type) {
    case 'string':
      return '';
    case 'integer':
    case 'number':
      return null;
    case 'boolean':
      return false;
  }
}

function ensureInitialized(action: UnifiedActionEntry): void {
  if (parameterValues[action.name]) return;
  const initial: Record<string, unknown> = {};
  for (const [key, spec] of Object.entries(action.parameters)) {
    if ('default' in spec && spec.default !== undefined) {
      initial[key] = spec.default;
    } else {
      initial[key] = defaultValueForType(spec.type);
    }
  }
  parameterValues[action.name] = initial;
}

const filteredActions = computed<UnifiedActionEntry[]>(() => {
  const prefix = `${props.handlerName}.`;
  return props.capabilities.actions.filter(a => a.name.startsWith(prefix));
});

const groupedActions = computed<ActionGroup[]>(() => {
  const groups = new Map<string, UnifiedActionEntry[]>();
  for (const action of filteredActions.value) {
    const prefix = action.name.split('.')[0] ?? '';
    const list = groups.get(prefix);
    if (list) {
      list.push(action);
    } else {
      groups.set(prefix, [action]);
    }
  }
  const result: ActionGroup[] = [];
  for (const [prefix, actions] of groups) {
    result.push({ handlerPrefix: prefix, actions });
  }
  return result;
});

watch(
  () => filteredActions.value,
  actions => {
    for (const action of actions) {
      ensureInitialized(action);
    }
  },
  { immediate: true },
);

const hasParameters = (action: UnifiedActionEntry): boolean =>
  Object.keys(action.parameters).length > 0;

const getLocalName = (fullName: string): string => {
  const dotIdx = fullName.indexOf('.');
  return dotIdx >= 0 ? fullName.substring(dotIdx + 1) : fullName;
};

async function handleExecute(action: UnifiedActionEntry): Promise<void> {
  if (submitting.value === action.name) return;
  submitting.value = action.name;
  feedback.value = null;

  ensureInitialized(action);
  const params: Record<string, unknown> = {};
  for (const key of Object.keys(action.parameters)) {
    params[key] = parameterValues[action.name][key];
  }

  try {
    const response = await maibotApi.triggerAction({
      action: { name: action.name, parameters: params },
    });
    const data = response.data;
    if (data.success && data.intent_id) {
      feedback.value = {
        type: 'success',
        message: `intent 已提交: ${data.intent_id}`,
      };
    } else {
      feedback.value = {
        type: 'error',
        message: data.error ?? data.message ?? '提交失败',
      };
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '';
    feedback.value = { type: 'error', message: msg || '提交失败' };
  } finally {
    submitting.value = null;
  }
}

function clearFeedback(): void {
  feedback.value = null;
}

function handleRetry(): void {
  emit('retry');
}
</script>

<style scoped>
.capabilities-panel {
  font-size: 12px;
  color: var(--text-primary);
}

.state-container {
  padding: var(--spacing-sm) 0;
}

.error-state {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--spacing-xs);
}

.retry-btn {
  margin-top: var(--spacing-xs);
}

.feedback-alert {
  margin-bottom: var(--spacing-sm);
}

.feedback-alert :deep(.el-alert__title) {
  font-size: 12px;
}

.handler-collapse {
  border: none;
}

.handler-collapse :deep(.el-collapse-item__header) {
  padding: 0 var(--spacing-xs);
  border-bottom-color: var(--border-color-light);
  background: transparent;
  font-size: 12px;
  height: 32px;
  line-height: 32px;
}

.handler-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}

.handler-collapse :deep(.el-collapse-item__content) {
  padding: var(--spacing-xs) 0;
  background: transparent;
}

.handler-title {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-xs);
}

.handler-tag {
  font-family: var(--font-mono);
  font-size: 11px;
}

.handler-count {
  font-size: 10px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
}

.action-list {
  display: flex;
  flex-direction: column;
}

.action-row {
  padding: var(--spacing-sm) 0;
  border-bottom: 1px solid var(--border-color-light);
}

.action-row:last-child {
  border-bottom: none;
}

.action-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  margin-bottom: var(--spacing-xs);
  flex-wrap: wrap;
}

.action-local-name {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  color: var(--color-primary);
  background: var(--bg-hover);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.action-description {
  font-size: 11px;
  color: var(--text-secondary);
  flex: 1;
  min-width: 0;
  line-height: 1.4;
}

.param-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: var(--spacing-xs);
  padding-left: var(--spacing-xs);
}

.param-row {
  display: grid;
  grid-template-columns: 110px 1fr;
  align-items: center;
  gap: var(--spacing-sm);
}

.param-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  min-width: 0;
}

.param-key {
  font-family: var(--font-mono);
  color: var(--text-primary);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.required-mark {
  color: var(--color-danger);
  font-weight: 700;
  font-size: 12px;
  cursor: help;
  flex-shrink: 0;
}

.param-help-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-hover);
  border-radius: 50%;
  cursor: help;
  flex-shrink: 0;
}

.param-control {
  min-width: 0;
}

.param-number {
  width: 100%;
}

.param-number :deep(.el-input-number) {
  width: 100%;
}

.action-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: var(--spacing-xs);
  padding-top: var(--spacing-xs);
  padding-left: var(--spacing-xs);
}

.action-footer .el-button {
  font-size: 11px;
}
</style>
