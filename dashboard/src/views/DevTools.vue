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

        <!-- Tab 3: 主播发言测试 -->
        <el-tab-pane name="streamer">
          <template #label>
            <span class="tab-label">
              <el-icon><ChatLineRound /></el-icon>
              主播发言测试
            </span>
          </template>

          <div class="tab-content-wrapper">
            <!-- Agent 未启用空态 -->
            <el-alert
              v-if="streamerStatus !== null && !streamerStatus.available"
              type="warning"
              title="主播 Agent 未启用"
              :description="
                streamerStatus.message ||
                '请在 agents.toml 的 [agents].enabled 启用 streamer 后重启。'
              "
              show-icon
              :closable="false"
              style="margin-bottom: 16px"
            />

            <div class="streamer-layout">
              <!-- 左列：触发面板 + 决策结果 -->
              <div class="streamer-left">
                <section class="inject-panel">
                  <div class="panel-header">
                    <div class="panel-title">
                      <el-icon class="title-icon"><ChatLineRound /></el-icon>
                      <span>触发决策</span>
                    </div>
                    <el-tag size="small" type="warning" effect="plain">开发工具</el-tag>
                  </div>

                  <el-form label-position="top">
                    <el-form-item label="触发模式">
                      <el-radio-group v-model="testMode" :disabled="testing">
                        <el-radio value="danmaku">模拟弹幕（真实决策链路）</el-radio>
                        <el-radio value="proactive">主动发言（直跑，绕过限流）</el-radio>
                      </el-radio-group>
                    </el-form-item>

                    <template v-if="testMode === 'danmaku'">
                      <el-form-item label="弹幕批次">
                        <div class="danmaku-rows">
                          <div v-for="(item, idx) in testDanmaku" :key="idx" class="danmaku-row">
                            <el-input
                              v-model="item.nickname"
                              placeholder="昵称（默认：测试观众）"
                              size="small"
                              class="danmaku-nickname"
                              :disabled="testing"
                            />
                            <el-input
                              v-model="item.text"
                              placeholder="弹幕文本"
                              size="small"
                              class="danmaku-text"
                              :disabled="testing"
                              @keydown.enter.prevent="runTest"
                            />
                            <el-button
                              size="small"
                              text
                              type="danger"
                              :disabled="testing || testDanmaku.length <= 1"
                              @click="removeDanmaku(idx)"
                            >
                              ✕
                            </el-button>
                          </div>
                          <el-button
                            size="small"
                            text
                            type="primary"
                            :disabled="testing"
                            @click="addDanmaku"
                          >
                            + 添加弹幕
                          </el-button>
                        </div>
                      </el-form-item>

                      <el-form-item>
                        <el-checkbox v-model="testForced" :disabled="testing">
                          强制响应（forced，豁免低置信度降级）
                        </el-checkbox>
                      </el-form-item>
                    </template>

                    <el-alert
                      v-if="testMode === 'proactive' && !proactiveEnabled"
                      type="info"
                      :closable="false"
                      show-icon
                      title="主动发言默认关闭（proactive_enabled=false）"
                      description="直跑模式不受该开关影响；只有右侧「真实限流链路触发」才依赖它。"
                      style="margin-bottom: 12px"
                    />

                    <el-form-item>
                      <el-button
                        type="primary"
                        :loading="testing"
                        :disabled="!streamerAvailable || !canRunTest"
                        @click="runTest"
                      >
                        <el-icon><Promotion /></el-icon>
                        触发决策（两段 LLM，约 10~30s）
                      </el-button>
                    </el-form-item>
                  </el-form>
                </section>

                <!-- 决策结果卡片 -->
                <section class="inject-panel">
                  <div class="panel-header">
                    <div class="panel-title">
                      <el-icon class="title-icon"><Document /></el-icon>
                      <span>决策结果</span>
                    </div>
                    <el-tag v-if="testResult?.elapsed_ms != null" size="small" effect="plain">
                      {{ (testResult.elapsed_ms / 1000).toFixed(1) }}s
                    </el-tag>
                  </div>

                  <el-empty v-if="!testResult" description="尚未触发决策" :image-size="80" />

                  <template v-else>
                    <!-- 失败提示（API 调用层失败） -->
                    <el-alert
                      v-if="!testResult.success"
                      type="error"
                      :title="testResult.message || '触发失败'"
                      :description="testResult.error || ''"
                      show-icon
                      :closable="false"
                      style="margin-bottom: 12px"
                    />

                    <template v-if="testResult.plan">
                      <div class="result-section-title">
                        Stage 1 · Planner
                        <el-tag
                          :type="testResult.plan.should_reply ? 'success' : 'info'"
                          size="small"
                          effect="plain"
                        >
                          {{ testResult.plan.should_reply ? '回复' : '保持沉默' }}
                        </el-tag>
                        <span v-if="testResult.plan.confidence != null" class="result-meta">
                          confidence {{ testResult.plan.confidence.toFixed(2) }}
                        </span>
                      </div>
                      <el-descriptions :column="1" size="small" border class="plan-desc">
                        <el-descriptions-item label="target">
                          {{ testResult.plan.target || '—' }}
                        </el-descriptions-item>
                        <el-descriptions-item label="topic_summary">
                          {{ testResult.plan.topic_summary || '—' }}
                        </el-descriptions-item>
                        <el-descriptions-item label="reply_guidance">
                          {{ testResult.plan.reply_guidance || '—' }}
                        </el-descriptions-item>
                      </el-descriptions>

                      <!-- 沉默提示 -->
                      <el-alert
                        v-if="!testResult.plan.should_reply"
                        type="info"
                        title="Planner 决定不回复"
                        description="如需主播一定开口，勾选「强制响应」或改用主动发言直跑模式。"
                        show-icon
                        :closable="false"
                        style="margin-top: 12px"
                      />
                    </template>

                    <template v-if="testResult.speech">
                      <div class="result-section-title" style="margin-top: 16px">
                        Stage 2 · 主播发言
                        <el-tag
                          v-if="testResult.emotion"
                          size="small"
                          type="success"
                          effect="plain"
                        >
                          {{ testResult.emotion }}
                        </el-tag>
                      </div>
                      <div class="speech-bubble">
                        {{ testResult.speech }}
                      </div>
                      <div v-if="testResult.utterance_id" class="utterance-id">
                        {{ testResult.utterance_id }}
                      </div>
                    </template>

                    <!-- 决策级失败（Planner/Replyer） -->
                    <el-alert
                      v-if="testResult.success && testResult.error"
                      type="warning"
                      :title="testResult.error"
                      show-icon
                      :closable="false"
                      style="margin-top: 12px"
                    />
                  </template>
                </section>
              </div>

              <!-- 右列：实时发言流 + 统计 + 真实链路触发 -->
              <div class="streamer-right">
                <section class="history-panel">
                  <div class="panel-header">
                    <div class="panel-title">
                      <el-icon class="title-icon"><Clock /></el-icon>
                      <span>主播发言流</span>
                      <el-badge :value="speechStream.length" :max="99" class="history-badge" />
                    </div>
                    <el-button
                      size="small"
                      :icon="Delete"
                      :disabled="speechStream.length === 0"
                      @click="speechStream = []"
                    >
                      清空
                    </el-button>
                  </div>

                  <div v-if="speechStream.length > 0" class="speech-stream">
                    <div v-for="item in speechStream" :key="item.utterance_id" class="speech-item">
                      <div class="speech-item-header">
                        <span class="speech-item-time">{{ item.time }}</span>
                        <el-tag v-if="item.emotion" size="small" effect="plain">
                          {{ item.emotion }}
                        </el-tag>
                      </div>
                      <div class="speech-item-text">{{ item.text }}</div>
                      <div class="utterance-id">{{ item.utterance_id }}</div>
                    </div>
                  </div>
                  <el-empty v-else description="等待 streamer.speech 事件" :image-size="80" />
                  <p class="ws-hint">
                    由 WebSocket <code>streamer.speech</code> 事件驱动：手动触发的决策与真实
                    弹幕引发的发言都会汇入此处（TTS/字幕联动可用 utterance_id 对账）。
                  </p>
                </section>

                <section class="inject-panel">
                  <div class="panel-header">
                    <div class="panel-title">
                      <el-icon class="title-icon"><DataAnalysis /></el-icon>
                      <span>运行统计</span>
                    </div>
                    <el-button
                      size="small"
                      :icon="Refresh"
                      :loading="statusLoading"
                      @click="fetchStreamerStatus"
                    >
                      刷新
                    </el-button>
                  </div>

                  <div v-if="streamerStatus?.available" class="stats-overview">
                    <div class="stat-card">
                      <div class="stat-value">{{ statistics.total_replies ?? 0 }}</div>
                      <div class="stat-label">总回复</div>
                    </div>
                    <div class="stat-card">
                      <div class="stat-value">{{ statistics.total_proactive ?? 0 }}</div>
                      <div class="stat-label">主动发言</div>
                    </div>
                    <div class="stat-card">
                      <div class="stat-value">{{ statistics.planner_failures ?? 0 }}</div>
                      <div class="stat-label">Planner 失败</div>
                    </div>
                    <div class="stat-card">
                      <div class="stat-value">{{ statistics.replyer_failures ?? 0 }}</div>
                      <div class="stat-label">Replyer 失败</div>
                    </div>
                  </div>
                  <el-empty v-else description="Agent 未启用" :image-size="60" />
                </section>

                <section class="inject-panel">
                  <div class="panel-header">
                    <div class="panel-title">
                      <el-icon class="title-icon"><Position /></el-icon>
                      <span>真实限流链路触发</span>
                    </div>
                  </div>
                  <el-input
                    v-model="proactiveTopicHint"
                    placeholder="话题提示（可选，仅用于日志）"
                    size="small"
                    style="margin-bottom: 8px"
                    :disabled="triggeringProactive"
                  />
                  <el-button
                    size="small"
                    type="warning"
                    :loading="triggeringProactive"
                    :disabled="!streamerAvailable"
                    @click="triggerProactive"
                  >
                    置位外部主动发言（走真实限流）
                  </el-button>
                  <p class="ws-hint">
                    仅置位 pending：下个 flush tick 由 ProactiveTrigger 判定（防接龙 / 每小时上限 /
                    话题要求），任一不满足则静默丢弃——用于测试限流本身。
                    <template v-if="!proactiveEnabled">
                      当前 <code>proactive_enabled=false</code>，真实链路会静默失效。
                    </template>
                  </p>
                </section>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { ElMessage } from 'element-plus';
