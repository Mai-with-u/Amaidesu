<template>
  <div class="components-page">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">组件管理</h1>
        <p class="page-subtitle">监控和管理 v2 架构的全部参与者（采集器 / Agent / 工具）</p>
      </div>
    </header>

    <!-- v2 三列视图：采集器 / Agent / 工具 -->
    <!--
      W8 证据：当前后端 `/api/v1/components` 仍返回旧 shape `{input:[], decision:[], output:[]}`，
      三个 Manager 未被注入所以列表恒空；v2 新分组 `collectors/agents/tools` 由后端 manager 接口
      下发，前端同时读 `component.phase` 与 `component.group` 以适配过渡期。
    -->
    <div ref="groupsContainerRef" class="groups-container">
      <PhaseColumn
        group="collectors"
        title="采集器"
        :components="collectorList"
        :icon="CollectorIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ComponentCard
          v-for="component in collectorList"
          :key="component.name"
          :component="component"
          :recent-events="getComponentEvents(component.name, 'collectors')"
          :recent-logs="getComponentLogs(component.name)"
          :event-count="getComponentEvents(component.name, 'collectors').length"
          :log-count="getComponentLogs(component.name).length"
          :action-loading="actionLoading"
          @control="action => handleControl('collectors', component.name, action)"
        />
      </PhaseColumn>

      <PhaseColumn
        group="agents"
        title="Agent"
        :components="agentList"
        :icon="AgentIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ComponentCard
          v-for="component in agentList"
          :key="component.name"
          :component="component"
          :recent-events="getComponentEvents(component.name, 'agents')"
          :recent-logs="getComponentLogs(component.name)"
          :event-count="getComponentEvents(component.name, 'agents').length"
          :log-count="getComponentLogs(component.name).length"
          :action-loading="actionLoading"
          @control="action => handleControl('agents', component.name, action)"
        />
      </PhaseColumn>

      <PhaseColumn
        group="tools"
        title="工具"
        :components="toolList"
        :icon="ToolIcon"
        :loading="loading"
        @refresh="handleRefresh"
      >
        <ComponentCard
          v-for="component in toolList"
          :key="component.name"
          :component="component"
          :recent-events="getComponentEvents(component.name, 'tools')"
          :recent-logs="getComponentLogs(component.name)"
          :event-count="getComponentEvents(component.name, 'tools').length"
          :log-count="getComponentLogs(component.name).length"
          :action-loading="actionLoading"
          @control="action => handleControl('tools', component.name, action)"
        >
          <template #detail-panel>
            <CapabilitiesPanel
              :capabilities="capabilities || { actions: [] }"
              :handler-name="component.name"
              :loading="capsLoading"
              :error="capsError"
              @retry="fetchCapabilities"
            />
          </template>
        </ComponentCard>
      </PhaseColumn>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, computed, h } from 'vue';
import { ElMessage } from 'element-plus';
import { useComponentsStore, useEventsStore, useLogsStore } from '@/stores';
import { storeToRefs } from 'pinia';
import type { ComponentControlAction, ComponentSummary, WebSocketMessage } from '@/types';
import type { LogEntry } from '@/stores/logs';
import { PhaseColumn, ComponentCard, CapabilitiesPanel } from '@/components/component-cards';
import { capabilitiesApi } from '@/api';
import type { UnifiedCapabilitiesView } from '@/types';

// ===== v2 图标（Agent/工具/采集器语义） =====

