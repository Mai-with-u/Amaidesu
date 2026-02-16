<template>
  <div class="providers-page">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">Provider 管理</h1>
        <p class="page-subtitle">监控和控制系统的所有 Provider 实例</p>
      </div>
    </header>

    <div ref="domainsContainerRef" class="domains-container">
      <!-- Input Domain Column -->
      <DomainColumn
        domain="input"
        title="输入域"
        :providers="providers?.input || []"
        :icon="InputIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ProviderCard
          v-for="provider in providers?.input || []"
          :key="provider.name"
          :provider="provider"
          :recent-events="getProviderEvents(provider.name, 'input')"
          :recent-logs="getProviderLogs(provider.name)"
          :event-count="getProviderEvents(provider.name, 'input').length"
          :log-count="getProviderLogs(provider.name).length"
          :action-loading="actionLoading"
          @control="(action) => handleControl('input', provider.name, action)"
        />
      </DomainColumn>

      <!-- Decision Domain Column -->
      <DomainColumn
        domain="decision"
        title="决策域"
        :providers="providers?.decision || []"
        :icon="DecisionIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ProviderCard
          v-for="provider in providers?.decision || []"
          :key="provider.name"
          :provider="provider"
          :recent-events="getProviderEvents(provider.name, 'decision')"
          :recent-logs="getProviderLogs(provider.name)"
          :event-count="getProviderEvents(provider.name, 'decision').length"
          :log-count="getProviderLogs(provider.name).length"
          :action-loading="actionLoading"
          @control="(action) => handleControl('decision', provider.name, action)"
        />
      </DomainColumn>

      <!-- Output Domain Column -->
      <DomainColumn
        domain="output"
        title="输出域"
        :providers="providers?.output || []"
        :icon="OutputIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ProviderCard
          v-for="provider in providers?.output || []"
          :key="provider.name"
          :provider="provider"
          :recent-events="getProviderEvents(provider.name, 'output')"
          :recent-logs="getProviderLogs(provider.name)"
          :event-count="getProviderEvents(provider.name, 'output').length"
          :log-count="getProviderLogs(provider.name).length"
          :action-loading="actionLoading"
          @control="(action) => handleControl('output', provider.name, action)"
        />
      </DomainColumn>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, h } from 'vue';
import { ElMessage } from 'element-plus';
import { useProvidersStore, useEventsStore, useLogsStore } from '@/stores';
import { storeToRefs } from 'pinia';
import type { ProviderControlAction, WebSocketMessage } from '@/types';
import type { LogEntry } from '@/stores/logs';
import { DomainColumn, ProviderCard } from '@/components/providers';

// Domain icons as inline SVG components
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
const providersStore = useProvidersStore();
const eventsStore = useEventsStore();
const logsStore = useLogsStore();

const { providers, loading } = storeToRefs(providersStore);
const { events } = storeToRefs(eventsStore);
const { logs } = storeToRefs(logsStore);

// Track loading state for individual actions
const actionLoading = reactive<Record<string, boolean>>({});

// Reference to domains container for positioning
const domainsContainerRef = ref<HTMLElement | null>(null);

// Get events related to a specific provider
function getProviderEvents(providerName: string, providerDomain: string): WebSocketMessage[] {
  return events.value.filter((e) => {
    // Input Provider: use event.data.source field for matching
    if (providerDomain === 'input' && e.type === 'message.received') {
      return e.data?.source === providerName;
    }

    // Decision Provider: use event.data.provider field for matching
    if (providerDomain === 'decision' && e.type === 'decision.intent') {
      return e.data?.provider === providerName;
    }

    // Output Provider: show all output domain events
    // Note: output.render event's provider field is the DecisionProvider name
    if (providerDomain === 'output') {
      return (
        e.type === 'output.render' ||
        e.type === 'provider.connected' ||
        e.type === 'provider.disconnected'
      );
    }

    // Provider connected/disconnected events
    if (e.type === 'provider.connected' || e.type === 'provider.disconnected') {
      return e.data?.provider === providerName;
    }

    return false;
  });
}

// Get logs related to a specific provider
function getProviderLogs(providerName: string): LogEntry[] {
  // Provider log modules are usually class names like "SubtitleOutputProvider"
  // Need to convert provider name to possible class name patterns
  const patterns = [
    providerName, // Original name
    `${providerName}Provider`, // Add Provider suffix
    `${providerName}InputProvider`, // Input Provider
    `${providerName}OutputProvider`, // Output Provider
    `${providerName}DecisionProvider`, // Decision Provider
  ];

  return logs.value.filter((log) =>
    patterns.some((p) => log.module.toLowerCase().includes(p.toLowerCase())),
  );
}

// Handle refresh
function handleRefresh() {
  providersStore.fetchProviders();
}

// Handle provider control actions
async function handleControl(domain: string, name: string, action: ProviderControlAction) {
  const actionKey = `${name}-${action}`;
  actionLoading[actionKey] = true;

  try {
    const result = await providersStore.controlProvider(domain, name, action);
    ElMessage.success(result.message);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '操作失败');
  } finally {
    actionLoading[actionKey] = false;
  }
}

onMounted(() => {
  providersStore.fetchProviders();
  // Connect to WebSocket for real-time events and logs
  eventsStore.connect();
  logsStore.connect();
});
</script>

<style scoped>
.providers-page {
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

/* Domains Container - Horizontal Layout */
.domains-container {
  display: flex;
  gap: var(--spacing-xl);
  position: relative;
  padding: var(--spacing-lg) 0;
}

/* Domain columns - equal width */
.domains-container > :deep(.domain-column) {
  flex: 1 1 0;
  min-width: 280px;
  position: relative;
  z-index: 1;
}

/* Provider card grid within columns */
.domains-container :deep(.providers-list) {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

/* Responsive Design */
@media (max-width: 1200px) {
  .domains-container {
    gap: var(--spacing-md);
  }

  .domains-container > :deep(.domain-column) {
    min-width: 250px;
  }
}

@media (max-width: 900px) {
  .domains-container {
    flex-direction: column;
  }

  .domains-container > :deep(.domain-column) {
    min-width: 100%;
  }

  /* Hide flow animations on mobile */
  .flow-canvas {
    display: none;
  }
}
</style>