import {
  Position,
  DataAnalysis,
  Clock,
  Promotion,
  Refresh,
  Delete,
  RefreshRight,
  ChatLineRound,
  Document,
} from '@element-plus/icons-vue';
import { debugApi, streamerApi } from '@/api';
import { useWebSocketStore } from '@/stores/websocket';
import type {
  EventBusStatsResponse,
  InjectMessageRequest,
  StreamerSpeechEventData,
  StreamerStatusResponse,
  StreamerTestDecisionResponse,
  WebSocketMessage,
} from '@/types';

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

// ============ 主播发言测试 Tab ============
type TestMode = 'danmaku' | 'proactive';

interface DanmakuDraft {
  nickname: string;
  text: string;
}

interface SpeechStreamItem {
  utterance_id: string;
  text: string;
  emotion?: string | null;
  time: string;
}

const testMode = ref<TestMode>('danmaku');
const testDanmaku = ref<DanmakuDraft[]>([{ nickname: '', text: '' }]);
const testForced = ref(false);
const testing = ref(false);
const testResult = ref<StreamerTestDecisionResponse | null>(null);

const streamerStatus = ref<StreamerStatusResponse | null>(null);
const statusLoading = ref(false);

const proactiveTopicHint = ref('');
const triggeringProactive = ref(false);

