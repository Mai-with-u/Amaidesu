<!--
  组件管理页共享左栏面板：标题 + 计数 + 批量操作插槽 + 加载/错误/空三态 + 列表行。

  采集器 / Agent / 工具三页共用的主从骨架左半部分。行内"状态点 + 名称 + 行尾
  标签"的形状与键盘导航（listbox/option、Enter/Space 选中）在此只有一份实现；
  各页通过 items 注入自己的行数据，通过插槽注入批量按钮与面板附加内容。
-->
<template>
  <aside class="md-list-panel" :aria-label="listLabel">
    <header class="md-list-header">
      <div class="md-list-header-main">
        <h2 class="md-list-title">{{ title }}</h2>
        <span v-if="countTotal !== undefined" class="md-list-count">
          <template v-if="countRunning !== undefined">
            <span class="md-list-count-running">{{ countRunning }}</span>
            <span class="md-list-count-unit">运行</span>
            <span class="md-list-count-divider">·</span>
            <span class="md-list-count-total">共 {{ countTotal }}</span>
          </template>
          <template v-else>
            <span class="md-list-count-running">{{ countTotal }}</span>
            <span class="md-list-count-suffix">{{ countSuffix }}</span>
          </template>
        </span>
      </div>
      <div class="md-batch-actions">
        <slot name="batch" />
      </div>
    </header>

    <slot name="below-header" />

    <div v-if="loading" class="md-list-empty">
      <el-skeleton :rows="4" animated />
    </div>
    <el-alert
      v-else-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
      class="md-list-error"
    >
      <el-button type="primary" @click="emit('retry')">重试</el-button>
    </el-alert>
    <div v-else-if="items.length === 0" class="md-list-empty">
      <el-empty :image-size="64" :description="emptyText" />
    </div>
    <ul v-else class="md-list" role="listbox">
      <li
        v-for="item in items"
        :key="item.key"
        class="md-row"
        :class="{
          'is-selected': item.key === selectedKey,
          'is-running': item.isStarted,
          'is-stopped': !item.isStarted && item.isEnabled,
          'is-disabled': item.isEnabled === false,
        }"
        role="option"
        :aria-selected="item.key === selectedKey"
        tabindex="0"
        @click="emit('select', item.key)"
        @keydown.enter.prevent="emit('select', item.key)"
        @keydown.space.prevent="emit('select', item.key)"
      >
        <span class="md-status-dot" aria-hidden="true" />
        <span class="md-row-name" :title="item.name">{{ item.name }}</span>
        <el-tag
          v-if="item.tag"
          size="small"
          :type="item.tagType ?? 'success'"
          effect="plain"
          class="md-running-tag"
        >
          {{ item.tag }}
        </el-tag>
      </li>
    </ul>
  </aside>
</template>

<script setup lang="ts">
/** 列表行数据：三页各自从自己的领域对象映射而来 */
export interface ListPanelItem {
  /** 唯一键，也是选中态与 select 事件的载荷 */
  key: string;
  /** 行内显示名 */
  name: string;
  /** 绿点语义：采集器/Agent = 运行中；工具分类 = 有启用的提供者 */
  isStarted?: boolean;
  /** 黄点语义：已启用但未运行；false = 灰点（未启用） */
  isEnabled?: boolean;
  /** 行尾标签文案（如"运行"或工具数量）；不传不渲染 */
  tag?: string | null;
  tagType?: 'success' | 'info' | 'warning';
}

withDefaults(
  defineProps<{
    title: string;
    /** 面板无障碍标签（aside 的 aria-label；不用 aria 前缀命名——aria-* 属性 Vue 不映射为 prop） */
    listLabel: string;
    items: ListPanelItem[];
    selectedKey?: string | null;
    loading?: boolean;
    error?: string | null;
    emptyText?: string;
    /** 运行态计数（渲染"N 运行 · 共 M"）；不传退化为 total-only 形态 */
    countRunning?: number;
    /** 总数；undefined 时整个计数区不渲染 */
    countTotal?: number;
    /** total-only 形态的单位后缀 */
    countSuffix?: string;
  }>(),
  {
    selectedKey: null,
    loading: false,
    error: null,
    emptyText: '暂无数据',
    countRunning: undefined,
    countTotal: undefined,
    countSuffix: '项',
  },
);

const emit = defineEmits<{
  select: [key: string];
  retry: [];
}>();
</script>
