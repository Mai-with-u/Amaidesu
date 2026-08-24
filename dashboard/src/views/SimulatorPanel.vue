<template>
  <div class="mock-control-panel">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">Mock 采集器控制面</h1>
        <p class="page-subtitle">
          v2 模拟直播间由 <code>modules/collectors/mock/</code> 承载；本面板用于启停与观测
        </p>
      </div>
      <div class="header-actions">
        <el-button
          :type="status.is_running ? 'danger' : 'success'"
          :loading="toggling"
          @click="toggleCollector"
        >
          {{ status.is_running ? '停止 Mock' : '启动 Mock' }}
        </el-button>
        <el-tag v-if="status.is_running" type="success" size="large" effect="dark"> 运行中 </el-tag>
        <el-tag
          v-else-if="!status.is_collector_available"
          type="warning"
          size="large"
          effect="plain"
        >
          未接入
        </el-tag>
        <el-tag v-else type="info" size="large" effect="plain">已停止</el-tag>
      </div>
    </header>

    <div class="content">
      <el-alert
        v-if="!status.is_collector_available"
        type="info"
        title="Mock 控制面端点尚未挂载"
        :description="'W8 证据：后端 /api/v1/simulator/* 已删除，/api/v1/mock/* 待补齐。当前面板降级为只读监控占位。'"
        show-icon
        :closable="false"
      />

      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="8">
          <div class="stat-card">
            <div class="stat-value">{{ stats.total_messages }}</div>
            <div class="stat-label">消息总数</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="stat-card">
            <div class="stat-value">{{ stats.simulated_count }}</div>
            <div class="stat-label">模拟消息（v2 独立计数）</div>
          </div>
        </el-col>
        <el-col :span="8">
          <div class="stat-card">
            <div class="stat-value">{{ personas.length }}</div>
            <div class="stat-label">常驻人设</div>
          </div>
        </el-col>
      </el-row>

      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>
              <span>运行时参数</span>
            </template>
            <el-form label-width="160px" size="small">
              <el-form-item label="节奏模式">
                <el-radio-group v-model="params.cadence_mode">
                  <el-radio value="uniform">均匀随机</el-radio>
                  <el-radio value="fixed">固定间隔</el-radio>
                  <el-radio value="auto">自适应突发</el-radio>
                </el-radio-group>
              </el-form-item>
              <el-form-item label="消息频率 (条/分)">
                <el-slider
                  v-model="params.base_rate_per_minute"
                  :min="0.5"
                  :max="30"
                  :step="0.5"
                  show-input
                />
              </el-form-item>
              <el-form-item label="突发倍率">
                <el-slider
                  v-model="params.burst_multiplier"
                  :min="1"
                  :max="10"
                  :step="0.5"
                  show-input
                />
              </el-form-item>
              <el-form-item label="礼物概率">
                <el-slider
                  v-model="params.gift_probability"
                  :min="0"
                  :max="0.5"
                  :step="0.01"
                  show-input
                />
              </el-form-item>
              <el-form-item>
                <el-button
                  type="primary"
                  size="small"
                  :disabled="!status.is_collector_available"
                  @click="updateParams"
                >
                  更新参数
                </el-button>
              </el-form-item>
            </el-form>
          </el-card>

          <el-card shadow="never" style="margin-top: 16px">
            <template #header>
              <span>手动触发</span>
            </template>
            <el-space wrap>
              <el-button
                type="warning"
                :disabled="!status.is_collector_available"
                @click="triggerGiftRain"
              >
                礼物雨 (30s)
              </el-button>
              <el-input
                v-model="injectTopic"
                placeholder="输入话题"
                size="small"
                style="width: 200px"
                :disabled="!status.is_collector_available"
              />
              <el-button
                type="primary"
                :disabled="!status.is_collector_available"
                @click="triggerTopicInjection"
              >
                注入话题
              </el-button>
              <el-button :disabled="!status.is_collector_available" @click="resetBudget">
                重置 Token
              </el-button>
            </el-space>
          </el-card>
        </el-col>

        <el-col :span="12">
          <el-card shadow="never">
            <template #header>
              <span>simulated 标记说明（v2 数据溯源）</span>
            </template>
            <div class="explain-block">
              <p>
                v2 后端会在 <code>room.message.*</code> 事件的 payload 中标记
                <code>simulated: true</code>； 消费方（viewers / topics /
                画像统计）查询会主动排除模拟数据（迁移文档 §1.6 定案）。
              </p>
              <p>
                模拟观众从来不是"观众"，只是测试输入；统计中即使长期重复运行、没有模拟运行记录也不计，
                除非用户显式要求查看含模拟数据的统计。
              </p>
              <p class="hint">
                当前前端 UI 收到 <code>room.message.*</code> 事件时会在卡片上加
                <code>[模拟]</code> 角标， 不污染真实弹幕链路（LiveObserver 已支持 v2 payload）。
              </p>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" style="margin-top: 16px">
        <template #header>
          <div class="card-header-row">
            <span>常驻人设（{{ personas.length }}）</span>
            <div class="card-header-actions">
              <el-input-number
                v-model="generateCount"
                :min="1"
                :max="20"
                size="small"
                controls-position="right"
                style="width: 90px"
              />
              <el-button
                type="primary"
                size="small"
                :loading="generating"
                :disabled="!status.is_collector_available"
                @click="generatePersonas"
              >
                生成人设
              </el-button>
            </div>
          </div>
        </template>
        <el-table :data="personas" size="small" stripe max-height="300">
          <el-table-column prop="user_nickname" label="昵称" width="140" />
          <el-table-column prop="role" label="角色" width="80">
            <template #default="{ row }">
              <el-tag :type="roleTagType(row.role)" size="small">{{ row.role }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="fans_medal_level" label="粉丝牌" width="80" align="right" />
          <el-table-column prop="guard_level" label="大航海" width="80" align="right" />
          <el-table-column prop="messages_generated" label="发言数" width="80" align="right" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default>
              <el-button link type="primary" size="small">编辑</el-button>
              <el-button link type="danger" size="small">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="personas.length === 0" description="暂无常驻人设" :image-size="80" />
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Mock 采集器控制面（v2.0）
 *
 * W8 证据：原 `/api/v1/simulator/*` 路由已删除；前端改用 `mockCollectorApi`（后端补齐
 * `/api/v1/mock/*` 时直接生效）。当前后端尚未挂载 mock 控制面，所有调用会 404/503，
 * 页面降级为只读监控占位。
 *
 * v2 数据语义变化：
 * - 所有模拟事件 payload 带 `simulated: true`（W3 §1.6 定案）
 * - 统计查询强制排除 simulated 源（消费方约束）
 * - 模拟观众不计入 viewers/topics 统计（除非显式要求）
 */
import { onMounted, onUnmounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { mockCollectorApi } from '@/api';
import type { MockCollectorStatus, MockCollectorStats, MockCollectorPersona } from '@/types';

const status = reactive<MockCollectorStatus>({
  is_running: false,
  started_at_ms: 0,
  config_snapshot: {},
  is_collector_available: false,
});

const stats = reactive<MockCollectorStats>({
  total_messages: 0,
  simulated_count: 0,
  total_tokens: 0,
  messages_by_type: {},
});

const personas = ref<MockCollectorPersona[]>([]);
const toggling = ref(false);
const injectTopic = ref('');
const generateCount = ref(1);
const generating = ref(false);
let pollTimer: ReturnType<typeof setInterval> | null = null;

const params = reactive({
  base_rate_per_minute: 6,
  burst_multiplier: 3,
  temp_passerby_ratio: 0.3,
  gift_probability: 0.05,
  cadence_mode: 'uniform',
  fixed_interval_s: 10,
});

function roleTagType(role: string): string {
  const map: Record<string, string> = {
    fan: 'success',
    teaser: 'warning',
    newcomer: 'info',
    hater: 'danger',
    veteran: 'primary',
  };
  return map[role] || '';
}

async function fetchStatus() {
  try {
    const res = await mockCollectorApi.getStatus();
    Object.assign(status, res.data);
  } catch {
    // 端点尚未挂载（503/404），保持默认 is_collector_available=false
  }
}

async function fetchStats() {
  try {
    const res = await mockCollectorApi.getStats();
    Object.assign(stats, res.data);
  } catch {
    // 静默
  }
}

async function fetchPersonas() {
  try {
    const res = await mockCollectorApi.getPersonas();
    personas.value = res.data ?? [];
  } catch {
    personas.value = [];
  }
}

async function toggleCollector() {
  if (!status.is_collector_available) {
    ElMessage.warning('Mock 控制面端点尚未挂载，无法启停');
    return;
  }
  toggling.value = true;
  try {
    if (status.is_running) {
      await mockCollectorApi.stop();
      ElMessage.success('Mock 采集器已停止');
    } else {
      await mockCollectorApi.start();
      ElMessage.success('Mock 采集器已启动');
    }
    await fetchStatus();
  } catch {
    ElMessage.error('操作失败（端点未挂载）');
  } finally {
    toggling.value = false;
  }
}

async function updateParams() {
  if (!status.is_collector_available) return;
  try {
    await mockCollectorApi.updateParams({ ...params });
    ElMessage.success('参数已更新');
  } catch {
    ElMessage.error('参数更新失败');
  }
}

async function triggerGiftRain() {
  if (!status.is_collector_available) return;
  try {
    await mockCollectorApi.triggerGiftRain(30);
    ElMessage.success('礼物雨已触发');
  } catch {
    ElMessage.error('触发失败');
  }
}

async function triggerTopicInjection() {
  if (!injectTopic.value.trim()) {
    ElMessage.warning('请输入话题');
    return;
  }
  try {
    await mockCollectorApi.triggerTopicInjection(injectTopic.value.trim());
    ElMessage.success(`话题已注入: ${injectTopic.value}`);
    injectTopic.value = '';
  } catch {
    ElMessage.error('话题注入失败');
  }
}

async function resetBudget() {
  if (!status.is_collector_available) return;
  try {
    await mockCollectorApi.resetTokenBudget();
    ElMessage.success('Token 预算已重置');
    await fetchStats();
  } catch {
    ElMessage.error('重置失败');
  }
}

async function generatePersonas() {
  if (!status.is_collector_available) return;
  generating.value = true;
  try {
    const res = await mockCollectorApi.generatePersonas(generateCount.value);
    const count = res.data.personas?.length ?? generateCount.value;
    const added = res.data.added ?? count;
    ElMessage.success(`已生成 ${added} 个人设`);
    await fetchPersonas();
  } catch {
    ElMessage.error('人设生成失败');
  } finally {
    generating.value = false;
  }
}

onMounted(async () => {
  await fetchStatus();
  await fetchStats();
  await fetchPersonas();
  pollTimer = setInterval(async () => {
    await fetchStats();
    await fetchPersonas();
  }, 5000);
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<style scoped>
.mock-control-panel {
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
  align-items: center;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
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

.card-header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-size: 12px;
}
</style>
