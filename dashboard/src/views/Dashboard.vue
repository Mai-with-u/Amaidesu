<template>
  <div class="dashboard">
    <!-- 页面标题 -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">系统监控</h1>
        <p class="page-subtitle">实时监控系统运行状态</p>
      </div>
      <div class="header-actions">
        <router-link to="/eventlog">
          <el-button type="primary" plain>
            <el-icon><Document /></el-icon>
            查看事件日志
          </el-button>
        </router-link>
      </div>
    </header>

    <!-- Domain 状态卡片 -->
    <section class="domain-cards">
      <div class="domain-card" :class="{ active: status?.input_domain?.enabled }">
        <div class="card-header">
          <div class="card-icon input">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </div>
          <div class="card-title-area">
            <div class="card-title-row">
              <h3 class="card-title">Input Domain</h3>
              <span class="health-badge" :class="getHealthStatus('input')">
                {{ getHealthLabel('input') }}
              </span>
            </div>
            <span class="card-subtitle">输入域</span>
          </div>
        </div>
        <div class="card-stats">
          <div class="stat">
            <span class="stat-value">{{ status?.input_domain?.active_providers || 0 }}</span>
            <span class="stat-label">运行中</span>
          </div>
          <div class="stat-divider"></div>
          <div class="stat">
            <span class="stat-value">{{ status?.input_domain?.total_providers || 0 }}</span>
            <span class="stat-label">总计</span>
          </div>
        </div>
        <div class="card-footer">
          <div class="domain-actions">
            <el-button
              size="small"
              type="success"
              plain
              :loading="domainLoading.input === 'start'"
              :disabled="!status?.input_domain?.enabled || allProvidersRunning('input')"
              @click="startAllProviders('input')"
            >
              启动全部
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="domainLoading.input === 'stop'"
              :disabled="!status?.input_domain?.enabled || allProvidersStopped('input')"
              @click="stopAllProviders('input')"
            >
              停止全部
            </el-button>
          </div>
        </div>
      </div>

      <div class="domain-card" :class="{ active: status?.decision_domain?.enabled }">
        <div class="card-header">
          <div class="card-icon decision">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M12 1v6m0 6v10" />
              <path d="m4.22 4.22 4.24 4.24m7.08 7.08 4.24 4.24" />
              <path d="M1 12h6m6 0h10" />
              <path d="m4.22 19.78 4.24-4.24m7.08-7.08 4.24-4.24" />
            </svg>
          </div>
          <div class="card-title-area">
            <div class="card-title-row">
              <h3 class="card-title">Decision Domain</h3>
              <span class="health-badge" :class="getHealthStatus('decision')">
                {{ getHealthLabel('decision') }}
              </span>
            </div>
            <span class="card-subtitle">决策域</span>
          </div>
        </div>
        <div class="card-stats">
          <div class="stat">
            <span class="stat-value">{{ status?.decision_domain?.active_providers || 0 }}</span>
            <span class="stat-label">运行中</span>
          </div>
          <div class="stat-divider"></div>
          <div class="stat">
            <span class="stat-value">{{ status?.decision_domain?.total_providers || 0 }}</span>
            <span class="stat-label">总计</span>
          </div>
        </div>
        <div class="card-footer">
          <div class="domain-actions">
            <el-button
              size="small"
              type="success"
              plain
              :loading="domainLoading.decision === 'start'"
              :disabled="!status?.decision_domain?.enabled || allProvidersRunning('decision')"
              @click="startAllProviders('decision')"
            >
              启动全部
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="domainLoading.decision === 'stop'"
              :disabled="!status?.decision_domain?.enabled || allProvidersStopped('decision')"
              @click="stopAllProviders('decision')"
            >
              停止全部
            </el-button>
          </div>
        </div>
      </div>

      <div class="domain-card" :class="{ active: status?.output_domain?.enabled }">
        <div class="card-header">
          <div class="card-icon output">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
          </div>
          <div class="card-title-area">
            <div class="card-title-row">
              <h3 class="card-title">Output Domain</h3>
              <span class="health-badge" :class="getHealthStatus('output')">
                {{ getHealthLabel('output') }}
              </span>
            </div>
            <span class="card-subtitle">输出域</span>
          </div>
        </div>
        <div class="card-stats">
          <div class="stat">
            <span class="stat-value">{{ status?.output_domain?.active_providers || 0 }}</span>
            <span class="stat-label">运行中</span>
          </div>
          <div class="stat-divider"></div>
          <div class="stat">
            <span class="stat-value">{{ status?.output_domain?.total_providers || 0 }}</span>
            <span class="stat-label">总计</span>
          </div>
        </div>
        <div class="card-footer">
          <div class="domain-actions">
            <el-button
              size="small"
              type="success"
              plain
              :loading="domainLoading.output === 'start'"
              :disabled="!status?.output_domain?.enabled || allProvidersRunning('output')"
              @click="startAllProviders('output')"
            >
              启动全部
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="domainLoading.output === 'stop'"
              :disabled="!status?.output_domain?.enabled || allProvidersStopped('output')"
              @click="stopAllProviders('output')"
            >
              停止全部
            </el-button>
          </div>
        </div>
      </div>
    </section>

    <!-- 系统信息 -->
    <section class="system-info-section">
      <div class="section-header">
        <h2 class="section-title">系统信息</h2>
      </div>
      <div class="info-grid">
        <div class="info-card">
          <span class="info-label">运行状态</span>
          <span class="info-value" :class="{ running: status?.running }">
            {{ status?.running ? '运行中' : '已停止' }}
          </span>
        </div>
        <div class="info-card">
          <span class="info-label">运行时间</span>
          <span class="info-value mono">{{ formatUptime(status?.uptime_seconds || 0) }}</span>
        </div>
        <div class="info-card">
          <span class="info-label">版本</span>
          <span class="info-value mono">{{ status?.version || '-' }}</span>
        </div>
        <div class="info-card">
          <span class="info-label">Python</span>
          <span class="info-value mono">{{ status?.python_version || '-' }}</span>
        </div>
      </div>
    </section>

    <!-- 快捷链接 -->
    <section class="quick-links-section">
      <div class="section-header">
        <h2 class="section-title">快捷入口</h2>
      </div>
      <div class="quick-links">
        <router-link to="/eventlog" class="quick-link-card">
          <div class="quick-link-icon">
            <el-icon :size="24"><Document /></el-icon>
          </div>
          <div class="quick-link-content">
            <h4 class="quick-link-title">事件日志</h4>
            <p class="quick-link-desc">实时监控系统事件流，支持筛选和搜索</p>
          </div>
        </router-link>
        <router-link to="/providers" class="quick-link-card">
          <div class="quick-link-icon">
            <el-icon :size="24"><Connection /></el-icon>
          </div>
          <div class="quick-link-content">
            <h4 class="quick-link-title">Provider 管理</h4>
            <p class="quick-link-desc">管理各域的 Provider 启停状态</p>
          </div>
        </router-link>
        <router-link to="/devtools" class="quick-link-card">
          <div class="quick-link-icon">
            <el-icon :size="24"><Tools /></el-icon>
          </div>
          <div class="quick-link-content">
            <h4 class="quick-link-title">开发工具</h4>
            <p class="quick-link-desc">消息注入和 EventBus 统计</p>
          </div>
        </router-link>
        <router-link to="/settings" class="quick-link-card">
          <div class="quick-link-icon">
            <el-icon :size="24"><Setting /></el-icon>
          </div>
          <div class="quick-link-content">
            <h4 class="quick-link-title">系统设置</h4>
            <p class="quick-link-desc">配置管理，保存后可重启服务</p>
          </div>
        </router-link>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { Document, Connection, Tools, Setting } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { useSystemStore, useProvidersStore } from '@/stores';