const CollectorIcon = {
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

const AgentIcon = {
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

const ToolIcon = {
  render() {
    return h(
      'svg',
      { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' },
      [
        h('path', {
          d: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77 -3.77a6 6 0 0 1 -7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1 -3 -3l6.91 -6.91a6 6 0 0 1 7.94 -7.94l-3.76 3.76z',
        }),
      ],
    );
  },
};

// ===== Stores =====

const componentsStore = useComponentsStore();
const eventsStore = useEventsStore();
const logsStore = useLogsStore();

const { components, loading } = storeToRefs(componentsStore);
const { events } = storeToRefs(eventsStore);
const { logs } = storeToRefs(logsStore);

const actionLoading = reactive<Record<string, boolean>>({});
const groupsContainerRef = ref<HTMLElement | null>(null);

// ===== v2 分组聚合 =====
//
// 优先读 `component.group`（v2）；若后端返回旧 shape（phase=input/decision/output），
// 按 phase 兜底路由到 v2 分组。重复键（同时落入新旧桶）按首次出现去重。

const collectorList = computed<ComponentSummary[]>(() => {
  const byKey = new Map<string, ComponentSummary>();
  for (const c of components.value?.collectors ?? []) byKey.set(c.name, c);
  // 旧 phase → v2 group 兜底
  for (const c of components.value?.input ?? []) {
    if (!byKey.has(c.name)) byKey.set(c.name, { ...c, group: 'collectors' });
  }
  return Array.from(byKey.values());
});

const agentList = computed<ComponentSummary[]>(() => {
  const byKey = new Map<string, ComponentSummary>();
  for (const c of components.value?.agents ?? []) byKey.set(c.name, c);
  for (const c of components.value?.decision ?? []) {
    if (!byKey.has(c.name)) byKey.set(c.name, { ...c, group: 'agents' });
  }
  return Array.from(byKey.values());
});

const toolList = computed<ComponentSummary[]>(() => {
  const byKey = new Map<string, ComponentSummary>();
  for (const c of components.value?.tools ?? []) byKey.set(c.name, c);
  for (const c of components.value?.output ?? []) {
    if (!byKey.has(c.name)) byKey.set(c.name, { ...c, group: 'tools' });
  }
  return Array.from(byKey.values());
});

// ===== Capabilities =====

const capabilities = ref<UnifiedCapabilitiesView | null>(null);
const capsLoading = ref(false);
const capsError = ref<string | null>(null);

async function fetchCapabilities() {
  if (capabilities.value && !capsError.value) return;
  capsLoading.value = true;
  capsError.value = null;
  try {
    const response = await capabilitiesApi.list();
    capabilities.value = response.data;
  } catch (e) {
    capsError.value = e instanceof Error ? e.message : '无法加载能力列表';
  } finally {
    capsLoading.value = false;
  }
}

// ===== 事件与日志按 group 名匹配 =====
//
// 匹配策略：组件名出现在事件 data.name / source / event.name 字段即关联；不再按
// 旧 phase 关联（v2 已无 phase 概念）。
function getComponentEvents(componentName: string, group: string): WebSocketMessage[] {
  return events.value.filter(e => {
    const data = (e.data ?? {}) as Record<string, unknown>;
    const msg = (data.message as Record<string, unknown> | undefined) ?? {};

    // Agent / 工具：偏好按 name / agent 字段匹配（v2 planner/tool 事件）
    if (group === 'agents' || group === 'tools') {
      const nameMatch = data.name === componentName || data.agent === componentName;
      if (nameMatch) return true;
    }

    // 采集器：按 source 匹配（room.message 事件的 source 即采集器名）
    if (group === 'collectors') {
      if (data.source === componentName) return true;
      if (msg?.source === componentName) return true;
    }

    // v2 连接事件兼容：collector.connected / decider.connected / agent.connected 等
    if (e.type.endsWith('.connected') || e.type.endsWith('.disconnected')) {
      return data.name === componentName;
    }

    return false;
  });
}

function getComponentLogs(componentName: string): LogEntry[] {
  // 日志模块命名遵循"<Name>Collector/<Name>Agent/<Name>Tool"或"<Name>"等
  const patterns = [
    componentName,
    `${componentName}Collector`,
    `${componentName}Agent`,
    `${componentName}Tool`,
    `${componentName}Handler`, // 旧 phase 兼容
    `${componentName}Decider`, // 旧 phase 兼容
  ];

  return logs.value.filter(log =>
    patterns.some(p => log.module.toLowerCase().includes(p.toLowerCase())),
  );
}

function handleRefresh() {
  componentsStore.fetchComponents();
}

async function handleControl(group: string, name: string, action: ComponentControlAction) {
  // v2 控制通道：当前 `/api/v1/components/{phase}/{name}/control` 仍按 phase 路由
  // （W8 证据：componets.py 未改 group）；临时用 phase 等价名传入。
  const phase = groupToLegacyPhase(group);
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

/** v2 group → 旧 phase（保留后端旧端点路径） */
function groupToLegacyPhase(group: string): string {
  if (group === 'collectors') return 'input';
  if (group === 'agents') return 'decision';
  if (group === 'tools') return 'output';
  return group;
}

onMounted(() => {
  componentsStore.fetchComponents();
  fetchCapabilities();
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

/* Groups Container - Horizontal Layout */
.groups-container {
  display: flex;
  gap: var(--spacing-xl);
  position: relative;
  padding: var(--spacing-lg) 0;
}

/* Group columns - equal width */
.groups-container > :deep(.group-column) {
  flex: 1 1 0;
  min-width: 280px;
  position: relative;
  z-index: 1;
}

/* Component card grid within columns */
.groups-container :deep(.components-list) {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

/* Responsive Design */
@media (max-width: 1200px) {
  .groups-container {
    gap: var(--spacing-md);
  }

  .groups-container > :deep(.group-column) {
    min-width: 250px;
  }
}

@media (max-width: 900px) {
  .groups-container {
    flex-direction: column;
  }

  .groups-container > :deep(.group-column) {
    min-width: 100%;
  }
}
</style>
