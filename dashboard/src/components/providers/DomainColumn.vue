<template>
  <section class="domain-column" :style="domainStyleVars">
    <!-- 域标题区域 -->
    <div class="section-header">
      <div class="section-title">
        <span class="domain-icon">
          <component :is="icon" />
        </span>
        <h2>{{ title }}</h2>
        <span class="count-badge">{{ providers.length }}</span>
      </div>
      <el-button size="small" :loading="loading" @click="$emit('refresh')"> 刷新 </el-button>
    </div>

    <!-- Provider 列表区域 -->
    <div v-if="providers.length" class="providers-list">
      <slot></slot>
    </div>

    <!-- 空状态 -->
    <el-empty v-else :description="emptyText" :image-size="80" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { Component } from 'vue';
import type { ProviderSummary } from '@/types';

interface Props {
  domain: 'input' | 'decision' | 'output';
  title: string;
  providers: ProviderSummary[];
  icon: Component;
  loading?: boolean;
}

interface Emits {
  (e: 'refresh'): void;
  (e: 'control', domain: string, name: string, action: 'start' | 'stop' | 'restart'): void;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
});

defineEmits<Emits>();

// 域特定颜色 CSS 变量
const domainStyleVars = computed(() => {
  const colorMap = {
    input: {
      '--domain-color': 'var(--color-input)',
      '--domain-color-bg': 'var(--color-input-bg)',
    },
    decision: {
      '--domain-color': 'var(--color-decision)',
      '--domain-color-bg': 'var(--color-decision-bg)',
    },
    output: {
      '--domain-color': 'var(--color-output)',
      '--domain-color-bg': 'var(--color-output-bg)',
    },
  };
  return colorMap[props.domain];
});

// 空状态文本
const emptyText = computed(() => {
  const textMap = {
    input: '暂无 Input Provider',
    decision: '暂无 Decision Provider',
    output: '暂无 Output Provider',
  };
  return textMap[props.domain];
});
</script>

<style scoped>
.domain-column {
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

/* Domain Icon */
.domain-icon {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--domain-color);
  transition: color var(--transition-normal);
}

.domain-icon :deep(svg) {
  width: 20px;
  height: 20px;
}

/* Count Badge */
.count-badge {
  background: var(--domain-color-bg);
  color: var(--domain-color);
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-lg);
  transition:
    background var(--transition-normal),
    color var(--transition-normal);
}

/* Providers List - Single column layout */
.providers-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

/* Empty State */
.domain-column :deep(.el-empty) {
  padding: var(--spacing-xl) 0;
}

.domain-column :deep(.el-empty__description) {
  color: var(--text-secondary);
}
</style>
