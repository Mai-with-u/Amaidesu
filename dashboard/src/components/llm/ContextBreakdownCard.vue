<!--
  ContextBreakdownCard - 上下文窗口占用面板（版式对齐 Claude Desktop 的 Context window 面板）
  头部总水位 → 分段堆叠细条 → 分段行（色块 / 名称 / token / 占比）→ 剩余空间 →
  可折叠明细组（MCP 工具、内置工具、技能、对话消息逐项 token）→
  用量区（模型所属平台的余额或套餐限额，平台查不到时整块隐藏）。
  数据口径见 src/modules/llm/context_meter.py：总数以 API 回报为准，分段为校准后估算。
-->
<template>
  <div class="cbc">
    <template v-if="breakdown">
      <div class="cbc-header">
        <span class="cbc-title">Context window</span>
        <span class="cbc-total" :class="{ warn: usageRatio > 0.8 }">
          {{ shortNumber(breakdown.api_prompt_tokens) }}
          <template v-if="breakdown.context_window > 0">
            / {{ shortNumber(breakdown.context_window) }} ({{ Math.round(usageRatio * 100) }}%)
          </template>
        </span>
      </div>

      <!-- 悬停某一分段行时，条上只亮这一段，其余压暗，方便对位 -->
      <div class="cbc-bar" role="img" aria-label="上下文窗口分段占用条">
        <div
          v-for="seg in barSegments"
          :key="seg.key"
          class="cbc-bar-seg"
          :class="{ dim: hoverKey !== null && hoverKey !== seg.key }"
          :style="{ flexGrow: seg.value, backgroundColor: seg.color }"
        ></div>
      </div>

      <ul class="cbc-rows">
        <li
          v-for="(row, index) in sectionRows"
          :key="row.key"
          class="cbc-row"
          :style="{ '--i': index }"
          @mouseenter="hoverKey = row.key"
          @mouseleave="hoverKey = null"
        >
          <span class="cbc-swatch" :style="{ backgroundColor: row.color }"></span>
          <span class="cbc-name">{{ row.label }}</span>
          <span class="cbc-tokens">{{ shortNumber(row.tokens) }}</span>
          <span class="cbc-percent">{{ percentText(row.tokens) }}</span>
        </li>
        <li
          v-if="breakdown.free_tokens !== null"
          class="cbc-row"
          :style="{ '--i': sectionRows.length }"
          @mouseenter="hoverKey = 'free'"
          @mouseleave="hoverKey = null"
        >
          <span class="cbc-swatch cbc-swatch--free"></span>
          <span class="cbc-name">Free space</span>
          <span class="cbc-tokens">{{ shortNumber(breakdown.free_tokens) }}</span>
          <span class="cbc-percent">{{ percentText(breakdown.free_tokens) }}</span>
        </li>
      </ul>

      <div v-if="detailGroups.length > 0" class="cbc-groups">
        <section
          v-for="group in detailGroups"
          :key="group.key"
          class="cbc-group"
          :class="{ collapsed: !expanded.has(group.key) }"
        >
          <div
            class="cbc-group-head"
            @click="toggleExpanded(group.key)"
            @mouseenter="hoverKey = group.key"
            @mouseleave="hoverKey = null"
          >
            <svg class="cbc-chevron" viewBox="0 0 10 10" aria-hidden="true">
              <path d="M2 3.5 5 6.5 8 3.5" />
            </svg>
            <span class="cbc-name">{{ group.label }}</span>
            <span class="cbc-tokens">{{ shortNumber(group.tokens) }}</span>
            <span class="cbc-count">{{ group.count }}</span>
          </div>
          <!-- 折叠用 grid 行高 0fr↔1fr 过渡，内容高度不定也能平滑收放 -->
          <div class="cbc-items-wrap">
            <div class="cbc-items-clip">
              <ul class="cbc-items">
                <li v-for="item in group.items" :key="item.name" class="cbc-item">
                  <span class="cbc-item-name" :title="item.name">{{ item.name }}</span>
                  <span class="cbc-tokens">{{ shortNumber(item.tokens) }}</span>
                </li>
              </ul>
            </div>
          </div>
        </section>
      </div>

      <div class="cbc-caption">
        最近一次 {{ breakdown.profile_name || '未知用途' }} 调用{{
          breakdown.calibrated ? '' : ' · 上游未回报总量，展示原始估算'
        }}
      </div>

      <!-- 用量区：平台能查到余额或套餐额度时才出现，版式对齐 Claude 面板底部的用量限额 -->
      <section v-if="quota && quota.windows.length > 0" class="cbc-usage">
        <div class="cbc-usage-head">
          <span class="cbc-title">Usage</span>
          <span class="cbc-usage-plan">{{ quota.platform_label }} · {{ quota.plan_label }}</span>
        </div>
        <div v-for="entry in quota.windows" :key="entry.label" class="cbc-quota">
          <div class="cbc-quota-line">
            <span class="cbc-name">{{ entry.label }}</span>
            <span class="cbc-quota-meta">{{ quotaMeta(entry) }}</span>
            <span class="cbc-quota-value">{{ quotaValue(entry) }}</span>
          </div>
          <div v-if="entry.used_percent !== null" class="cbc-quota-track">
            <div
              class="cbc-quota-fill"
              :class="`is-${quotaLevel(entry.used_percent)}`"
              :style="{ width: `${entry.used_percent}%` }"
            ></div>
          </div>
        </div>
      </section>
    </template>
    <div v-else class="cbc-empty">
      {{ loading ? '加载中…' : '暂无上下文分段数据（需一次成功调用后生成）' }}
    </div>
  </div>
