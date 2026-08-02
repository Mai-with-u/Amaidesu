<template>
  <div class="simulator-panel">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">模拟直播间</h1>
        <p class="page-subtitle">LLM 模拟观众弹幕调试工具</p>
      </div>
      <div class="header-actions">
        <el-button
          :type="status.is_running ? 'danger' : 'success'"
          :loading="toggling"
          @click="toggleSimulator"
        >
          {{ status.is_running ? '停止模拟' : '启动模拟' }}
        </el-button>
        <el-tag v-if="status.is_running" type="success" size="large" effect="dark">运行中</el-tag>
        <el-tag v-else type="info" size="large" effect="plain">已停止</el-tag>
      </div>
    </header>

    <div class="simulator-content">
      <el-row :gutter="16">
        <el-col :span="6">
          <div class="stat-card">
            <div class="stat-value">{{ stats.total_messages }}</div>
            <div class="stat-label">消息总数</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="stat-card">
            <div class="stat-value">{{ stats.total_tokens }}</div>
            <div class="stat-label">Token 用量</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="stat-card">
            <div class="stat-value">{{ Object.keys(personas).length }}</div>
            <div class="stat-label">常驻人设</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="stat-card" @click="resetBudget">
            <div class="stat-value clickable">{{ budgetRemaining }}</div>
            <div class="stat-label">剩余 Token (点击重置)</div>
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
              <el-form-item v-if="params.cadence_mode === 'fixed'" label="固定间隔 (秒)">
                <el-slider
                  v-model="params.fixed_interval_s"
                  :min="1"
                  :max="60"
                  :step="0.5"
                  show-input
                />
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
              <el-form-item label="路人比例">
                <el-slider
                  v-model="params.temp_passerby_ratio"
                  :min="0"
                  :max="1"
                  :step="0.05"
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
                <el-button type="primary" size="small" @click="updateParams">更新参数</el-button>
              </el-form-item>
            </el-form>
          </el-card>

          <el-card shadow="never" style="margin-top: 16px">
            <template #header>
              <span>手动触发</span>
            </template>
            <el-space wrap>
              <el-button type="warning" @click="triggerGiftRain">礼物雨 (30s)</el-button>
              <el-input
                v-model="injectTopic"
                placeholder="输入话题"
                size="small"
                style="width: 200px"
              />
              <el-button type="primary" @click="triggerTopicInjection">注入话题</el-button>
              <el-button @click="resetBudget">重置 Token</el-button>
            </el-space>
          </el-card>
        </el-col>

        <el-col :span="12">
          <el-card shadow="never" style="height: 400px; display: flex; flex-direction: column">
            <template #header>
              <span>实时消息流 (最近 50 条)</span>
            </template>
            <div class="message-stream" ref="messageStreamRef">
              <div
                v-for="(msg, i) in messageStream"
                :key="i"
                class="message-item"
                :class="msg.data_type"
              >
                <span class="msg-time">{{ msg.time }}</span>
                <el-tag
                  :type="
                    msg.data_type === 'gift'
                      ? 'warning'
                      : msg.data_type === 'super_chat'
                        ? 'danger'
                        : ''
                  "
                  size="small"
                  effect="plain"
                >
                  {{ msg.data_type }}
                </el-tag>
                <span class="msg-nick">{{ msg.user_nickname }}</span>
                <span class="msg-text">{{ msg.text }}</span>
              </div>
              <div v-if="messageStream.length === 0" class="empty-stream">暂无消息</div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" style="margin-top: 16px">
        <template #header>
          <span>常驻人设 ({{ personas.length }})</span>
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
          <el-table-column prop="user_id" label="ID" min-width="140" />
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue';
import { ElMessage } from 'element-plus';
import { simulatorApi } from '@/api';
import type { SimulatorStatus, SimulatorStats, SimulatorPersona } from '@/api';

const status = reactive<SimulatorStatus>({
  is_running: false,
  current_state: 'STOPPED',
  started_at_ms: 0,
  config_snapshot: {},
  is_collector_available: false,
});

const stats = reactive<SimulatorStats>({
  total_messages: 0,
  total_tokens: 0,
  messages_by_type: {},
  messages_by_role: {},
});

const personas = ref<SimulatorPersona[]>([]);
const toggling = ref(false);
const injectTopic = ref('');

