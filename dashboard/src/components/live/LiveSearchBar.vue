<template>
  <div class="live-search">
    <el-icon class="search-glyph" aria-hidden="true"><Search /></el-icon>
    <input
      ref="inputRef"
      class="search-input"
      :value="query"
      type="text"
      :placeholder="placeholder"
      aria-label="搜索这场直播的内容"
      @input="onInput"
      @keydown.down.prevent="emit('next')"
      @keydown.up.prevent="emit('previous')"
      @keydown.enter.prevent="emit('confirm')"
      @keydown.esc.prevent="onEsc"
    />
    <span v-if="loading" class="search-state" role="status">检索中…</span>
    <template v-else-if="query.trim()">
      <span class="search-state mono">
        {{ hits.length ? `${Math.min(index + 1, hits.length)} / ${hits.length}` : '无命中' }}
      </span>
      <span v-if="scopeNote" class="search-scope" :title="scopeNote">{{ scopeNote }}</span>
    </template>
    <span class="grow" />
    <template v-if="query.trim()">
      <button
        type="button"
        class="search-nav"
        :disabled="!hits.length"
        title="上一条（Shift+F3）"
        aria-label="上一条命中"
        @click="emit('previous')"
      >
        <el-icon><ArrowUp /></el-icon>
      </button>
      <button
        type="button"
        class="search-nav"
        :disabled="!hits.length"
        title="下一条（F3）"
        aria-label="下一条命中"
        @click="emit('next')"
      >
        <el-icon><ArrowDown /></el-icon>
      </button>
    </template>
    <!-- 空查询时整个隐藏，不做成可见但禁用——否则看着像"关闭搜索条" -->
    <button
      v-if="query.trim()"
      type="button"
      class="search-nav"
      title="清空搜索（Esc）"
      aria-label="清空搜索"
      @click="emit('clear')"
    >
      <el-icon><Close /></el-icon>
    </button>

    <!-- 命中清单：主时间线不因搜索变形，这里给出全部命中，点一条跳到那一刻。
         顶部类别 chips 按桶过滤清单（≥2 个分类才出——单分类没有过滤可言）；
         传进来的 hits 已是页面过滤后的清单，chips 数字是全量口径 -->
    <div
      v-if="showList && hits.length"
      class="search-hits"
      role="listbox"
      aria-label="搜索命中清单"
    >
      <div v-if="categories.length > 1" class="hit-cats" role="group" aria-label="按分类过滤命中">
        <button
          type="button"
          class="hit-cat"
          :class="{ 'is-on': activeCategory === 'all' }"
          @click="emit('set-category', 'all')"
        >
          全部 {{ total }}
        </button>
        <button
          v-for="cat in categories"
          :key="cat.key"
          type="button"
          class="hit-cat"
          :class="{ 'is-on': activeCategory === cat.key }"
          @click="emit('set-category', cat.key)"
        >
          {{ cat.label }} {{ cat.count }}
        </button>
      </div>
      <button
        v-for="(hit, i) in hits"
        :key="`${hit.id}-${i}`"
        type="button"
        class="search-hit"
        :class="{ 'is-active': i === index }"
        role="option"
        :aria-selected="i === index"
        :data-hit-index="i"
        @click="emit('select', i)"
        @mousemove="emit('hover', i)"
      >
        <span class="hit-time mono">{{ formatTimeHMS(hit.tsMs) }}</span>
        <span class="hit-tag">{{ hit.tag }}</span>
        <span class="hit-actor" :title="hit.actor">{{ hit.actor || '—' }}</span>
        <span class="hit-text"
          ><template v-for="part in hit.parts" :key="`${part.text}:${part.hit}`">
            <mark v-if="part.hit">{{ part.text }}</mark>
            <span v-else>{{ part.text }}</span>
          </template>
        </span>
      </button>
      <p v-if="hasMore" class="search-hits-more">
        还有更早的命中没列出（已显示最近的 {{ hits.length }} 条）
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 直播时间线搜索条：关键词输入 + 命中清单。
 *
 * 布局约定：作为时间线区 stage-bar 下的一行常驻（挤在 stage-bar 里会盖住
 * 三档视图切换）；命中清单是浮层，不占布局高度。主时间线不因搜索隐藏或
 * 重排内容，命中行只在页面层加竖条标记——本组件只负责"找与选"，
 * "跳到那一刻"由页面按当前档位实现。
 *
 * 键盘：↑↓ 移动选中、Enter 跳到选中、Esc 关闭清单；/ 聚焦本输入框
 * （页面级快捷键经 defineExpose 的 focus 调用）。清单的选中项变化时
 * 自动滚进可视区。
 */
import { computed, nextTick, ref, watch } from 'vue';
import { ArrowDown, ArrowUp, Close, Search } from '@element-plus/icons-vue';
import { formatTimeHMS } from '@/utils/format';
import type { LiveHitCategory, SearchHit } from '@/utils/liveSearch';

