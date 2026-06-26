<template>
  <section class="phase-column" :style="phaseStyleVars">
    <!-- Phase title area -->
    <div class="section-header">
      <div class="section-title">
        <span class="phase-icon">
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

interface Props {
  phase: 'input' | 'decision' | 'output';
  title: string;
  components: ComponentSummary[];
  icon: Component;
  loading?: boolean;
}

interface Emits {
  (e: 'refresh'): void;
  (e: 'control', phase: string, name: string, action: 'start' | 'stop' | 'restart'): void;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
});

defineEmits<Emits>();

const phaseStyleVars = computed(() => {
  const colorMap = {
    input: {
      '--phase-color': 'var(--color-input)',
      '--phase-color-bg': 'var(--color-input-bg)',
    },
    decision: {
      '--phase-color': 'var(--color-decision)',
      '--phase-color-bg': 'var(--color-decision-bg)',
    },
    output: {
      '--phase-color': 'var(--color-output)',
      '--phase-color-bg': 'var(--color-output-bg)',
    },
  };
  return colorMap[props.phase];
});

const emptyText = computed(() => {
  const textMap = {
    input: '暂无 Input 组件',
    decision: '暂无 Decision 组件',
    output: '暂无 Output 组件',
  };
  return textMap[props.phase];
});
</script>

<style scoped>
.phase-column {
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

/* Phase Icon */
.phase-icon {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--phase-color);
  transition: color var(--transition-normal);
}

.phase-icon :deep(svg) {
  width: 20px;
  height: 20px;
}

/* Count Badge */
.count-badge {
  background: var(--phase-color-bg);
  color: var(--phase-color);
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
.phase-column :deep(.el-empty) {
  padding: var(--spacing-xl) 0;
}

.phase-column :deep(.el-empty__description) {
  color: var(--text-secondary);
}
</style>