</template>

<script lang="ts">
/**
 * 额度查询的模块级缓存：面板每次弹出都会重新挂载，60s 内同一模型反复悬停
 * 直接复用上次结果，Usage 区不闪现；同一模型的并发请求合并为一次。查询失败
 * 按"无额度"处理并同样缓存。缓存放模块作用域而非组件实例：面板每次弹出
 * 都重新挂载，实例字段会让缓存随挂载失效。
 */
import { llmApi } from '@/api';
import type { LLMProviderQuota } from '@/types';

const CACHE_TTL_MS = 60_000;
const cache = new Map<string, { at: number; data: LLMProviderQuota | null }>();
const inflight = new Map<string, Promise<LLMProviderQuota | null>>();

function cachedQuota(modelName: string): LLMProviderQuota | null | undefined {
  const hit = cache.get(modelName);
  return hit && Date.now() - hit.at < CACHE_TTL_MS ? hit.data : undefined;
}

function fetchQuota(modelName: string): Promise<LLMProviderQuota | null> {
  const pending = inflight.get(modelName);
  if (pending) return pending;
  const request = llmApi
    .getProviderQuota(modelName)
    .then(response => response.data ?? null)
    .catch((error: unknown) => {
      // 额度属增强信息：失败只隐藏 Usage 区，不打断上下文明细
      console.error('Failed to fetch provider quota:', error);
      return null;
    })
    .then(data => {
      cache.set(modelName, { at: Date.now(), data });
      return data;
    })
    .finally(() => inflight.delete(modelName));
  inflight.set(modelName, request);
  return request;
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { LLMContextBreakdown, LLMContextSection, LLMQuotaWindow } from '@/types';
import { useNowTick } from '@/composables/useNowTick';
import { formatQuotaAmount, formatResetIn, quotaLevel } from '@/utils/providerQuota';

/**
 * 分段展示名与配色（顺序即行序，与 Claude Desktop 面板一致）。
 * tools 是较早落库记录的单一工具段，保留以便旧记录照常显示。
 */
const SECTION_META: Record<string, { label: string; color: string }> = {
  messages: { label: 'Messages', color: '#3b82f6' },
  mcp_tools: { label: 'MCP tools', color: '#e2603f' },
  system_tools: { label: 'System tools', color: '#1f9d6b' },
  tools: { label: 'Tools', color: '#d99a1e' },
  skills: { label: 'Skills', color: '#a3a3a3' },
  system: { label: 'System prompt', color: '#8a8a8a' },
};
const SECTION_ORDER = Object.keys(SECTION_META);
/** 明细组：只展开逐项有意义的分段（系统提示词只有一项，不单列） */
const GROUP_KEYS = ['mcp_tools', 'system_tools', 'tools', 'skills', 'messages'];

const props = defineProps<{
  breakdown: LLMContextBreakdown | null;
  loading?: boolean;
}>();

/**
 * 记录用户手动展开的明细组。明细逐项列表较长，弹出面板时默认全部折叠，
 * 只看分段总览；点开某组才展开。换模型（数据源变化）时重置回全部折叠。
 */
const expanded = ref<Set<string>>(new Set());
/** 鼠标所在的分段（含 free），用于在分段条上高亮对应色块 */
const hoverKey = ref<string | null>(null);
/** 面板对应模型所属平台的额度；平台不支持或查询失败为 null，用量区随之隐藏 */
const quota = ref<LLMProviderQuota | null>(null);
watch(
  computed(() => props.breakdown?.model_name),
  async name => {
    if (!name) {
      quota.value = null;
      return;
    }
    // 缓存命中直接同步填充；未命中先清空，等请求回来再显示
    const hit = cachedQuota(name);
    if (hit !== undefined) {
      quota.value = hit;
      return;
    }
    quota.value = null;
    const data = await fetchQuota(name);
    // 等待期间换了模型时丢弃旧结果
    if (props.breakdown?.model_name === name) {
      quota.value = data;
    }
  },
  { immediate: true },
);
/** 驱动重置倒计时逐秒刷新 */
const now = useNowTick();
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

/** 只渲染有展示名的已知分段（后端新增分段键时前端不盲目上色），占用为 0 的分段不占行 */
const visibleSections = computed<LLMContextSection[]>(() => {
  const breakdown = props.breakdown;
  if (!breakdown) {
    return [];
  }
  return breakdown.sections
    .filter(section => section.key in SECTION_META && section.tokens > 0)
    .sort((a, b) => SECTION_ORDER.indexOf(a.key) - SECTION_ORDER.indexOf(b.key));
});

const sectionRows = computed(() =>
  visibleSections.value.map(section => ({
    ...section,
    label: SECTION_META[section.key].label,
    color: SECTION_META[section.key].color,
  })),
);

const barSegments = computed(() => {
  const breakdown = props.breakdown;
  if (!breakdown) {
    return [];
  }
  const segments = sectionRows.value.map(row => ({
    key: row.key,
    value: row.tokens,
    color: row.color,
  }));
  if (breakdown.free_tokens !== null && breakdown.free_tokens > 0) {
    segments.push({ key: 'free', value: breakdown.free_tokens, color: 'var(--cbc-free-color)' });
  }
  return segments;
});

const detailGroups = computed(() =>
  GROUP_KEYS.map(key => visibleSections.value.find(section => section.key === key))
    .filter((section): section is LLMContextSection => !!section && section.items.length > 0)
    .map(section => ({
      key: section.key,
      label: SECTION_META[section.key].label,
      tokens: section.tokens,
      count: section.count,
      items: [...section.items].sort((a, b) => b.tokens - a.tokens),
    })),
);

function toggleExpanded(key: string): void {
  const next = new Set(expanded.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
  }
  expanded.value = next;
}

/** 紧凑数字：1.0M 写成 1M，与 Claude 面板一致 */
function shortNumber(value: number): string {
  const abs = Math.abs(value);
  const scaled =
    abs >= 1_000_000
      ? `${(value / 1_000_000).toFixed(1)}M`
      : abs >= 1_000
        ? `${(value / 1_000).toFixed(1)}k`
        : `${Math.round(value)}`;
  return scaled.replace('.0', '');
}

/** 用量行中间的次要信息：限额窗口写重置倒计时，带总额的余额写已用比例 */
function quotaMeta(entry: LLMQuotaWindow): string {
  if (entry.kind === 'limit') {
    if (entry.resets_at_ms === null) return '';
    const left = formatResetIn(entry.resets_at_ms, now.value);
    return left === 'soon' ? 'Resets soon' : `Resets in ${left}`;
  }
  return entry.used_percent !== null ? `${Math.round(entry.used_percent)}% used` : '';
}

/** 用量行右侧的主数值：限额窗口写已用百分比，余额写剩余金额 */
function quotaValue(entry: LLMQuotaWindow): string {
  if (entry.kind === 'balance') {
    return entry.amount !== null ? formatQuotaAmount(entry.amount, entry.currency) : '—';
  }
  return entry.used_percent !== null ? `${Math.round(entry.used_percent)}%` : '—';
}

function percentText(tokens: number): string {
  const window = props.breakdown?.context_window ?? 0;
  if (window <= 0 || !Number.isFinite(tokens)) {
    return '—';
  }
  return `${((tokens / window) * 100).toFixed(1)}%`;
}
</script>

<style scoped>
/* 容器内边距接住两类"伸出"，再用等量负外边距抵消，版面位置不变：
   - 两侧 6px：行悬停底色用 -6px 外边距伸出文字两侧；
   - 底部 4px：末块用量区的上浮动画起点在下方 4px。
   否则它们超出容器，overflow-y:auto 会闪出横向或纵向滚动条；
   内容真超过 70vh 时的纵向滚动条照常保留 */
.cbc {
  --cbc-free-color: var(--el-fill-color-darker);
  max-height: 70vh;
  margin: 0 -6px -4px;
  padding: 0 6px 4px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-primary);
}