const speechStream = ref<SpeechStreamItem[]>([]);
const MAX_SPEECH_STREAM = 50;

const wsStore = useWebSocketStore();

const streamerAvailable = computed(() => streamerStatus.value?.available ?? false);
const proactiveEnabled = computed(() => streamerStatus.value?.config?.proactive_enabled ?? false);
const statistics = computed(() => streamerStatus.value?.statistics ?? {});

const canRunTest = computed(() => {
  if (testMode.value === 'proactive') return true;
  return testDanmaku.value.some(d => d.text.trim());
});

function addDanmaku() {
  testDanmaku.value.push({ nickname: '', text: '' });
}

function removeDanmaku(idx: number) {
  testDanmaku.value.splice(idx, 1);
}

async function fetchStreamerStatus() {
  statusLoading.value = true;
  try {
    const res = await streamerApi.getStatus();
    streamerStatus.value = res.data;
  } catch (err) {
    console.error('获取主播 Agent 状态失败:', err);
  } finally {
    statusLoading.value = false;
  }
}

async function runTest() {
  if (!canRunTest.value || testing.value) return;
  testing.value = true;
  testResult.value = null;
  try {
    const request =
      testMode.value === 'proactive'
        ? { proactive: true }
        : {
            batch: testDanmaku.value
              .filter(d => d.text.trim())
              .map(d => ({ nickname: d.nickname.trim() || undefined, text: d.text.trim() })),
            forced: testForced.value,
          };
    const res = await streamerApi.testDecision(request);
    testResult.value = res.data;
    if (!res.data.success) {
      ElMessage.warning(res.data.message || res.data.error || '触发被拒绝');
    } else if (res.data.speech) {
      ElMessage.success('主播已发言');
    }
    // 触发后刷新统计（无论成败都会推进计数器）
    void fetchStreamerStatus();
  } catch (err) {
    ElMessage.error(err instanceof Error ? `触发失败：${err.message}` : '触发失败');
  } finally {
    testing.value = false;
  }
}

