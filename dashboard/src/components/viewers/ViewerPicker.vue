<template>
  <el-select
    :model-value="modelValue ?? undefined"
    class="viewer-picker"
    filterable
    remote
    clearable
    :remote-method="search"
    :loading="loading"
    :placeholder="placeholder"
    @update:model-value="onSelect"
  >
    <el-option
      v-for="item in options"
      :key="item.viewer_id"
      :label="optionLabel(item)"
      :value="item.viewer_id"
    >
      <span class="option-name">{{ item.user_name || '（未留名）' }}</span>
      <span class="option-meta mono"
        >#{{ item.viewer_id }} · {{ item.user_id || '无外部 ID' }}</span
      >
    </el-option>
  </el-select>
</template>

<script setup lang="ts">
/**
 * 观众选择器 —— 按昵称或外部 ID 远程搜索，选出一位观众（取内部观众 ID）
 *
 * 用于"改选并入哪位老观众""把这位观众并入另一位"：输入时 250ms 防抖查
 * GET /viewers?search=，结果里排除 ``excludeId``（不能并入自己）。
 */
import { ref } from 'vue';
import { viewersApi } from '@/api';
import type { ViewerListItem } from '@/types';

const props = withDefaults(
  defineProps<{
    modelValue: number | null;
    /** 不出现在候选里的观众（通常是当前要并入别人的这一位） */
    excludeId?: number | null;
    placeholder?: string;
  }>(),
  { excludeId: null, placeholder: '搜索昵称或外部 ID 选择观众' },
);

const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>();

const options = ref<ViewerListItem[]>([]);
const loading = ref(false);
let timer: ReturnType<typeof setTimeout> | null = null;

function optionLabel(item: ViewerListItem): string {
  return `${item.user_name || '（未留名）'}（#${item.viewer_id}）`;
}

function search(query: string): void {
  if (timer) clearTimeout(timer);
  const keyword = query.trim();
  if (!keyword) {
    options.value = [];
    return;
  }
  timer = setTimeout(async () => {
    loading.value = true;
    try {
      const response = await viewersApi.list({
        search: keyword,
        order_by: 'last_active_ms',
        limit: 20,
      });
      options.value = response.data.items.filter(item => item.viewer_id !== props.excludeId);
    } catch {
      // 搜索失败只清空候选，不打断用户继续输入
      options.value = [];
    } finally {
      loading.value = false;
    }
  }, 250);
}

function onSelect(value: number | undefined | ''): void {
  emit('update:modelValue', typeof value === 'number' ? value : null);
}
</script>

<style scoped>
.viewer-picker {
  width: 280px;
}

.option-name {
  margin-right: var(--spacing-sm);
  color: var(--text-primary);
}

.option-meta {
  font-size: 11px;
  color: var(--text-placeholder);
}
</style>