import { storeToRefs } from 'pinia';

const systemStore = useSystemStore();
const providersStore = useProvidersStore();

const { status } = storeToRefs(systemStore);
const { providers } = storeToRefs(providersStore);

const domainLoading = ref<Record<string, 'start' | 'stop' | null>>({
  input: null,
  decision: null,
  output: null,
});

// Get providers for a specific domain
function getDomainProviders(domain: string) {
  if (!providers.value) return [];
  return providers.value[domain as keyof typeof providers.value] || [];
}

// Check if all providers in a domain are running
function allProvidersRunning(domain: string): boolean {
  const domainProviders = getDomainProviders(domain);
  if (domainProviders.length === 0) return true;
  return domainProviders.every((p) => p.is_started);
}

// Check if all providers in a domain are stopped
function allProvidersStopped(domain: string): boolean {
  const domainProviders = getDomainProviders(domain);
  if (domainProviders.length === 0) return true;
  return domainProviders.every((p) => !p.is_started);
}

// Get health status class for a domain
function getHealthStatus(domain: string): string {
  const domainStatus = status.value?.[`${domain}_domain` as keyof typeof status.value] as
    | { enabled?: boolean; active_providers?: number; total_providers?: number }
    | undefined;

  if (!domainStatus?.enabled || domainStatus.total_providers === 0) {
    return 'status-disabled';
  }

  const activeCount = domainStatus.active_providers || 0;
  const totalCount = domainStatus.total_providers || 0;

  if (activeCount === totalCount) {
    return 'status-healthy';
  } else if (activeCount === 0) {
    return 'status-stopped';
  } else {
    return 'status-warning';
  }
}