async function triggerProactive() {
  triggeringProactive.value = true;
  try {
    const res = await streamerApi.triggerProactive({
      topic_hint: proactiveTopicHint.value.trim() || undefined,
    });
    if (res.data.success) {
      ElMessage.info(res.data.message);
    } else {
      ElMessage.warning(res.data.message);
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '触发失败');
  } finally {
    triggeringProactive.value = false;
  }
}

// WS streamer.speech → 发言流
function handleStreamerSpeech(msg: WebSocketMessage): void {
  if (msg.type !== 'streamer.speech') return;
  const data = msg.data as unknown as StreamerSpeechEventData;
  if (!data?.utterance_id || typeof data.text !== 'string') return;
  // WS message.timestamp 为秒；发言时间取事件发布时刻（若 payload 携带）
  const tsMs = data.timestamp_ms ?? msg.timestamp * 1000;
  speechStream.value.unshift({
    utterance_id: data.utterance_id,
    text: data.text,
    emotion: data.emotion,
    time: new Date(tsMs).toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }),
  });
  if (speechStream.value.length > MAX_SPEECH_STREAM) {
    speechStream.value.length = MAX_SPEECH_STREAM;
  }
}

// 初始化
onMounted(() => {
  fetchEventBusStats();
  void fetchStreamerStatus();
  wsStore.subscribe(handleStreamerSpeech);
});

onUnmounted(() => {
  wsStore.unsubscribe(handleStreamerSpeech);
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

/* 主播发言测试 Tab 样式 */
.streamer-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: var(--spacing-lg);
}

.streamer-left,
.streamer-right {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-lg);
  min-width: 0;
}

.danmaku-rows {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  width: 100%;
}

.danmaku-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.danmaku-nickname {
  flex: 0 0 180px;
}

.danmaku-text {
  flex: 1;
  min-width: 0;
}

.result-section-title {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: var(--spacing-sm);
}

.result-meta {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
}

.plan-desc {
  margin-bottom: 0;
}

.speech-bubble {
  background: var(--bg-hover);
  border-left: 3px solid var(--color-primary);
  border-radius: var(--radius-md);
  padding: var(--spacing-md) var(--spacing-lg);
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-primary);
  white-space: pre-wrap;
}

.utterance-id {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-placeholder);
  margin-top: var(--spacing-xs);
}

.speech-stream {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.speech-item {
  background: var(--bg-hover);
  border-radius: var(--radius-md);
  padding: var(--spacing-sm) var(--spacing-md);
}

.speech-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-sm);
  margin-bottom: 4px;
}

.speech-item-time {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
}

.speech-item-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
}

.ws-hint {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.7;
  margin: var(--spacing-sm) 0 0;
}

.ws-hint code {
  font-family: var(--font-mono);
  font-size: 11px;
  background: var(--bg-hover);
  padding: 1px 4px;
  border-radius: 3px;
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

  .streamer-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .form-row {
    grid-template-columns: 1fr;
  }

  .stats-overview {
    grid-template-columns: 1fr 1fr;
  }

  .danmaku-row {
    flex-direction: column;
    align-items: stretch;
  }

  .danmaku-nickname {
    flex: none;
  }
}
</style>
