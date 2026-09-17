<template>
  <div class="detail-page">
    <!-- ============================================================ -->
    <!-- 档案头：返回 + 身份 + 统计徽章                                  -->
    <!-- ============================================================ -->
    <header v-if="detail" class="profile-head">
      <router-link class="back-link" to="/viewers">← 观众列表</router-link>
      <h1 class="profile-name" :title="detail.user_name || '（未留名）'">
        {{ detail.user_name || '（未留名）' }}
      </h1>
      <code class="mono profile-id" :title="detail.user_id">{{ detail.user_id }}</code>
      <span class="head-badge">发言 {{ detail.message_count }}</span>
      <span class="head-badge">被回复 {{ detail.replied_count }}</span>
      <span class="head-badge">最后活跃 {{ relativeTime(detail.last_active_ms) }}</span>
    </header>
    <header v-else class="profile-head">
      <router-link class="back-link" to="/viewers">← 观众列表</router-link>
      <span class="profile-name loading-name">{{ loadError ?? '加载中…' }}</span>
    </header>

    <!-- ============================================================ -->
    <!-- 统计卡行：礼物 / SC / 场次 / 首次出现                           -->
    <!-- ============================================================ -->
    <section v-if="detail" class="stat-cards">
      <div class="stat-card">
        <span class="stat-value mono">{{ detail.gift_total_count }}</span>
        <span class="stat-label">礼物件数</span>
      </div>
      <div class="stat-card">
        <span class="stat-value mono">¥{{ detail.sc_total_amount.toFixed(2) }}</span>
        <span class="stat-label">SC 总额（{{ detail.sc_total_count }} 条）</span>
      </div>
      <div class="stat-card">
        <span class="stat-value mono">{{ detail.session_count }}</span>
        <span class="stat-label">参与场次</span>
      </div>
      <div class="stat-card">
        <span class="stat-value mono">{{ firstSeenText }}</span>
        <span class="stat-label">首次出现</span>
      </div>
    </section>

    <!-- ============================================================ -->
    <!-- 三区：对话 / 贡献 / 场次                                        -->
    <!-- ============================================================ -->
    <section v-if="detail" class="tabs-card">
      <el-tabs v-model="activeTab">
        <!-- ===== 对话：观众消息与主播回复交织 ===== -->
        <el-tab-pane label="对话" name="dialogue">
          <div class="dialogue-wrap">
            <div class="dialogue-toolbar">
              <el-button
                size="small"
                :disabled="dialogueExhausted"
                :loading="dialogueLoading"
                @click="loadEarlier"
              >
                {{ dialogueExhausted ? '已到最早' : '加载更早' }}
              </el-button>
              <span class="dialogue-hint">
                观众消息与主播回复按时间交织 · 现存记录 {{ dialogueItems.length }} 条
                <template v-if="dialogueExhausted && detail.message_count > dialogueViewerCount">
                  · 发言累计 {{ detail.message_count }}（部分已随场次清理）
                </template>
              </span>
            </div>
            <div ref="dialogueScroll" class="dialogue-scroll">
              <p v-if="dialogueItems.length === 0 && !dialogueLoading" class="empty-hint">
                该观众还没有可回看的消息
              </p>
              <div
                v-for="item in dialogueItems"
                :key="item.id"
                class="bubble-row"
                :class="item.kind === 'reply' ? 'is-reply' : 'is-viewer'"
              >
                <div class="bubble">
                  <div class="bubble-meta">
                    <span class="bubble-actor">{{
                      item.kind === 'reply' ? '主播' : detail.user_name || '观众'
                    }}</span>
                    <span
                      v-if="item.message_type && item.message_type !== 'danmaku'"
                      class="type-tag"
                      >{{ typeLabel(item.message_type) }}</span
                    >
                    <span v-if="item.simulated" class="sim-tag">模拟</span>
                    <span class="bubble-time mono" :title="formatTime(item.timestamp_ms)">
                      {{ formatTime(item.timestamp_ms) }}
                    </span>
                  </div>
                  <p class="bubble-text">{{ item.content }}</p>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- ===== 贡献：礼物与 SC 明细 ===== -->
        <el-tab-pane label="贡献" name="contributions">
          <div v-loading="contribLoading" class="contrib-wrap">
            <p class="scope-hint">明细按现存记录展示；列表计数为历史累计，含已清理的场次</p>
            <template v-if="contrib">
              <h3 class="contrib-title">
                礼物（{{ contrib.gifts.length }} 笔，共 {{ contrib.gift_total_count }} 件）
              </h3>
              <el-table
                v-if="contrib.gifts.length > 0"
                :data="contrib.gifts"
                size="small"
                stripe
                :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
              >
                <el-table-column label="时间" width="170">
                  <template #default="{ row }">
                    <span class="mono">{{ formatTime(row.timestamp_ms) }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="gift_name" label="礼物" min-width="120" />
                <el-table-column prop="gift_count" label="件数" width="80" />
                <el-table-column label="场次" width="90">
                  <template #default="{ row }">
                    <span class="mono">#{{ row.live_session_id ?? '—' }}</span>
                  </template>
                </el-table-column>
              </el-table>
              <p v-else class="empty-hint">暂无礼物记录</p>

              <h3 class="contrib-title">
                醒目留言（{{ contrib.super_chats.length }} 笔，共 ¥{{
                  contrib.sc_total_amount.toFixed(2)
                }}）
              </h3>
              <el-table
                v-if="contrib.super_chats.length > 0"
                :data="contrib.super_chats"
                size="small"
                stripe
                :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
              >
                <el-table-column label="时间" width="170">
                  <template #default="{ row }">
                    <span class="mono">{{ formatTime(row.timestamp_ms) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="金额" width="100">
                  <template #default="{ row }">
                    <span class="mono sc-amount">¥{{ row.amount.toFixed(2) }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="message" label="留言" min-width="200" />
                <el-table-column label="场次" width="90">
                  <template #default="{ row }">
                    <span class="mono">#{{ row.live_session_id ?? '—' }}</span>
                  </template>
                </el-table-column>
              </el-table>
              <p v-else class="empty-hint">暂无 SC 记录</p>
            </template>
          </div>
        </el-tab-pane>

        <!-- ===== 场次：参与历史 ===== -->
        <el-tab-pane label="场次" name="sessions">
          <div v-loading="sessionsLoading" class="sessions-wrap">
            <el-table
              v-if="sessions.length > 0"
              :data="sessions"
              size="small"
              stripe
              :header-cell-style="{ background: 'var(--bg-hover)', color: 'var(--text-primary)' }"
            >
              <el-table-column label="场次" width="90">
                <template #default="{ row }">
                  <span class="mono">#{{ row.live_session_id }}</span>
                </template>
              </el-table-column>
              <el-table-column label="标题" min-width="160">
                <template #default="{ row }">
                  <span>{{ row.title || '（无标题）' }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="message_count" label="发言数" width="90" />
              <el-table-column label="参与时间" min-width="300">
                <template #default="{ row }">
                  <span class="mono"
                    >{{ formatTime(row.first_ms) }} – {{ formatTime(row.last_ms) }}</span
                  >
                </template>
              </el-table-column>
            </el-table>
            <p v-else-if="!sessionsLoading" class="empty-hint">该观众还没有参与过开启的场次</p>
          </div>
        </el-tab-pane>
      </el-tabs>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * 观众详情 —— 单观众档案：统计徽章 + 对话交织 + 贡献明细 + 参与场次
 *
 * 数据面：
 * - GET /viewers/{userId}：档案聚合（统计行 + 三表边界 + 贡献汇总 + 场次数）
 * - GET /viewers/{userId}/messages：对话批次——观众消息与主播回复按时间交织，
 *   "加载更早"以 next_before 游标向前翻，跨批按后端行 id 去重
 * - GET /viewers/{userId}/contributions、/sessions：明细表
 * 贡献/场次 tab 懒加载（首次切换才取数）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { viewersApi } from '@/api';
import type {
  ViewerContributions,
  ViewerDetail,
  ViewerDialogueItem,
  ViewerSessionItem,
} from '@/types';

const props = defineProps<{ userId: string }>();

const detail = ref<ViewerDetail | null>(null);
const loadError = ref('');
const activeTab = ref('dialogue');

const dialogueItems = ref<ViewerDialogueItem[]>([]);
const dialogueLoading = ref(false);
const dialogueExhausted = ref(false);
const nextBefore = ref<number | null>(null);
const dialogueScroll = ref<HTMLElement | null>(null);

const contrib = ref<ViewerContributions | null>(null);
const contribLoading = ref(false);
const sessions = ref<ViewerSessionItem[]>([]);
const sessionsLoading = ref(false);

/** 已加载批次中观众消息行数（主播回复行不计） */
const dialogueViewerCount = computed(
  () => dialogueItems.value.filter(item => item.kind === 'viewer').length,
);

const firstSeenText = computed(() => {
  const ms = detail.value?.first_seen_ms;
  if (!ms) return '—';
  return new Date(ms).toLocaleDateString('zh-CN');
});

const TYPE_LABELS: Record<string, string> = {
  guard: '上舰',
  enter: '进房',
};

function typeLabel(messageType: string): string {
  return TYPE_LABELS[messageType] ?? messageType;
}

function formatTime(ms: number): string {
  return new Date(ms).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function relativeTime(ms: number): string {
  if (!ms) return '—';
  const diffSec = Math.floor((Date.now() - ms) / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86_400) return `${Math.floor(diffSec / 3600)} 小时前`;
  if (diffSec < 30 * 86_400) return `${Math.floor(diffSec / 86_400)} 天前`;
  return new Date(ms).toLocaleDateString('zh-CN');
}

async function loadDetail(): Promise<void> {
  detail.value = null;
  loadError.value = '';
  try {
    const response = await viewersApi.detail(props.userId);
    detail.value = response.data;
    await loadDialogue();
  } catch {
    loadError.value = '观众不存在（或存储不可用）';
  }
}

async function loadDialogue(): Promise<void> {
  dialogueLoading.value = true;
  try {
    const response = await viewersApi.messages(props.userId, { limit: 30 });
    mergeDialogue(response.data.items, response.data.next_before);
  } finally {
    dialogueLoading.value = false;
  }
}

async function loadEarlier(): Promise<void> {
  if (nextBefore.value == null || dialogueLoading.value) return;
  dialogueLoading.value = true;
  try {
    const response = await viewersApi.messages(props.userId, {
      before_timestamp_ms: nextBefore.value,
      limit: 30,
    });
    mergeDialogue(response.data.items, response.data.next_before);
  } finally {
    dialogueLoading.value = false;
  }
}

/** 批次合并到展示头部（更早的在前），按后端行 id 去重后保持时间正序 */
function mergeDialogue(items: ViewerDialogueItem[], next: number | null): void {
  const known = new Set(dialogueItems.value.map(item => item.id));
  const fresh = items.filter(item => !known.has(item.id));
  dialogueItems.value = [...fresh, ...dialogueItems.value].sort(
    (a, b) => a.timestamp_ms - b.timestamp_ms,
  );
  nextBefore.value = next;
  dialogueExhausted.value = next == null;
}

async function loadContrib(): Promise<void> {
  if (contrib.value || contribLoading.value) return;
  contribLoading.value = true;
  try {
    const response = await viewersApi.contributions(props.userId);
    contrib.value = response.data;
  } finally {
    contribLoading.value = false;
  }
}

async function loadSessions(): Promise<void> {
  if (sessions.value.length > 0 || sessionsLoading.value) return;
  sessionsLoading.value = true;
  try {
    const response = await viewersApi.sessions(props.userId);
    sessions.value = response.data.items;
  } finally {
    sessionsLoading.value = false;
  }
}

// tab 懒加载：对话随档案加载；贡献/场次首次切入再取数
watch(activeTab, tab => {
  if (tab === 'contributions') void loadContrib();
  if (tab === 'sessions') void loadSessions();
});

watch(
  () => props.userId,
  () => {
    dialogueItems.value = [];
    dialogueExhausted.value = false;
    nextBefore.value = null;
    contrib.value = null;
    sessions.value = [];
    activeTab.value = 'dialogue';
    void loadDetail();
  },
);

onMounted(async () => {
  await loadDetail();
  await nextTick();
  // 初始定位到对话底部（最新消息）
  if (dialogueScroll.value) dialogueScroll.value.scrollTop = dialogueScroll.value.scrollHeight;
});
</script>

<style scoped>
.detail-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.profile-head {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.back-link {
  font-size: 13px;
  color: var(--color-primary);
  text-decoration: none;
}
.back-link:hover {
  text-decoration: underline;
}

.profile-name {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.loading-name {
  font-size: 15px;
  color: var(--text-placeholder);
  font-weight: 400;
}

.profile-id {
  font-size: 11px;
  color: var(--text-placeholder);
}

.head-badge {
  padding: 2px 10px;
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-card);
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--spacing-sm);
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.stat-value {
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
}

.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
}

.tabs-card {
  padding: var(--spacing-sm) var(--spacing-md) var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

/* ===== 对话区 ===== */

.dialogue-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.dialogue-toolbar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.dialogue-hint {
  font-size: 11px;
  color: var(--text-placeholder);
}

.dialogue-scroll {
  height: 480px;
  overflow-y: auto;
  padding: var(--spacing-sm);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-hover);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}
.dialogue-scroll::-webkit-scrollbar {
  width: 6px;
}
.dialogue-scroll::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.bubble-row {
  display: flex;
}
.bubble-row.is-viewer {
  justify-content: flex-start;
}
.bubble-row.is-reply {
  justify-content: flex-end;
}

.bubble {
  max-width: 72%;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
}
.is-reply .bubble {
  border-left: 3px solid var(--color-agent);
  background: var(--color-agent-bg);
}

.bubble-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.bubble-actor {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.is-reply .bubble-actor {
  color: var(--color-agent);
}

.type-tag {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 600;
  background: var(--bg-hover);
  color: var(--color-warning, #b88230);
  border: 1px solid var(--border-color-light);
}

.sim-tag {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  color: var(--text-placeholder);
  border: 1px dashed var(--border-color-dark);
}

.bubble-time {
  font-size: 10px;
  color: var(--text-placeholder);
}

.bubble-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* ===== 贡献 / 场次 ===== */

.contrib-wrap,
.sessions-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.scope-hint {
  margin: 0;
  font-size: 11px;
  color: var(--text-placeholder);
}

.contrib-title {
  margin: var(--spacing-sm) 0 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.sc-amount {
  font-weight: 600;
  color: var(--color-warning, #b88230);
}

.empty-hint {
  padding: var(--spacing-lg);
  text-align: center;
  font-size: 13px;
  color: var(--text-placeholder);
  margin: 0;
}

@media (max-width: 860px) {
  .profile-name {
    max-width: 100%;
  }
  .bubble {
    max-width: 88%;
  }
}
</style>
