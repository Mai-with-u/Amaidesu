<template>
  <div class="devtools">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">DevTools</h1>
        <p class="page-subtitle">开发调试工具集</p>
      </div>
    </header>

    <div class="devtools-content">
      <el-tabs v-model="activeTab" class="devtools-tabs">
        <!-- Tab 1: 消息注入 -->
        <el-tab-pane name="inject">
          <template #label>
            <span class="tab-label">
              <el-icon><Position /></el-icon>
              消息注入
            </span>
          </template>

          <div class="tab-content-wrapper">
            <div class="inject-layout">
              <!-- 注入表单 -->
              <section class="inject-panel">
                <div class="panel-header">
                  <div class="panel-title">
                    <el-icon class="title-icon"><Promotion /></el-icon>
                    <span>消息注入</span>
                  </div>
                  <el-tag size="small" type="warning" effect="plain">开发工具</el-tag>
                </div>

                <el-form
                  :model="injectForm"
                  label-position="top"
                  class="inject-form"
                  @submit.prevent="injectMessage"
                >
                  <el-form-item label="消息内容" required>
                    <el-input
                      v-model="injectForm.text"
                      type="textarea"
                      :rows="4"
                      placeholder="输入要注入的测试消息..."
                      resize="none"
                    />
                  </el-form-item>

                  <div class="form-row">
                    <el-form-item label="来源标识">
                      <el-input v-model="injectForm.source" placeholder="debug_inject" />
                    </el-form-item>

                    <el-form-item label="数据类型">
                      <el-select v-model="injectForm.data_type" style="width: 100%">
                        <el-option label="文本 (text)" value="text" />
                        <el-option label="音频 (audio)" value="audio" />
                        <el-option label="图片 (image)" value="image" />
                      </el-select>
                    </el-form-item>
                  </div>

                  <el-form-item label="重要性权重">
                    <div class="importance-slider">
                      <el-slider
                        v-model="injectForm.importance"
                        :min="0"
                        :max="1"
                        :step="0.1"
                        :show-tooltip="false"
                      />
                      <span class="importance-value">{{
                        (injectForm.importance ?? 0.5).toFixed(1)
                      }}</span>
                    </div>
                  </el-form-item>

                  <el-form-item>
                    <el-button
                      type="primary"
                      :loading="injecting"
                      :disabled="!injectForm.text.trim()"
                      @click="injectMessage"
                    >
                      <el-icon><Promotion /></el-icon>
                      注入消息
                    </el-button>
                  </el-form-item>
                </el-form>
              </section>

              <!-- 注入历史 -->
              <section class="history-panel">
                <div class="panel-header">
                  <div class="panel-title">
                    <el-icon class="title-icon"><Clock /></el-icon>
                    <span>注入历史</span>
                    <el-badge :value="injectHistory.length" :max="99" class="history-badge" />
                  </div>
                  <el-button
                    size="small"
                    :icon="Delete"
                    :disabled="injectHistory.length === 0"
                    @click="injectHistory = []"
                  >
                    清空
                  </el-button>
                </div>

                <el-table
                  v-if="injectHistory.length > 0"
                  :data="injectHistory"
                  size="small"
                  empty-text="暂无注入记录"
                  class="history-table"
                >
                  <el-table-column prop="time" label="时间" width="100">
                    <template #default="{ row }">
                      <span class="time-text">{{ row.time }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="text" label="消息内容">
                    <template #default="{ row }">
                      <div class="message-preview">{{ row.text }}</div>
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="70" align="center">
                    <template #default="{ row }">
                      <el-tag
                        :type="row.success ? 'success' : 'danger'"
                        size="small"
                        effect="plain"
                      >
                        {{ row.success ? '成功' : '失败' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="70" align="center">
                    <template #default="{ row }">
                      <el-button size="small" text :icon="RefreshRight" @click="retryInject(row)">
                        重试
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>

                <el-empty v-else description="暂无注入记录" :image-size="80" />
              </section>
            </div>
          </div>
        </el-tab-pane>

        <!-- Tab 2: EventBus 统计 -->
        <el-tab-pane name="eventbus">
          <template #label>
            <span class="tab-label">
              <el-icon><DataAnalysis /></el-icon>
              EventBus
            </span>
          </template>

          <div class="tab-content-wrapper">
            <section class="eventbus-panel">
              <div class="panel-header">
                <div class="panel-title">
                  <el-icon class="title-icon"><DataAnalysis /></el-icon>
                  <span>EventBus 统计</span>
                </div>
                <el-button
                  size="small"
                  :icon="Refresh"
                  :loading="statsLoading"
                  @click="fetchEventBusStats"
                >
                  刷新
                </el-button>
              </div>

              <div class="stats-overview">
                <div class="stat-card">
                  <div class="stat-value">{{ eventBusStats?.total_events || 0 }}</div>
                  <div class="stat-label">总事件数</div>
                </div>
                <div class="stat-card">
                  <div class="stat-value">{{ eventBusStats?.total_subscribers || 0 }}</div>
                  <div class="stat-label">总订阅数</div>
                </div>
              </div>

              <div v-if="eventsByName.length > 0" class="events-table">
                <h4>事件分布</h4>
                <el-table :data="eventsByName" size="small" max-height="300">
                  <el-table-column prop="name" label="事件名称">
                    <template #default="{ row }">
                      <code class="event-name">{{ row.name }}</code>
                    </template>
                  </el-table-column>
                  <el-table-column prop="count" label="触发次数" width="120" align="right">
                    <template #default="{ row }">
                      <span class="event-count">{{ row.count }}</span>
                    </template>
                  </el-table-column>
                </el-table>
              </div>

              <el-empty v-else description="暂无事件统计数据" :image-size="100" />
            </section>
          </div>
        </el-tab-pane>

        <!-- Tab 3: MaiBot 控制 -->
        <el-tab-pane name="maibot">
          <template #label>
            <span class="tab-label">
              <el-icon><Pointer /></el-icon>
              MaiBot 控制
            </span>
          </template>

          <div class="tab-content-wrapper">
            <div class="inject-layout">
              <!-- 控制表单 -->
              <section class="inject-panel">
                <div class="panel-header">
                  <div class="panel-title">
                    <el-icon class="title-icon"><Pointer /></el-icon>
                    <span>MaiBot 动作控制</span>
                  </div>
                  <el-tag size="small" type="success" effect="plain">外部控制</el-tag>
                </div>

                <el-form
                  :model="maibotForm"
                  label-position="top"
                  class="inject-form"
                  @submit.prevent="triggerMaibotAction"
                >
                  <el-form-item label="动作类型">
                    <el-select
                      v-model="maibotForm.action"
                      placeholder="选择动作类型"
                      clearable
                      style="width: 100%"
                    >
                      <el-option label="hotkey (热键)" value="hotkey" />
                      <el-option label="expression (表情)" value="expression" />
                      <el-option label="motion (动作)" value="motion" />
                    </el-select>
                  </el-form-item>

                  <!-- 热键参数 -->
                  <el-form-item v-if="maibotForm.action === 'hotkey'" label="选择热键">
                    <el-select
                      v-model="maibotForm.hotkey"
                      placeholder="选择预设热键"
                      style="width: 100%"
                    >
                      <el-option label="smile (微笑)" value="smile" />
                      <el-option label="wave (挥手)" value="wave" />
                      <el-option label="nod (点头)" value="nod" />
                      <el-option label="shake (摇头)" value="shake" />
                      <el-option label="clap (鼓掌)" value="clap" />
                      <el-option label="dance (跳舞)" value="dance" />
                      <el-option label="jump (跳跃)" value="jump" />
                      <el-option label="sit (坐下)" value="sit" />
                      <el-option label="lie (躺下)" value="lie" />
                      <el-option label="run (跑步)" value="run" />
                    </el-select>
                  </el-form-item>

                  <!-- 表情参数 -->
                  <el-form-item v-if="maibotForm.action === 'expression'" label="选择表情">
                    <el-select
                      v-model="maibotForm.expression"
                      placeholder="选择预设表情"
                      style="width: 100%"
                    >
                      <el-option label="happy (开心)" value="happy" />
                      <el-option label="sad (难过)" value="sad" />
                      <el-option label="angry (生气)" value="angry" />
                      <el-option label="surprised (惊讶)" value="surprised" />
                      <el-option label="scared (害怕)" value="scared" />
                      <el-option label="embarrassed (尴尬)" value="embarrassed" />
                      <el-option label="cry (哭泣)" value="cry" />
                      <el-option label="laugh (大笑)" value="laugh" />
                    </el-select>
                  </el-form-item>

                  <!-- 动作参数 -->
                  <el-form-item v-if="maibotForm.action === 'motion'" label="选择动作">
                    <el-select
                      v-model="maibotForm.motion"
                      placeholder="选择预设动作"
                      style="width: 100%"
                    >
                      <el-option label="wave (挥手)" value="wave" />
                      <el-option label="nod (点头)" value="nod" />
                      <el-option label="shake_head (摇头)" value="shake_head" />
                      <el-option label="dance (跳舞)" value="dance" />
                      <el-option label="jump (跳跃)" value="jump" />
                      <el-option label="run (跑步)" value="run" />
                      <el-option label="sit_down (坐下)" value="sit_down" />
                      <el-option label="stand_up (站起来)" value="stand_up" />
                      <el-option label="walk (走路)" value="walk" />
                      <el-option label="attack (攻击)" value="attack" />
                    </el-select>
                  </el-form-item>

                  <el-form-item label="情绪类型">
                    <el-select
                      v-model="maibotForm.emotion"
                      placeholder="选择情绪"
                      clearable
                      style="width: 100%"
                    >
                      <el-option label="happy (开心)" value="happy" />
                      <el-option label="neutral (中性)" value="neutral" />
                      <el-option label="sad (难过)" value="sad" />
                      <el-option label="angry (生气)" value="angry" />
                      <el-option label="excited (兴奋)" value="excited" />
                      <el-option label="shy (害羞)" value="shy" />
                    </el-select>
                  </el-form-item>

                  <el-form-item label="优先级">
                    <div class="importance-slider">
                      <el-slider
                        v-model="maibotForm.priority"
                        :min="0"
                        :max="100"
                        :show-tooltip="false"
                      />
                      <span class="importance-value">{{ maibotForm.priority }}</span>
                    </div>
                  </el-form-item>

                  <el-form-item label="回复文本 (可选)">
                    <el-input
                      v-model="maibotForm.text"
                      type="textarea"
                      :rows="2"
                      placeholder="可选的回复文本..."
                      resize="none"
                    />
                  </el-form-item>

                  <el-form-item>
                    <el-button
                      type="primary"
                      :loading="maibotLoading"
                      :disabled="!maibotForm.action && !maibotForm.emotion"
                      @click="triggerMaibotAction"
                    >
                      <el-icon><Pointer /></el-icon>
                      触发动作/情绪
                    </el-button>
                  </el-form-item>
                </el-form>
              </section>

              <!-- 控制历史 -->
              <section class="history-panel">
                <div class="panel-header">
                  <div class="panel-title">
                    <el-icon class="title-icon"><Clock /></el-icon>
                    <span>控制历史</span>
                    <el-badge :value="maibotHistory.length" :max="99" class="history-badge" />
                  </div>
                  <el-button
                    size="small"
                    :icon="Delete"
                    :disabled="maibotHistory.length === 0"
                    @click="maibotHistory = []"
                  >
                    清空
                  </el-button>
                </div>

                <el-table
                  v-if="maibotHistory.length > 0"
                  :data="maibotHistory"
                  size="small"
                  empty-text="暂无控制记录"
                  class="history-table"
                >
                  <el-table-column prop="time" label="时间" width="100">
                    <template #default="{ row }">
                      <span class="time-text">{{ row.time }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="动作/情绪">
                    <template #default="{ row }">
                      <div class="message-preview">
                        <el-tag v-if="row.action" size="small" type="warning">{{
                          row.action
                        }}</el-tag>
                        <el-tag v-if="row.emotion" size="small" type="success">{{
                          row.emotion
                        }}</el-tag>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="70" align="center">
                    <template #default="{ row }">
                      <el-tag
                        :type="row.success ? 'success' : 'danger'"
                        size="small"
                        effect="plain"
                      >
                        {{ row.success ? '成功' : '失败' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>

                <el-empty v-else description="暂无控制记录" :image-size="80" />
              </section>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import {
  Position,
  DataAnalysis,
  Clock,
  Promotion,
  Refresh,
  Delete,
  RefreshRight,
  Pointer,
} from '@element-plus/icons-vue';
import { debugApi, maibotApi } from '@/api';
import type { EventBusStatsResponse, InjectMessageRequest } from '@/types';

// Tab state
const activeTab = ref('inject');

// ============ 消息注入 Tab ============
const injectForm = ref<InjectMessageRequest>({
  source: 'debug_inject',
  text: '',
  data_type: 'text',
  importance: 0.5,
});

const injecting = ref(false);

interface InjectHistoryItem {
  time: string;
  text: string;
  source: string;
  data_type: string;
  importance: number;
  success: boolean;
}

const injectHistory = ref<InjectHistoryItem[]>([]);

async function injectMessage() {
  if (!injectForm.value.text.trim()) {
    ElMessage.warning('请输入消息内容');
    return;
  }

  injecting.value = true;
  const time = new Date().toLocaleString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  try {
    await debugApi.injectMessage(injectForm.value);
    injectHistory.value.unshift({
      time,
      text: injectForm.value.text,
      source: injectForm.value.source || 'debug_inject',
      data_type: injectForm.value.data_type || 'text',
      importance: injectForm.value.importance || 0.5,
      success: true,
    });
    ElMessage.success('消息已注入系统');
    injectForm.value.text = '';
  } catch (_error) {
    injectHistory.value.unshift({
      time,
      text: injectForm.value.text,
      source: injectForm.value.source || 'debug_inject',
      data_type: injectForm.value.data_type || 'text',
      importance: injectForm.value.importance || 0.5,
      success: false,
    });
    ElMessage.error('消息注入失败');
  } finally {
    injecting.value = false;
  }
}

async function retryInject(item: InjectHistoryItem) {
  injectForm.value = {
    source: item.source,
    text: item.text,
    data_type: item.data_type,
    importance: item.importance,
  };
  await injectMessage();
}

// ============ MaiBot 控制 Tab ============
interface MaibotHistoryItem {
  time: string;
  action?: string;
  emotion?: string;
  priority: number;
  text?: string;
  success: boolean;
  error?: string;
}

const maibotForm = ref({
  action: '',
  hotkey: '',
  expression: '',
  motion: '',
  emotion: '',
  priority: 50,
  text: '',
});

const maibotLoading = ref(false);
const maibotHistory = ref<MaibotHistoryItem[]>([]);

async function triggerMaibotAction() {
  if (!maibotForm.value.action && !maibotForm.value.emotion) {
    ElMessage.warning('请选择动作或情绪');
    return;
  }

  maibotLoading.value = true;
  const time = new Date().toLocaleString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  // 根据动作类型构建参数
  let actionParams = {};
  if (maibotForm.value.action === 'hotkey' && maibotForm.value.hotkey) {
    actionParams = { hotkey: maibotForm.value.hotkey };
  } else if (maibotForm.value.action === 'expression' && maibotForm.value.expression) {
    actionParams = { expression: maibotForm.value.expression };
  } else if (maibotForm.value.action === 'motion' && maibotForm.value.motion) {
    actionParams = { motion: maibotForm.value.motion };
  }

  const requestData = {
    action: maibotForm.value.action || undefined,
    action_params: Object.keys(actionParams).length > 0 ? actionParams : undefined,
    emotion: maibotForm.value.emotion || undefined,
    priority: maibotForm.value.priority,
    text: maibotForm.value.text || undefined,
  };

  try {
    const response = await maibotApi.triggerAction(requestData);

    maibotHistory.value.unshift({
      time,
      action: maibotForm.value.action || undefined,
      emotion: maibotForm.value.emotion || undefined,
      priority: maibotForm.value.priority,
      text: maibotForm.value.text || undefined,
      success: response.data.success,
      error: response.data.error,
    });

    if (response.data.success) {
      ElMessage.success('动作/情绪触发成功');
    } else {
      ElMessage.error(response.data.error || '触发失败');
    }
  } catch (error) {
    console.error('Maibot API error:', error);
    maibotHistory.value.unshift({
      time,
      action: maibotForm.value.action || undefined,
      emotion: maibotForm.value.emotion || undefined,
      priority: maibotForm.value.priority,
      text: maibotForm.value.text || undefined,
      success: false,
      error: String(error),
    });
    ElMessage.error('请求失败');
  } finally {
    maibotLoading.value = false;
  }
}

// ============ EventBus 统计 Tab ============
const statsLoading = ref(false);
const eventBusStats = ref<EventBusStatsResponse | null>(null);

const eventsByName = computed(() => {
  if (!eventBusStats.value?.events_by_name) return [];
  return Object.entries(eventBusStats.value.events_by_name)
    .map(([name, count]) => ({
      name,
      count,
    }))
    .sort((a, b) => b.count - a.count);
});

async function fetchEventBusStats() {
  statsLoading.value = true;
  try {
    const response = await debugApi.getEventBusStats();
    eventBusStats.value = response.data;
  } catch (error) {
    console.error('Failed to fetch EventBus stats:', error);
    ElMessage.error('获取统计数据失败');
  } finally {
    statsLoading.value = false;
  }
}

// 初始化
onMounted(() => {
  fetchEventBusStats();
});
</script>

<style scoped>
.devtools {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--header-height) - var(--spacing-lg) * 2);
  overflow: hidden;
}

.page-header {
  margin-bottom: var(--spacing-md);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}

.devtools-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-color-light);
  box-shadow: var(--shadow-sm);
}

.devtools-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.devtools-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 var(--spacing-lg);
  background: var(--bg-hover);
  border-bottom: 1px solid var(--border-color-light);
}

.devtools-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.devtools-tabs :deep(.el-tabs__item) {
  height: 48px;
  line-height: 48px;
  font-size: 14px;
  font-weight: 500;
  padding: 0 var(--spacing-lg);
}

.tab-label {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.devtools-tabs :deep(.el-tabs__content) {
  flex: 1;
  overflow: hidden;
}

.devtools-tabs :deep(.el-tab-pane) {
  height: 100%;
}

.tab-content-wrapper {
  height: 100%;
  padding: var(--spacing-lg);
  overflow-y: auto;
}

/* 消息注入 Tab 样式 */
.inject-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-lg);
  height: 100%;
}