.cbc-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.cbc-title {
  font-weight: 600;
}

.cbc-total {
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  transition: color var(--transition-normal);
}

.cbc-total.warn {
  color: var(--el-color-danger);
}

/* 分段条：每次弹出从左向右展开；数据刷新时各段宽度平滑过渡 */
.cbc-bar {
  display: flex;
  gap: 2px;
  height: 6px;
  margin-bottom: 12px;
  overflow: hidden;
  border-radius: 3px;
  animation: cbc-bar-reveal 0.6s var(--ease-out);
}

@keyframes cbc-bar-reveal {
  from {
    clip-path: inset(0 100% 0 0);
  }
  to {
    clip-path: inset(0 0 0 0);
  }
}

.cbc-bar-seg {
  min-width: 2px;
  border-radius: 1px;
  transition:
    flex-grow 0.5s var(--ease-out),
    opacity var(--transition-fast);
}

.cbc-bar-seg.dim {
  opacity: 0.25;
}

.cbc-rows,
.cbc-items {
  margin: 0;
  padding: 0;
  list-style: none;
}

.cbc-row,
.cbc-group-head,
.cbc-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 分段行在分段条展开后逐条淡入，间隔 30ms */
.cbc-row {
  margin: 0 -6px;
  padding: 3px 6px;
  border-radius: var(--radius-sm);
  transition: background-color var(--transition-fast);
  animation: cbc-rise-in 0.32s var(--ease-out) backwards;
  animation-delay: calc(var(--i, 0) * 30ms + 80ms);
}

