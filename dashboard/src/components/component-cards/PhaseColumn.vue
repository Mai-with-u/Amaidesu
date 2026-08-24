<template>
  <section class="group-column" :style="groupStyleVars">
    <!-- Group title area -->
    <div class="section-header">
      <div class="section-title">
        <span class="group-icon">
          <component :is="icon" />
        </span>
        <h2>{{ title }}</h2>
        <span class="count-badge">{{ components.length }}</span>
      </div>
      <el-button size="small" :loading="loading" @click="$emit('refresh')"> 刷新 </el-button>
    </div>

    <!-- Component list area -->
    <div v-if="components.length" class="components-list">
      <slot></slot>
    </div>

    <!-- Empty state -->
    <el-empty v-else :description="emptyText" :image-size="80" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { Component } from 'vue';
import type { ComponentSummary } from '@/types';

/**
 * Group 列组件（v2）
 *
 * W8 证据：后端 `/api/v1/components` 当前返回 `{input:[], decision:[], output:[]}` 三个空数组
 * （DashboardServer 的三个 Manager 未被 main.py 注入）。v2 新架构分组键为
 * `collectors / agents / tools`，但当前端点尚未更新；为兼容现状，`group` 属性接受
 * 旧 phase 名（input/decision/output）与 v2 group 名（collectors/agents/tools）。
 *
 * 颜色映射同时支持 v2 CSS 变量（--color-collector/--color-agent/--color-tool）
 * 与旧 phase 变量（--color-input/--color-decision/--color-output）。
 */

interface Props {
  /** v2: collectors | agents | tools（旧：input | decision | output） */
  group: string;
  title: string;
  components: ComponentSummary[];
  icon: Component;
  loading?: boolean;
}

interface Emits {
  (e: 'refresh'): void;
  (e: 'control', group: string, name: string, action: 'start' | 'stop' | 'restart'): void;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
});

defineEmits<Emits>();

const groupStyleVars = computed(() => {
  // v2 group → CSS 变量映射；缺变量时回落到旧 phase 变量
  const colorMap: Record<string, Record<string, string>> = {
    // v2 新分组
    collectors: {
      '--group-color': 'var(--color-collector, var(--color-input))',
      '--group-color-bg': 'var(--color-collector-bg, var(--color-input-bg))',
    },
    agents: {
      '--group-color': 'var(--color-agent, var(--color-decision))',
      '--group-color-bg': 'var(--color-agent-bg, var(--color-decision-bg))',
    },
    tools: {
      '--group-color': 'var(--color-tool, var(--color-output))',
      '--group-color-bg': 'var(--color-tool-bg, var(--color-output-bg))',
    },
    // 旧 phase 兼容（v2 后端未更新前仍会下发）
    input: {
      '--group-color': 'var(--color-input)',
      '--group-color-bg': 'var(--color-input-bg)',
    },
    decision: {
      '--group-color': 'var(--color-decision)',
      '--group-color-bg': 'var(--color-decision-bg)',
    },
    output: {
      '--group-color': 'var(--color-output)',
      '--group-color-bg': 'var(--color-output-bg)',
    },
  };
  return colorMap[props.group] ?? colorMap.input;
});

const emptyText = computed(() => {
  const textMap: Record<string, string> = {
    // v2 默认文案
    collectors: '暂无采集器',
    agents: '暂无 Agent',
    tools: '暂无工具',
    // 旧 phase 兼容
    input: '暂无 Input 组件',
    decision: '暂无 Decision 组件',
    output: '暂无 Output 组件',
  };
  return textMap[props.group] ?? '暂无组件';
});
</script>

<style scoped>
.group-column {
  margin-bottom: var(--spacing-xl);
  position: relative;
}

/* Section Header */
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-md);
}

.section-title {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.section-title h2 {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

/* Group Icon */
.group-icon {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--group-color);
  transition: color var(--transition-normal);
}

.group-icon :deep(svg) {
  width: 20px;
  height: 20px;
}

/* Count Badge */
.count-badge {
  background: var(--group-color-bg);
  color: var(--group-color);
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-lg);
  transition:
    background var(--transition-normal),
    color var(--transition-normal);
}

/* Component List - Single column layout */
.components-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

/* Empty State */
.group-column :deep(.el-empty) {
  padding: var(--spacing-xl) 0;
}

.group-column :deep(.el-empty__description) {
  color: var(--text-secondary);
}
</style>
