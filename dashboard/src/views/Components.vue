<template>
  <div class="components-page">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">组件管理</h1>
        <p class="page-subtitle">
          监控和控制系统的所有阶段参与者（Collector / Decider / OutputHandler）
        </p>
      </div>
    </header>

    <div ref="phasesContainerRef" class="phases-container">
      <!-- Input Phase Column -->
      <PhaseColumn
        phase="input"
        title="输入阶段"
        :components="components?.input || []"
        :icon="InputIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ComponentCard
          v-for="component in components?.input || []"
          :key="component.name"
          :component="component"
          :recent-events="getComponentEvents(component.name, 'input')"
          :recent-logs="getComponentLogs(component.name)"
          :event-count="getComponentEvents(component.name, 'input').length"
          :log-count="getComponentLogs(component.name).length"
          :action-loading="actionLoading"
          @control="action => handleControl('input', component.name, action)"
        />
      </PhaseColumn>

      <!-- Decision Phase Column -->
      <PhaseColumn
        phase="decision"
        title="决策阶段"
        :components="components?.decision || []"
        :icon="DecisionIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ComponentCard
          v-for="component in components?.decision || []"
          :key="component.name"
          :component="component"
          :recent-events="getComponentEvents(component.name, 'decision')"
          :recent-logs="getComponentLogs(component.name)"
          :event-count="getComponentEvents(component.name, 'decision').length"
          :log-count="getComponentLogs(component.name).length"
          :action-loading="actionLoading"
          @control="action => handleControl('decision', component.name, action)"
        />
      </PhaseColumn>

      <!-- Output Phase Column -->
      <PhaseColumn
        phase="output"
        title="输出阶段"
        :components="components?.output || []"
        :icon="OutputIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ComponentCard
          v-for="component in components?.output || []"
          :key="component.name"
          :component="component"
          :recent-events="getComponentEvents(component.name, 'output')"
          :recent-logs="getComponentLogs(component.name)"
          :event-count="getComponentEvents(component.name, 'output').length"
          :log-count="getComponentLogs(component.name).length"
          :action-loading="actionLoading"
          @control="action => handleControl('output', component.name, action)"
        />
      </PhaseColumn>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, h } from 'vue';
import { ElMessage } from 'element-plus';
import { useComponentsStore, useEventsStore, useLogsStore } from '@/stores';
import { storeToRefs } from 'pinia';
import type { ComponentControlAction, WebSocketMessage } from '@/types';
import type { LogEntry } from '@/stores/logs';
import { PhaseColumn, ComponentCard } from '@/components/component-cards';