.cbc-row:hover {
  background: var(--el-fill-color-light);
}

@keyframes cbc-rise-in {
  from {
    opacity: 0;
    translate: 0 4px;
  }
}

.cbc-swatch {
  width: 10px;
  height: 10px;
  flex: none;
  border-radius: 3px;
  transition: transform 0.25s var(--ease-spring);
}

.cbc-row:hover .cbc-swatch {
  transform: scale(1.25);
}

.cbc-swatch--free {
  background: var(--cbc-free-color);
}

.cbc-name {
  flex: 1;
  overflow: hidden;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cbc-tokens {
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.cbc-percent {
  width: 48px;
  flex: none;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

/* 明细组条数做成小胶囊，与 token 数区分开 */
.cbc-count {
  min-width: 22px;
  flex: none;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  line-height: 18px;
  text-align: center;
}

.cbc-groups {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
  animation: cbc-rise-in 0.32s var(--ease-out) 0.2s backwards;
}

.cbc-group + .cbc-group {
  margin-top: 4px;
}

.cbc-group-head {
  margin: 0 -6px;
  padding: 3px 6px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  user-select: none;
  transition: background-color var(--transition-fast);
}

.cbc-group-head:hover {
  background: var(--el-fill-color-light);
}

.cbc-chevron {
  width: 10px;
  height: 10px;
  flex: none;
  fill: none;
  stroke: var(--el-text-color-secondary);
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform 0.25s var(--ease-out);
}

.cbc-group.collapsed .cbc-chevron {
  transform: rotate(-90deg);
}

/* 折叠：外层 grid 行高 1fr→0fr，内层裁切，列表同时淡出 */
.cbc-items-wrap {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows 0.28s var(--ease-out);
}

.cbc-group.collapsed .cbc-items-wrap {
  grid-template-rows: 0fr;
}

.cbc-items-clip {
  min-height: 0;
  overflow: hidden;
}

.cbc-items {
  max-height: 168px;
  overflow-y: auto;
  padding: 2px 4px 2px 18px;
  transition: opacity 0.2s ease;
}

.cbc-group.collapsed .cbc-items {
  opacity: 0;
}

.cbc-item {
  padding: 1px 0;
  font-size: 12px;
}

.cbc-item-name {
  flex: 1;
  overflow: hidden;
  color: var(--el-text-color-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cbc-caption {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* 用量区：与上方脚注同样用细线分隔，随面板一起上浮淡入 */
.cbc-usage {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
  animation: cbc-rise-in 0.32s var(--ease-out) 0.24s backwards;
}

.cbc-usage-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.cbc-usage-plan,
.cbc-quota-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.cbc-quota + .cbc-quota {
  margin-top: 8px;
}

.cbc-quota-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.cbc-quota-meta {
  font-variant-numeric: tabular-nums;
}

.cbc-quota-value {
  min-width: 48px;
  flex: none;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

/* 用量条：底色同"剩余空间"，弹出时与分段条同款从左向右展开 */
.cbc-quota-track {
  height: 6px;
  margin-top: 4px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--cbc-free-color);
}

.cbc-quota-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--el-color-primary);
  transition: width 0.5s var(--ease-out);
  animation: cbc-bar-reveal 0.6s var(--ease-out);
}

.cbc-quota-fill.is-warning {
  background: var(--el-color-warning);
}

.cbc-quota-fill.is-danger {
  background: var(--el-color-danger);
}

.cbc-empty {
  padding: 8px 0;
  color: var(--el-text-color-secondary);
  text-align: center;
}
</style>