// Get health label for a domain
function getHealthLabel(domain: string): string {
  const domainStatus = status.value?.[`${domain}_domain` as keyof typeof status.value] as
    | { enabled?: boolean; active_providers?: number; total_providers?: number }
    | undefined;

  if (!domainStatus?.enabled || domainStatus.total_providers === 0) {
    return '已禁用';
  }

  const activeCount = domainStatus.active_providers || 0;
  const totalCount = domainStatus.total_providers || 0;

  if (activeCount === totalCount) {
    return '正常运行';
  } else if (activeCount === 0) {
    return '已停止';
  } else {
    return '部分异常';
  }
}

// Start all providers in a domain
async function startAllProviders(domain: string) {
  const domainProviders = getDomainProviders(domain);
  const stoppedProviders = domainProviders.filter((p) => !p.is_started && p.is_enabled);

  if (stoppedProviders.length === 0) {
    ElMessage.info('所有 Provider 已在运行中');
    return;
  }

  domainLoading.value[domain] = 'start';

  try {
    const results = await Promise.allSettled(
      stoppedProviders.map((p) => providersStore.controlProvider(domain, p.name, 'start')),
    );

    const failed = results.filter((r) => r.status === 'rejected');
    const succeeded = results.length - failed.length;

    if (failed.length === 0) {
      ElMessage.success(`已启动 ${succeeded} 个 Provider`);
    } else {
      ElMessage.warning(`启动完成: ${succeeded} 成功, ${failed.length} 失败`);
    }

    await systemStore.fetchStatus();
  } catch (error) {
    ElMessage.error('批量启动失败');
    console.error('Batch start error:', error);
  } finally {
    domainLoading.value[domain] = null;
  }
}

// Stop all providers in a domain
async function stopAllProviders(domain: string) {
  const domainProviders = getDomainProviders(domain);
  const runningProviders = domainProviders.filter((p) => p.is_started);

  if (runningProviders.length === 0) {
    ElMessage.info('所有 Provider 已停止');
    return;
  }

  domainLoading.value[domain] = 'stop';

  try {
    const results = await Promise.allSettled(
      runningProviders.map((p) => providersStore.controlProvider(domain, p.name, 'stop')),
    );

    const failed = results.filter((r) => r.status === 'rejected');
    const succeeded = results.length - failed.length;

    if (failed.length === 0) {
      ElMessage.success(`已停止 ${succeeded} 个 Provider`);
    } else {
      ElMessage.warning(`停止完成: ${succeeded} 成功, ${failed.length} 失败`);
    }

    await systemStore.fetchStatus();
  } catch (error) {
    ElMessage.error('批量停止失败');
    console.error('Batch stop error:', error);
  } finally {
    domainLoading.value[domain] = null;
  }
}

