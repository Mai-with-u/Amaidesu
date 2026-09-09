<template>
  <div class="dict-editor">
    <div class="dict-items">
      <div v-for="(entry, index) in entries" :key="index" class="dict-row">
        <el-input
          v-model="entry.key"
          size="small"
          placeholder="键"
          class="dict-key-input"
          @input="handleUpdate"
        />
        <el-input
          v-model="entry.display"
          size="small"
          placeholder="值"
          class="dict-value-input"
          @input="handleValueEdit(index)"
        />
        <el-button type="danger" size="small" text :icon="Delete" @click="removeRow(index)" />
      </div>
    </div>
    <div class="dict-actions">
      <el-button type="primary" size="small" :icon="Plus" @click="addRow"> 添加键值对 </el-button>
      <span v-if="entries.length === 0" class="empty-hint">暂无键值对</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { Plus, Delete } from '@element-plus/icons-vue';

/**
 * 单条编辑项：
 * - key：用户输入的字典键（始终为字符串）
 * - display：编辑框展示的字符串；非字符串值通过 JSON.stringify 进入，编辑后
 *   通过 JSON.parse 回填；解析失败的纯文本保持为字符串
 * - value：回写到父组件的实际值（unknown，保留原类型）
 */
interface KeyValueEntry {
  key: string;
  display: string;
  value: unknown;
}

const props = defineProps<{
  modelValue: unknown;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: unknown];
  change: [];
}>();

/** 渲染时非字符串值通过 JSON 展示给用户。 */
function valueToDisplay(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

/** 编辑时尝试把用户输入解析回原类型；空字符串或解析失败时回退为字符串。 */
function displayToValue(display: string): unknown {
  if (display === '') return '';
  try {
    return JSON.parse(display);
  } catch {
    return display;
  }
}

/** 把外部 dict 转成内部编辑项：保留原值类型，仅生成展示串。 */
function dictToEntries(dict: unknown): KeyValueEntry[] {
  if (!dict || typeof dict !== 'object' || Array.isArray(dict)) return [];
  return Object.entries(dict).map(([k, v]) => ({
    key: k,
    display: valueToDisplay(v),
    value: v,
  }));
}

/** 把内部编辑项转回父组件需要的 dict：空键被过滤。 */
function entriesToDict(entries: KeyValueEntry[]): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const e of entries) {
    if (e.key) result[e.key] = e.value;
  }
  return result;
}

const entries = ref<KeyValueEntry[]>([]);
const _lastSyncedHash = ref('');

/**
 * 用"按 key 排序后的 JSON"做稳定指纹，避免外部值顺序差异触发误重置；
 * 对称用作「自己的 emit 回环」识别——外部回传和我们刚发出去的字典完全一致时跳过重置。
 */
function canonicalHash(obj: Record<string, unknown>): string {
  const sorted: Record<string, unknown> = {};
  for (const k of Object.keys(obj).sort()) sorted[k] = obj[k];
  return JSON.stringify(sorted);
}

watch(
  () => props.modelValue,
  newVal => {
    const incoming = canonicalHash(entriesToDict(dictToEntries(newVal)));
    if (incoming === _lastSyncedHash.value) return; // 自己的 emit 回环
    entries.value = dictToEntries(newVal);
    _lastSyncedHash.value = incoming;
  },
  { immediate: true },
);

/** 用户在编辑框里修改值：解析展示串回填到 value，再触发一次同步。 */
function handleValueEdit(index: number) {
  const entry = entries.value[index];
  if (!entry) return;
  entry.value = displayToValue(entry.display);
  syncToParent();
}

function syncToParent() {
  const newHash = canonicalHash(entriesToDict(entries.value));
  if (newHash === _lastSyncedHash.value) return; // 无变化（包含用户编辑但解析后类型未变的情形）
  _lastSyncedHash.value = newHash;
  emit('update:modelValue', entriesToDict(entries.value));
  emit('change');
}

function addRow() {
  entries.value.push({ key: '', display: '', value: '' });
  // 不立即同步：空 key 会被 entriesToDict 过滤掉，导致循环清除
  // handleUpdate 由用户输入 key/value 时触发
}

function removeRow(index: number) {
  entries.value.splice(index, 1);
  syncToParent();
}

function handleUpdate() {
  syncToParent();
}
</script>

<style scoped>
.dict-editor {
  background: var(--bg-hover);
  border-radius: var(--radius-sm);
  padding: var(--spacing-sm);
}

.dict-items {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  margin-bottom: var(--spacing-sm);
}

.dict-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
}

.dict-key-input {
  flex: 0 0 160px;
}

.dict-value-input {
  flex: 1;
}

.dict-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.empty-hint {
  font-size: 12px;
  color: var(--text-placeholder);
}
</style>
