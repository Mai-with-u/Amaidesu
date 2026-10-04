<!--
  ContextBreakdownCard - 上下文窗口占用解剖卡
  "上下文水位"悬停弹出的明细内容：头部水位 + 分段堆叠条 + 类目行
  （system / messages / tools），大段可展开条目明细，剩余空间灰行置底。
  数据口径见 src/modules/llm/context_meter.py：总数以 API 回报为准，
  分段为校准后估算。
-->
<template>
  <div class="cbc">
    <template v-if="breakdown">
      <div class="cbc-header">
        <span class="cbc-title">Context window</span>
        <span class="cbc-total" :class="{ warn: usageRatio > 0.8 }">
          {{ compactNumber(breakdown.api_prompt_tokens) }} /
          {{ compactNumber(breakdown.context_window) }} ({{ percentText(usageRatio) }})
        </span>
      </div>
      <div class="cbc-caption">
        来自最近一次 {{ breakdown.profile_name || '未知用途' }} 调用{{
          breakdown.calibrated ? '' : '（上游未回报总量，展示原始估算）'
        }}
      </div>

      <div class="cbc-bar" role="img" aria-label="上下文窗口分段占用条">
        <div
          v-for="seg in barSegments"
          :key="seg.key"
          class="cbc-bar-seg"
          :style="{ flexGrow: seg.value, backgroundColor: seg.color }"
        ></div>
      </div>

      <ul class="cbc-rows">
        <li v-for="row in sectionRows" :key="row.key" class="cbc-row">
          <div
            class="cbc-row-main"
            :class="{ expandable: row.items.length > 0 }"
            @click="toggleExpand(row.key)"
          >
            <span class="cbc-caret" :class="{ open: expanded.has(row.key) }">
              {{ row.items.length > 0 ? '▸' : '' }}
            </span>
            <span class="cbc-dot" :style="{ backgroundColor: row.color }"></span>
            <span class="cbc-name">
              {{ row.label }}
              <span v-if="row.count > 0" class="cbc-count">{{ row.count }}</span>
            </span>
            <span class="cbc-tokens">{{ compactNumber(row.tokens) }}</span>
            <span class="cbc-percent">{{
              percentText(row.tokens / breakdown.context_window)
            }}</span>
          </div>
          <ul v-if="expanded.has(row.key)" class="cbc-subrows">
            <li v-for="item in row.items" :key="item.name" class="cbc-subrow">
              <span class="cbc-subname" :title="item.name">{{ item.name }}</span>
              <span class="cbc-tokens">{{ compactNumber(item.tokens) }}</span>
            </li>
          </ul>
        </li>
        <li v-if="breakdown.free_tokens !== null" class="cbc-row">
          <div class="cbc-row-main">
            <span class="cbc-caret"></span>
            <span class="cbc-dot" :style="{ backgroundColor: FREE_COLOR }"></span>
            <span class="cbc-name">Free space</span>
            <span class="cbc-tokens">{{ compactNumber(breakdown.free_tokens) }}</span>
            <span class="cbc-percent">
              {{ percentText(breakdown.free_tokens / breakdown.context_window) }}
            </span>
          </div>
        </li>
      </ul>
    </template>
    <div v-else class="cbc-empty">
      {{ loading ? '加载中…' : '暂无上下文分段数据（需一次成功调用后生成）' }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { LLMContextBreakdown, LLMContextSection } from '@/types';
import { compactNumber } from '@/utils/chartFormat';

/** 分段固定配色（与堆叠条、类目行色点共用同一映射） */
const SECTION_META: Record<string, { label: string; color: string }> = {
  system: { label: 'System prompt', color: '#409eff' },
  messages: { label: 'Messages', color: '#67c23a' },
  tools: { label: 'Tools', color: '#e6a23c' },
};
const FREE_COLOR = '#c0c4cc';

const props = defineProps<{
  breakdown: LLMContextBreakdown | null;
  loading?: boolean;
}>();

/** 展开状态在换模型（数据源变化）时重置，避免展开残留到另一模型的分段上 */
const expanded = ref<Set<string>>(new Set());
watch(
  () => props.breakdown?.model_name,
  () => {
    expanded.value = new Set();
  },
);

const usageRatio = computed(() => {
  if (!props.breakdown || props.breakdown.context_window <= 0) {
    return 0;
  }
  return props.breakdown.api_prompt_tokens / props.breakdown.context_window;
});

const barSegments = computed(() => {
  const breakdown = props.breakdown;
  if (!breakdown || breakdown.context_window <= 0) {
    return [];
  }
  const segments = visibleSections.value.map(section => ({
    key: section.key,
    value: Math.max(section.tokens, 0.0001),
    color: SECTION_META[section.key]?.color ?? FREE_COLOR,
  }));
  if (breakdown.free_tokens !== null && breakdown.free_tokens > 0) {
    segments.push({ key: 'free', value: breakdown.free_tokens, color: FREE_COLOR });
  }
  return segments;
});

/** 只渲染有 meta 的已知分段（后端新增分段键时前端不盲目上色） */
const visibleSections = computed<LLMContextSection[]>(() => {
  const breakdown = props.breakdown;
  if (!breakdown) {
    return [];
  }
  return breakdown.sections.filter(section => section.key in SECTION_META);
});

const sectionRows = computed(() =>
  visibleSections.value.map(section => ({
    ...section,
    label: SECTION_META[section.key]?.label ?? section.key,
    color: SECTION_META[section.key]?.color ?? FREE_COLOR,
  })),
);

function toggleExpand(key: string): void {
  const next = new Set(expanded.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
  }
  expanded.value = next;
}

function percentText(ratio: number): string {
  if (!Number.isFinite(ratio) || props.breakdown?.context_window === 0) {
    return '—';
  }
  return `${(ratio * 100).toFixed(1)}%`;
}
</script>

<style scoped>
.cbc {
  width: 320px;
  font-size: 12px;
  color: var(--el-text-color-primary);
}

.cbc-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 2px;
}

.cbc-title {
  font-weight: 600;
}

.cbc-total {
  font-variant-numeric: tabular-nums;
}

.cbc-total.warn {
  color: var(--el-color-danger);
}

.cbc-caption {
  margin-bottom: 8px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.cbc-bar {
  display: flex;
  height: 8px;
  margin-bottom: 8px;
  overflow: hidden;
  border-radius: 4px;
  background: var(--el-fill-color-light);
}

.cbc-bar-seg {
  min-width: 2px;
}

.cbc-bar-seg + .cbc-bar-seg {
  border-left: 1px solid #fff;
}

.cbc-rows {
  margin: 0;
  padding: 0;
  list-style: none;
}

.cbc-row-main {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
  border-radius: 4px;
  user-select: none;
}

.cbc-row-main.expandable {
  cursor: pointer;
}

.cbc-row-main.expandable:hover {
  background: var(--el-fill-color-light);
}

.cbc-caret {
  width: 10px;
  flex: none;
  color: var(--el-text-color-secondary);
  transition: transform 0.15s;
}

.cbc-caret.open {
  transform: rotate(90deg);
}

.cbc-dot {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 2px;
}

.cbc-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cbc-count {
  margin-left: 4px;
  color: var(--el-text-color-secondary);
}

.cbc-tokens {
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-regular);
}

.cbc-percent {
  width: 52px;
  flex: none;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}

.cbc-subrows {
  margin: 0 0 4px 16px;
  padding: 0;
  list-style: none;
}

.cbc-subrow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 0 2px 12px;
  color: var(--el-text-color-secondary);
}

.cbc-subname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cbc-empty {
  padding: 12px 0;
  color: var(--el-text-color-secondary);
}
</style>