const props = withDefaults(
  defineProps<{
    query: string;
    /** 过滤后的命中清单（分类过滤在页面层做，这里只展示） */
    hits: SearchHit[];
    index: number;
    loading: boolean;
    /** 检索范围说明（"整场找到 12 条" / "只搜到已加载的内容"） */
    scopeNote: string;
    /** 命中清单展开（页面控制：有命中即展开，Esc 收起） */
    showList: boolean;
    /** 服务端还有更早的命中没返回 */
    hasMore: boolean;
    /** 各分类的全量计数（chips 上的数字；空数组 = 该档不提供分类） */
    categories?: Array<{ key: LiveHitCategory; label: string; count: number }>;
    /** 当前选中的分类 */
    activeCategory?: 'all' | LiveHitCategory;
    placeholder?: string;
  }>(),
  {
    placeholder: '搜索这一场的内容：弹幕、发言、工具调用…',
    hasMore: false,
    categories: () => [],
    activeCategory: 'all',
  },
);

/** 全量命中数：各分类计数之和（"全部"chip 上显示） */
const total = computed(() => props.categories.reduce((sum, cat) => sum + cat.count, 0));

const emit = defineEmits<{
  'update:query': [value: string];
  /** 移到下一条命中（循环） */
  next: [];
  /** 移到上一条命中（循环） */
  previous: [];
  /** 跳到当前选中的命中 */
  confirm: [];
  /** 跳到指定序号的命中 */
  select: [index: number];
  /** 鼠标移到某条命中上：跟随改选中 */
  hover: [index: number];
  /** 清空搜索 */
  clear: [];
  /** Esc 在输入框按下：收起清单 */
  collapse: [];
  /** 切换分类过滤 */
  'set-category': [category: 'all' | LiveHitCategory];
}>();

const inputRef = ref<HTMLInputElement | null>(null);

function onInput(event: Event): void {
  emit('update:query', (event.target as HTMLInputElement).value);
}

/** Esc：清单开着先收清单，再清空归页面处理（输入框失焦由页面快捷键兜底） */
function onEsc(): void {
  if (props.showList && props.hits.length) {
    emit('collapse');
    return;
  }
  emit('clear');
}

/** 选中项变化时滚进清单可视区（键盘导航时列表可能比可视高度长） */
watch(
  () => props.index,
  async () => {
    await nextTick();
    inputRef.value
      ?.closest('.live-search')
      ?.querySelector(`[data-hit-index="${props.index}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  },
);

/** / 快捷键聚焦输入框（页面级 onConsoleKeydown 调用） */
function focus(): void {
  inputRef.value?.focus();
  inputRef.value?.select();
}

defineExpose({ focus });
</script>

<style scoped>
.live-search {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  border-bottom: 1px solid var(--border-color-light);
  background: var(--bg-card);
}

.search-glyph {
  color: var(--text-placeholder);
  font-size: 14px;
}

.search-input {
  flex: 1;
  min-width: 120px;
  max-width: 420px;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
  transition: border-color var(--transition-fast);
}

.search-input:focus {
  border-color: var(--color-primary);
}

.search-input::placeholder {
  color: var(--text-placeholder);
}

.search-state {
  flex: none;
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.search-scope {
  flex: none;
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--text-placeholder);
}

.grow {
  flex: 1;
}

.search-nav {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.search-nav:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.search-nav:disabled {
  color: var(--text-placeholder);
  cursor: default;
}

/* 清单浮层：锚在搜索条下沿，与时间线滚动互不影响 */
.search-hits {
  position: absolute;
  top: calc(100% + 4px);
  left: 12px;
  right: 12px;
  z-index: 20;
  max-height: 320px;
  overflow-y: auto;
  padding: 4px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--bg-overlay);
  box-shadow: var(--shadow-md);
}

/* 分类过滤 chips：吸在清单顶（清单滚动时不跟着滚走） */
.hit-cats {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px 4px 6px;
  background: var(--bg-overlay);
}

.hit-cat {
  padding: 2px 8px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.hit-cat:hover {
  border-color: var(--border-color-dark);
  color: var(--text-primary);
}

.hit-cat.is-on {
  border-color: var(--color-primary);
  background: var(--color-primary-bg);
  color: var(--color-primary);
}

.search-hit {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 5px 8px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-regular);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.search-hit.is-active {
  background: var(--bg-active);
}

.hit-time {
  flex: none;
  color: var(--text-secondary);
  font-size: 11px;
}

.hit-tag {
  flex: none;
  min-width: 34px;
  color: var(--text-secondary);
  font-size: 11px;
}

.hit-actor {
  flex: none;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.hit-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hit-text mark {
  padding: 0 1px;
  background: var(--color-warning-light);
  color: inherit;
  font-weight: 600;
  border-radius: 2px;
}

.search-hits-more {
  margin: 4px 0 2px;
  padding: 2px 8px;
  font-size: 11px;
  color: var(--text-placeholder);
}
</style>
