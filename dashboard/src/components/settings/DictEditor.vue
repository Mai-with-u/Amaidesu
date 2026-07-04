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
          v-model="entry.value"
          size="small"
          placeholder="值"
          class="dict-value-input"
          @input="handleUpdate"
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

interface KeyValueEntry {
  key: string;
  value: string;
}

const props = defineProps<{
  modelValue: unknown;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: unknown];
  change: [];
}>();

function dictToEntries(dict: unknown): KeyValueEntry[] {
  if (!dict || typeof dict !== 'object') return [];
  return Object.entries(dict).map(([k, v]) => ({
    key: k,
    value: typeof v === 'string' ? v : JSON.stringify(v),
  }));
}

function entriesToDict(entries: KeyValueEntry[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const e of entries) {
    if (e.key) result[e.key] = e.value;
  }
  return result;
}

const entries = ref<KeyValueEntry[]>([]);
const _lastSyncedHash = ref('');
const _externalHash = ref('');

/** 对 entries 做稳定序列化，用于比对是否是自己的更新 */
function entriesHash(): string {
  const ordered = entries.value
    .filter(e => e.key)
    .sort((a, b) => a.key.localeCompare(b.key));
  return JSON.stringify(ordered);
}

watch(
  () => props.modelValue,
  newVal => {
    const external = JSON.stringify(dictToEntries(newVal));
    if (external === _lastSyncedHash.value) return; // 自己的 emit 回环
    _externalHash.value = external;
    entries.value = dictToEntries(newVal);
  },
  { immediate: true },
);

/** 仅当外部值与我们产生的新值不同时才重置（版本号） */
function syncToParent() {
  const newHash = entriesHash();
  if (newHash === _lastSyncedHash.value) return; // 无变化
  _lastSyncedHash.value = newHash;
  emit('update:modelValue', entriesToDict(entries.value));
  emit('change');
}

function addRow() {
  entries.value.push({ key: '', value: '' });
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