.inject-panel,
.history-panel {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-md);
  padding-bottom: var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.title-icon {
  width: 18px;
  height: 18px;
  color: var(--color-primary);
}

.inject-form {
  flex: 1;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-md);
}

.importance-slider {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.importance-slider :deep(.el-slider) {
  flex: 1;
}

.importance-value {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--color-primary);
  min-width: 32px;
  text-align: right;
}

.history-panel {
  min-height: 0;
  overflow: hidden;
}

.history-badge {
  margin-left: var(--spacing-sm);
}

.history-table {
  flex: 1;
}

.time-text {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
}

.message-preview {
  font-size: 13px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}

/* EventBus 统计 Tab 样式 */
.eventbus-panel {
  background: var(--bg-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color-light);
  padding: var(--spacing-lg);
}

.stats-overview {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-lg);
}

.stat-card {
  background: var(--bg-hover);
  border-radius: var(--radius-md);
  padding: var(--spacing-lg);
  text-align: center;
}

.stat-value {
  font-size: 32px;
  font-weight: 700;
  color: var(--color-primary);
  line-height: 1.2;
}

.stat-label {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: var(--spacing-xs);
}

.events-table h4 {
  margin: 0 0 var(--spacing-md);
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
}

.event-name {
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--bg-hover);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  color: var(--text-primary);
}

.event-count {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--color-primary);
}

/* 滚动条样式 */
.tab-content-wrapper::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.tab-content-wrapper::-webkit-scrollbar-track {
  background: var(--bg-elevated);
}

.tab-content-wrapper::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 4px;
}

.tab-content-wrapper::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}

/* 响应式 */
@media (max-width: 1200px) {
  .inject-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .form-row {
    grid-template-columns: 1fr;
  }

  .stats-overview {
    grid-template-columns: 1fr;
  }
}
</style>