// Phase icons as inline SVG components
const InputIcon = {
  render() {
    return h(
      'svg',
      { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' },
      [
        h('path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }),
        h('polyline', { points: '7,10 12,15 17,10' }),
        h('line', { x1: '12', y1: '15', x2: '12', y2: '3' }),
      ],
    );
  },
};

const DecisionIcon = {
  render() {
    return h(
      'svg',
      { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' },
      [
        h('rect', { x: '4', y: '4', width: '16', height: '16', rx: '2', ry: '2' }),
        h('rect', { x: '9', y: '9', width: '6', height: '6' }),
        h('line', { x1: '9', y1: '1', x2: '9', y2: '4' }),
        h('line', { x1: '15', y1: '1', x2: '15', y2: '4' }),
        h('line', { x1: '9', y1: '20', x2: '9', y2: '23' }),
        h('line', { x1: '15', y1: '20', x2: '15', y2: '23' }),
        h('line', { x1: '20', y1: '9', x2: '23', y2: '9' }),
        h('line', { x1: '20', y1: '14', x2: '23', y2: '14' }),
        h('line', { x1: '1', y1: '9', x2: '4', y2: '9' }),
        h('line', { x1: '1', y1: '14', x2: '4', y2: '14' }),
      ],
    );
  },
};

const OutputIcon = {
  render() {
    return h(
      'svg',
      { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' },
      [
        h('path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }),
        h('polyline', { points: '17,8 12,3 7,8' }),
        h('line', { x1: '12', y1: '3', x2: '12', y2: '15' }),
      ],
    );
  },
};

// Stores
const componentsStore = useComponentsStore();
const eventsStore = useEventsStore();
const logsStore = useLogsStore();

const { components, loading } = storeToRefs(componentsStore);
const { events } = storeToRefs(eventsStore);
const { logs } = storeToRefs(logsStore);

// Track loading state for individual actions
const actionLoading = reactive<Record<string, boolean>>({});

// Reference to phases container for positioning
const phasesContainerRef = ref<HTMLElement | null>(null);

// Get events related to a specific component
function getComponentEvents(componentName: string, componentPhase: string): WebSocketMessage[] {
  return events.value.filter(e => {
    // Input collector: match by event.data.source
    if (componentPhase === 'input' && e.type === 'message.received') {
      return e.data?.source === componentName;
    }

    // Decision decider: match by event.data.name (ConnectedPayload.name / IntentPayload.name)
    if (componentPhase === 'decision' && e.type === 'decision.intent') {
      return e.data?.name === componentName;
    }

    // Output handler: show all output phase events
    if (componentPhase === 'output') {
      return (
        e.type === 'output.render' ||
        e.type === 'collector.connected' ||
        e.type === 'collector.disconnected'
      );
    }

    // collector.connected / collector.disconnected events (Input 阶段)
    if (e.type === 'collector.connected' || e.type === 'collector.disconnected') {
      return e.data?.name === componentName;
    }

    // decider.connected / decider.disconnected events (Decision 阶段)
    if (e.type === 'decider.connected' || e.type === 'decider.disconnected') {
      return e.data?.name === componentName;
    }

    return false;
  });
}

// Get logs related to a specific component
function getComponentLogs(componentName: string): LogEntry[] {
  // Log modules are usually class names like "SubtitleHandler"
  // Need to convert component name to possible class name patterns
  const patterns = [
    componentName,
    `${componentName}Collector`,
    `${componentName}Decider`,
    `${componentName}Handler`,
  ];

  return logs.value.filter(log =>
    patterns.some(p => log.module.toLowerCase().includes(p.toLowerCase())),
  );
}

// Handle refresh
function handleRefresh() {
  componentsStore.fetchComponents();
}

// Handle component control actions
async function handleControl(phase: string, name: string, action: ComponentControlAction) {
  const actionKey = `${name}-${action}`;
  actionLoading[actionKey] = true;

  try {
    const result = await componentsStore.controlComponent(phase, name, action);
    ElMessage.success(result.message);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '操作失败');
  } finally {
    actionLoading[actionKey] = false;
  }
}

onMounted(() => {
  componentsStore.fetchComponents();
  eventsStore.connect();
  logsStore.connect();
});
</script>

<style scoped>
.components-page {
  max-width: 1600px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: var(--spacing-lg);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.page-subtitle {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0;
}

/* Phases Container - Horizontal Layout */
.phases-container {
  display: flex;
  gap: var(--spacing-xl);
  position: relative;
  padding: var(--spacing-lg) 0;
}

/* Phase columns - equal width */
.phases-container > :deep(.phase-column) {
  flex: 1 1 0;
  min-width: 280px;
  position: relative;
  z-index: 1;
}

/* Component card grid within columns */
.phases-container :deep(.components-list) {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

/* Responsive Design */
@media (max-width: 1200px) {
  .phases-container {
    gap: var(--spacing-md);
  }

  .phases-container > :deep(.phase-column) {
    min-width: 250px;
  }
}

@media (max-width: 900px) {
  .phases-container {
    flex-direction: column;
  }

  .phases-container > :deep(.phase-column) {
    min-width: 100%;
  }

  /* Hide flow animations on mobile */
  .flow-canvas {
    display: none;
  }
}
</style>