const params = reactive({
  base_rate_per_minute: 6,
  burst_multiplier: 3,
  temp_passerby_ratio: 0.3,
  gift_probability: 0.05,
  cadence_mode: 'uniform',
  fixed_interval_s: 10,
});

interface StreamMessage {
  time: string;
  data_type: string;
  user_nickname: string;
  text: string;
}

const messageStream = ref<StreamMessage[]>([]);
let ws: WebSocket | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;

const budgetRemaining = computed(() => Math.max(0, 50000 - stats.total_tokens));

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
    const res = await simulatorApi.getStatus();
    Object.assign(status, res.data);
  } catch {
    // Collector not available
  }
}

async function fetchStats() {
  try {
    const res = await simulatorApi.getStats();
    Object.assign(stats, res.data);
  } catch {
    //
  }
}

async function fetchPersonas() {
  try {
    const res = await simulatorApi.getPersonas();
    personas.value = res.data;
  } catch {
    //
  }
}

async function toggleSimulator() {
  toggling.value = true;
  try {
    if (status.is_running) {
      await simulatorApi.stop();
      ElMessage.success('模拟器已停止');
    } else {
      await simulatorApi.start();
      ElMessage.success('模拟器已启动');
    }
    await fetchStatus();
  } catch (e) {
    ElMessage.error('操作失败');
  } finally {
    toggling.value = false;
  }
}

async function updateParams() {
  try {
    await simulatorApi.updateParams({ ...params });
    ElMessage.success('参数已更新');
  } catch {
    ElMessage.error('参数更新失败');
  }
}

async function triggerGiftRain() {
  try {
    await simulatorApi.triggerGiftRain(30);
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
    await simulatorApi.triggerTopicInjection(injectTopic.value);
    ElMessage.success(`话题已注入: ${injectTopic.value}`);
    injectTopic.value = '';
  } catch {
    ElMessage.error('话题注入失败');
  }
}

async function resetBudget() {
  try {
    await simulatorApi.resetTokenBudget();
    ElMessage.success('Token 预算已重置');
    await fetchStats();
  } catch {
    ElMessage.error('重置失败');
  }
}

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/api/v1/ws/simulator/stream`;
  ws = new WebSocket(wsUrl);

  ws.onmessage = event => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'ping') return;
      messageStream.value.unshift({
        time: new Date().toLocaleTimeString('zh-CN', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }),
        data_type: data.type || 'text',
        user_nickname: data.user_nickname || '',
        text: data.text || '',
      });
      if (messageStream.value.length > 50) {
        messageStream.value = messageStream.value.slice(0, 50);
      }
    } catch {
      // ignore malformed messages
    }
  };

  ws.onclose = () => {
    ws = null;
  };

  ws.onerror = () => {
    ws = null;
  };
}

onMounted(async () => {
  await fetchStatus();
  await fetchStats();
  await fetchPersonas();
  connectWebSocket();

  pollTimer = setInterval(async () => {
    await fetchStats();
    await fetchPersonas();
  }, 5000);
});

onUnmounted(() => {
  if (ws) ws.close();
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<style scoped>
.simulator-panel {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height) - var(--spacing-lg) * 2);
  overflow: hidden;
}

.page-header {
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
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

.simulator-content {
  flex: 1;
  overflow-y: auto;
}

.stat-card {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
  text-align: center;
  cursor: default;
}

.stat-card .stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--color-primary);
  line-height: 1.2;
}

.stat-card .stat-value.clickable {
  cursor: pointer;
}

.stat-card .stat-value.clickable:hover {
  color: var(--color-danger);
}

.stat-card .stat-label {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: var(--spacing-xs);
}

.message-stream {
  flex: 1;
  overflow-y: auto;
  font-size: 13px;
}

.message-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px solid var(--border-color-light);
}

.message-item:last-child {
  border-bottom: none;
}

.msg-time {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.msg-nick {
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.msg-text {
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.empty-stream {
  color: var(--text-disabled);
  text-align: center;
  padding: var(--spacing-xl);
}

.message-item.gift .msg-text {
  color: var(--color-warning);
}

.message-item.super_chat .msg-text {
  color: var(--color-danger);
  font-weight: 600;
}
</style>
