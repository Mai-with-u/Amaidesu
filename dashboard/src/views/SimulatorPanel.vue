<template>
  <div class="simulator-page">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">LLM 模拟器（开发基础设施）</h1>
        <p class="page-subtitle">
          ADR-006：<code>SimulatorService</code> 是生成式虚拟直播间，仅在
          <code>[simulator].enabled = true</code> 时组合根装配。确定性 JSONL 回放由
          <code>MockCollector</code> 承载，控制面归位到「组件 → 采集器」。
        </p>
      </div>
      <div class="header-actions">
        <el-button
          v-if="status.enabled && status.is_available"
          :type="status.is_running ? 'danger' : 'success'"
          :loading="toggling === 'toggle'"
          @click="toggleSimulator"
        >
          {{ status.is_running ? '停止模拟器' : '启动模拟器' }}
        </el-button>
        <el-tag v-if="!status.enabled" type="info" size="large" effect="plain"> 未启用 </el-tag>
        <el-tag v-else-if="!status.is_available" type="warning" size="large" effect="plain">
          未注入
        </el-tag>
        <el-tag v-else-if="status.is_running" type="success" size="large" effect="dark">
          运行中
        </el-tag>
        <el-tag v-else type="warning" size="large" effect="plain">已停止</el-tag>
      </div>
    </header>

    <div class="content">
      <!-- ============================================================ -->
      <!-- 空态：enabled=false 引导去 Settings                          -->
      <!-- ============================================================ -->
      <el-alert
        v-if="!status.enabled"
        type="info"
        title="模拟器当前未启用"
        :description="'请在 config/core.toml 的 [simulator] 段将 enabled 设为 true 并重启应用。运行时切换开关需要重启。'"
        show-icon
        :closable="false"
      />

      <el-alert
        v-else-if="!status.is_available"
        type="warning"
        title="配置启用但服务未注入"
        :description="'通常因为 LLMManager 缺失（如 --dry 模式），或组合根跳过 [simulator] 装配。请检查 config/core.toml 的 [simulator] 与 [llm_providers] 段。'"
        show-icon
        :closable="false"
      />

      <!-- ============================================================ -->
      <!-- 摘要行：3 个 stat chip                                       -->
      <!-- ============================================================ -->
      <el-row v-if="status.is_available" :gutter="16" style="margin-top: 16px">
        <el-col :span="8">
          <div class="stat-card">
            <div class="stat-value">{{ status.enabled ? '已启用' : '已禁用' }}</div>
            <div class="stat-label">[simulator].enabled</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="stat-card">
            <div :class="['stat-value', status.is_running ? 'is-on' : 'is-off']">
              {{ status.is_running ? '是' : '否' }}
            </div>
            <div class="stat-label">运行中</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="stat-card">
            <div class="stat-value mono">{{ llmLabel }}</div>
            <div class="stat-label">LLM client</div>
          </div>
        </el-col>
      </el-row>

      <el-row v-if="status.is_available" :gutter="16" style="margin-top: 16px">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>
              <span>关键配置（只读）</span>
            </template>
            <el-descriptions :column="1" size="small" border>
              <el-descriptions-item label="基础消息率">
                {{ formatNumber(status.config.base_rate_per_minute) }} 条/分钟
              </el-descriptions-item>
              <el-descriptions-item label="节奏模式">
                {{ status.config.cadence_mode || 'uniform' }}
              </el-descriptions-item>
              <el-descriptions-item label="礼物概率">
                {{ formatPercent(status.config.gift_probability) }}
              </el-descriptions-item>
              <el-descriptions-item label="SC 概率">
                {{ formatPercent(status.config.sc_probability) }}
              </el-descriptions-item>
              <el-descriptions-item label="LLM client">
                {{ status.config.llm_client_type || 'llm_fast' }}
              </el-descriptions-item>
              <el-descriptions-item label="LLM 温度">
                {{ formatNumber(status.config.llm_temperature) }}
              </el-descriptions-item>
              <el-descriptions-item label="Token 预算">
                {{ formatNumber(status.config.token_budget_per_hour) }} / 小时
              </el-descriptions-item>
              <el-descriptions-item label="最大并发">
                {{ status.config.max_concurrent_llm ?? '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="生成语言">
                {{ status.config.language || 'zh' }}
              </el-descriptions-item>
              <el-descriptions-item v-if="status.config.fallback_session_id" label="Session">
                {{ status.config.fallback_session_id }}
              </el-descriptions-item>
            </el-descriptions>
            <p class="hint">
              修改以上任一字段请编辑
              <code>config/core.toml</code> 的
              <code>[simulator]</code> 段并重启应用（运行时不支持热改）。
            </p>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>
              <span>如何观测模拟消息</span>
            </template>
            <div class="explain-block">
              <p>
                模拟器生成的消息以 <code>room.message.*</code> 事件推送到 EventBus，payload 携带
                <code>simulated: true</code>
                数据溯源标记；统计查询会主动排除模拟数据（迁移文档 §1.6）。
              </p>
              <p>
                可在「直播间观察」页看到 <code>[模拟]</code>
                角标的消息；亦可在 EventLog 页按
                <code>room.message.danmaku</code> 过滤，配合关注 payload 的
                <code>user.name</code> 是否为常见模拟昵称。
              </p>
              <p>
                预算耗尽（<code>token_budget_per_hour</code>）时，模拟器进入 5s
                等待恢复，期间不会产生新消息但仍响应启停信号。
              </p>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card v-if="status.is_available" shadow="never" style="margin-top: 16px">
        <template #header>
          <div class="card-header-row">
            <span>确定性 JSONL 回放（MockCollector）</span>
            <el-tag size="small" type="info" effect="plain">ADR-006</el-tag>
          </div>
        </template>
        <div class="explain-block">
          <p>
            <strong>MockCollector</strong>（采集器名 <code>mock</code>）只承担确定 性 JSONL
            回放，发出的消息同样携带 <code>simulated: true</code>。需要在
            <code>config/tools.toml</code> 的 <code>[tools.perception.config].enabled</code> 中添加
            <code>"mock"</code> 后重启应用，由 <code>CollectorManager</code> 装配。
          </p>
          <p>
            它的启停 / 配置查看统一在「<router-link to="/collectors" class="inline-link"
              >组件 → 采集器</router-link
            >」页管理（与 B 站 / 屏幕等其他采集器同源入口，避免控制面碎片化）。
          </p>
          <p class="hint">
            当前 SimulatorPanel 不暴露 mock 启停入口——按 ADR-006 收敛语义到
            唯一承载者，避免重复入口引发状态不一致。
          </p>
        </div>
      </el-card>

      <p v-if="lastError" class="error-hint">{{ lastError }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * SimulatorPanel —— SimulatorService 控制面（ADR-006 follow-up）
 *
 * 数据源：`/api/v1/simulator/*`（详见 src/modules/dashboard/api/simulator.py）。
 * 形态：单列 stat chips + 关键配置只读摘要 + 配套说明。
 *
 * 设计取舍：
 * - 模拟器在生产默认 enabled=false——空态用 el-alert 引导去 Settings 而不是显示
 *   404/disabled 占位；这与"我是开发基础设施"的定位相符：开发者知道在哪开开关。
 * - MockCollector 启停不重复暴露：归位到「组件 → 采集器」页，与 B 站等其它
 *   采集器同源控制，避免控制面碎片化（详见 docs/architecture/adr/006-…）。
 */
import { onMounted, onUnmounted, reactive, ref, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { simulatorApi } from '@/api';
import type { SimulatorControlResponse } from '@/types';

interface SimulatorStatusState {
  enabled: boolean;
  is_available: boolean;
  is_running: boolean;
  message: string;
  config: Record<string, unknown>;
}

const status = reactive<SimulatorStatusState>({
  enabled: false,
  is_available: false,
  is_running: false,
  message: '',
  config: {},
});

const toggling = ref<'toggle' | 'start' | 'stop' | null>(null);
const lastError = ref('');
let pollTimer: ReturnType<typeof setInterval> | null = null;

const llmLabel = computed(() => {
  const v = status.config.llm_client_type;
  return typeof v === 'string' && v ? v : 'llm_fast';
});

function formatNumber(v: unknown): string {
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return v.toString();
    return v.toFixed(2);
  }
  return '—';
}

function formatPercent(v: unknown): string {
  if (typeof v === 'number') return `${(v * 100).toFixed(1)}%`;
  return '—';
}

async function fetchStatus() {
  try {
    const res = await simulatorApi.getStatus();
    const data = res.data;
    status.enabled = !!data.enabled;
    status.is_available = !!data.is_available;
    status.is_running = !!data.is_running;
    status.message = typeof data.message === 'string' ? data.message : '';
    status.config =
      data.config && typeof data.config === 'object' && !Array.isArray(data.config)
        ? (data.config as Record<string, unknown>)
        : {};
    lastError.value = '';
  } catch (err) {
    // enabled=false 时 status 仍返回（不抛 404）—— 此处 try/catch 仅兜底偶发网络错误
    lastError.value = err instanceof Error ? `状态获取失败：${err.message}` : '状态获取失败';
  }
}

async function handleControl(action: 'start' | 'stop') {
  toggling.value = action;
  try {
    const res = action === 'start' ? await simulatorApi.start() : await simulatorApi.stop();
    const payload: SimulatorControlResponse = res.data;
    if (payload.success) {
      ElMessage.success(payload.message || (action === 'start' ? '已启动' : '已停止'));
    } else {
      ElMessage.warning(payload.message || '操作被拒绝');
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '操作失败');
  } finally {
    toggling.value = null;
    await fetchStatus();
  }
}

async function toggleSimulator() {
  if (!status.enabled || !status.is_available) return;
  await handleControl(status.is_running ? 'stop' : 'start');
}

onMounted(async () => {
  await fetchStatus();
  // 5s 轮询以捕捉按钮外的状态变化（如 token 预算耗尽、其它面板启停）
  pollTimer = setInterval(fetchStatus, 5000);
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<style scoped>
.simulator-page {
  display: flex;
  flex-direction: column;
  padding: var(--spacing-lg);
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: var(--spacing-md);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--spacing-md);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  min-width: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  flex-shrink: 0;
}

.content {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.stat-card {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
  text-align: center;
}

.stat-card .stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--color-primary);
  line-height: 1.2;
}

.stat-card .stat-value.mono {
  font-family: var(--font-mono);
  font-size: 22px;
}

.stat-card .stat-value.is-on {
  color: var(--color-success);
}

.stat-card .stat-value.is-off {
  color: var(--text-placeholder);
}

.stat-card .stat-label {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: var(--spacing-xs);
}

.explain-block {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-regular);
}

.explain-block code {
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
}

.explain-block .hint {
  font-size: 12px;
  color: var(--text-secondary);
  font-style: italic;
}

.card-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-md);
}

.hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: var(--spacing-sm);
  font-style: italic;
}

.hint code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.inline-link {
  color: var(--color-primary);
  text-decoration: none;
}

.inline-link:hover {
  text-decoration: underline;
}

.error-hint {
  font-size: 12px;
  color: var(--color-danger, #f56c6c);
  margin: var(--spacing-sm) 0 0;
}

code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-size: 12px;
}
</style>