// Format uptime as human-readable string
function formatUptime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  if (hours > 0) {
    return `${hours}h ${minutes}m ${secs}s`;
  } else if (minutes > 0) {
    return `${minutes}m ${secs}s`;
  } else {
    return `${secs}s`;
  }
}

// Lifecycle management
let statusInterval: ReturnType<typeof setInterval> | null = null;

onMounted(async () => {
  await systemStore.fetchStatus();
  await providersStore.fetchProviders();

  statusInterval = setInterval(() => {
    systemStore.fetchStatus();
    providersStore.fetchProviders();
  }, 10000);
});

onUnmounted(() => {
  if (statusInterval) {
    clearInterval(statusInterval);
  }
});
</script>

<style scoped>
.dashboard {
  max-width: 1400px;
  margin: 0 auto;
}

/* 页面标题 */
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-lg);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.header-actions {
  flex-shrink: 0;
}

/* Domain 卡片 */
.domain-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-lg);
}

.domain-card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-normal);
  position: relative;
  overflow: hidden;
}

.domain-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: var(--border-color);
  transition: background var(--transition-normal);
}

.domain-card.active::before {
  background: var(--color-success);
}

.domain-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.card-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-md);
}

.card-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.card-icon svg {
  width: 24px;
  height: 24px;
}

.card-icon.input {
  background-color: var(--color-input-bg);
  color: var(--color-input);
}

.card-icon.decision {
  background-color: var(--color-decision-bg);
  color: var(--color-decision);
}

.card-icon.output {
  background-color: var(--color-output-bg);
  color: var(--color-output);
}

.card-title-area {
  flex: 1;
}

.card-title-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.card-subtitle {
  font-size: 12px;
  color: var(--text-secondary);
}

.health-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.health-badge.status-healthy {
  background-color: var(--color-success-light);
  color: var(--color-success);
}

.health-badge.status-warning {
  background-color: var(--color-warning-light);
  color: var(--color-warning);
}

.health-badge.status-stopped {
  background-color: var(--color-danger-light);
  color: var(--color-danger);
}

.health-badge.status-disabled {
  background-color: var(--bg-hover);
  color: var(--text-secondary);
}

.card-stats {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-md);
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
}

.stat-divider {
  width: 1px;
  height: 32px;
  background-color: var(--border-color-light);
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.domain-actions {
  display: flex;
  gap: var(--spacing-xs);
}

/* 系统信息 */
.system-info-section {
  margin-bottom: var(--spacing-lg);
}

.section-header {
  margin-bottom: var(--spacing-md);
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--spacing-md);
}

.info-card {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.info-label {
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.info-value {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
}

.info-value.mono {
  font-family: var(--font-mono);
}

.info-value.running {
  color: var(--color-success);
}

/* 快捷入口 */
.quick-links-section {
  margin-bottom: var(--spacing-lg);
}

.quick-links {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--spacing-md);
}

.quick-link-card {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  text-decoration: none;
  color: inherit;
  transition: all var(--transition-fast);
}

.quick-link-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow-sm);
  transform: translateY(-2px);
}

.quick-link-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-hover);
  border-radius: var(--radius-md);
  color: var(--color-primary);
  flex-shrink: 0;
}

.quick-link-content {
  flex: 1;
  min-width: 0;
}

.quick-link-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.quick-link-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.4;
}

/* Utility Classes */
.mono {
  font-family: var(--font-mono);
}

/* Responsive */
@media (max-width: 1200px) {
  .domain-cards {
    grid-template-columns: repeat(2, 1fr);
  }

  .info-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .quick-links {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    gap: var(--spacing-md);
  }

  .domain-cards {
    grid-template-columns: 1fr;
  }

  .quick-links {
    grid-template-columns: 1fr;
  }
}
</style>
