<template>
  <div class="collectors-page">
    <!-- ============================================================== -->
    <!-- LEFT：采集器列表（narrow, 240px）                                  -->
    <!-- ============================================================== -->
    <aside class="list-panel" aria-label="采集器列表">
      <header class="list-header">
        <div class="list-header-main">
          <h2 class="list-title">采集器</h2>
          <span class="list-count">
            <span class="list-count-running">{{ startedCount }}</span>
            <span class="list-count-divider">/</span>
            <span class="list-count-total">{{ totalCount }}</span>
          </span>
        </div>
        <div class="batch-actions">
          <el-button
            size="small"
            type="success"
            plain
            class="batch-btn"
            :loading="batchLoading === 'start'"
            :disabled="totalCount === 0 || startedCount === totalCount"
            title="启动全部"
            @click="runBatch('start')"
          >
            启动
          </el-button>
          <el-button
            size="small"
            type="danger"
            plain
            class="batch-btn"
            :loading="batchLoading === 'stop'"
            :disabled="totalCount === 0 || startedCount === 0"
            title="停止全部"
            @click="runBatch('stop')"
          >
            停止
          </el-button>
        </div>
      </header>

      <div v-if="totalCount === 0 && !loading" class="list-empty">
        <el-empty :image-size="64" description="暂无采集器" />
      </div>
      <ul v-else class="collector-list" role="listbox">
        <li
          v-for="c in collectorsList"
          :key="c.name"
          class="collector-row"
          :class="{
            'is-selected': c.name === selectedName,
            'is-running': c.is_started,
            'is-stopped': !c.is_started && c.is_enabled,
            'is-disabled': !c.is_enabled,
          }"
          role="option"
          :aria-selected="c.name === selectedName"
          @click="select(c.name)"
        >
          <span class="status-dot" aria-hidden="true" />
          <span class="collector-name" :title="c.name">{{ c.name }}</span>
          <el-tag
            v-if="c.is_started"
            size="small"
            type="success"
            effect="plain"
            class="running-tag"
          >
            运行
          </el-tag>
        </li>
      </ul>
    </aside>

    <!-- ============================================================== -->
    <!-- RIGHT：详情 + 数据流（flex-1, the star）                          -->
    <!-- ============================================================== -->
    <main class="detail-panel" aria-label="采集器详情">
      <template v-if="selectedCollector">
        <!-- 1. 详情头：名称 + 状态 + 操作 -->
        <header class="detail-header">
          <div class="detail-title-block">
            <div class="detail-title-row">
              <h1 class="detail-name">{{ selectedCollector.name }}</h1>
              <el-tag
                size="default"
                :type="
                  selectedCollector.is_started
                    ? 'success'
                    : selectedCollector.is_enabled
                      ? 'warning'
                      : 'info'
                "
                effect="dark"
                class="status-tag"
              >
                {{ statusLabel(selectedCollector) }}
              </el-tag>
              <span class="type-chip">类型：采集器</span>
            </div>
            <p class="detail-description">
              {{ selectedCollector.description || '（暂无描述）' }}
            </p>
            <div class="detail-links">
              <router-link to="/settings" class="config-link">
                <el-icon><Setting /></el-icon>
                <span>查看配置</span>
              </router-link>
            </div>
          </div>
          <div class="detail-actions">
            <el-button
              type="primary"
              size="default"
              :disabled="selectedCollector.is_started"
              :loading="actionLoading[`${selectedCollector.name}-start`]"
              @click="handleControl('start')"
            >
              启动
            </el-button>
            <el-button
              size="default"
              :disabled="!selectedCollector.is_started"
              :loading="actionLoading[`${selectedCollector.name}-stop`]"
              @click="handleControl('stop')"
            >
              停止
            </el-button>
            <el-button
              type="warning"
              size="default"
              plain
              :loading="actionLoading[`${selectedCollector.name}-restart`]"
              @click="handleControl('restart')"
            >
              重启
            </el-button>
          </div>
        </header>

        <!-- 2. 元信息条：compact stat chips -->
        <div class="details-strip" aria-label="状态摘要">
          <div class="stat-chip">
            <span class="chip-label">已启用</span>
            <span class="chip-value" :class="selectedCollector.is_enabled ? 'chip-yes' : 'chip-no'">
              {{ selectedCollector.is_enabled ? '是' : '否' }}
            </span>
          </div>
          <div class="stat-chip">
            <span class="chip-label">运行中</span>
            <span class="chip-value" :class="selectedCollector.is_started ? 'chip-yes' : 'chip-no'">
              {{ selectedCollector.is_started ? '是' : '否' }}
            </span>
          </div>
          <div class="stat-chip">
            <span class="chip-label">类型</span>
            <span class="chip-value mono">{{ selectedCollector.type || '—' }}</span>
          </div>
          <div class="stat-chip stat-chip--accent">
            <span class="chip-label">归因族</span>
            <span class="chip-value mono">{{ attributionFamiliesLabel }}</span>
          </div>
        </div>

        <!-- 3. 数据流：THE MAIN SPACE -->
        <section class="stream-panel" aria-label="采集数据流">
          <header class="stream-header">
            <div class="stream-title-block">
              <span class="stream-pulse" aria-hidden="true" />
              <h3 class="stream-title">采集数据流</h3>
              <span class="stream-subtitle">· {{ attributionMode }}</span>
              <el-tag size="small" type="info" effect="plain" class="stream-count">
                {{ streamEntries.length }} / {{ STREAM_CAP }}
              </el-tag>
            </div>
            <div class="stream-controls">
              <el-button size="small" :type="paused ? 'primary' : 'default'" @click="togglePause">
                {{ paused ? '继续' : '暂停' }}
              </el-button>
              <el-button size="small" :disabled="streamEntries.length === 0" @click="clearStream">
                清空
              </el-button>
            </div>
          </header>

          <p class="stream-note">
            精确归因需事件负载增加
            <code>source</code> 字段（后端后续票）——此处按采集器已知事件族做近似匹配
          </p>

          <div ref="streamScrollRef" class="stream-scroll">
            <div v-if="streamEntries.length === 0" class="stream-empty">
              <span class="stream-empty-icon" aria-hidden="true">∅</span>
              <p>暂无数据——该采集器尚未产生事件（未启用或无流量）</p>
            </div>
            <ul v-else class="stream-list">
              <li v-for="item in streamEntries" :key="item.id" class="stream-item">
                <span class="stream-item-dot" aria-hidden="true" />
                <span class="stream-item-type mono">{{ item.eventType }}</span>
                <span class="stream-item-content">{{ item.summary }}</span>
                <span class="stream-item-time mono">{{ relativeTime(item.timestamp) }}</span>
              </li>
            </ul>
          </div>
        </section>
      </template>

      <div v-else class="detail-empty">
        <el-empty description="从左侧选择一个采集器查看详情与数据流" />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * Collectors 页面 —— Master-Detail 版
 *
 * 改动要点（vs 旧版）：
 * - 旧版：counts header + 批量按钮 + 卡片网格 + 侧栏 room.message.* 流。
 * - 新版：左 240px 采集器列表 + 右详情三段（头 / 元信息条 / 数据流）。
 *   旧版被用户拒绝的「侧栏 feed」升格为页面主角；批量按钮下放到左列表头。
 *
 * 数据流归因：
 * - 调查：RoomMessagePayload（payloads/room.py）+ BasePayload（payloads/base.py）
 *   均无 `source` / collector-identity 字段。
 * - 结论：Case B（按事件族近似归属）。下表 COLLECTOR_EVENT_FAMILIES 是基于
 *   collectors/*.py 的实际 emit 代码人工核对得出。
 *
 * 后端后续票：Payload 增加 source 字段即可消除近似归因。
 */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { storeToRefs } from 'pinia';
import { Setting } from '@element-plus/icons-vue';
import { useComponentsStore, useEventsStore } from '@/stores';
import type { ComponentControlAction, ComponentSummary } from '@/types';
import { summarizeEvent } from '@/utils/eventSummary';

// ============================================================
// 归因映射：采集器名 → 事件类型前缀白名单（Case B）
// ============================================================
//
// 核对来源：
// - console_input_collector.py: `_emit_semantic_event` 按 data_type 发 4 种事件
// - bili_danmaku_official_collector.py: WS 弹幕/礼物/SC/进房全部走 _emit_semantic_event
// - bili_danmaku_collector.py (legacy): 仅 _emit_semantic_event 发 room.message.danmaku
// - mock_collector.py: 既有 _emit_danmaku 也有 _emit_semantic；开启时覆盖全部 4 种
// - screen_change_collector.py: 基类 _emit_normalized_message 兜底（data_type=text → danmaku）
// - stt_collector.py: 基类 _emit_normalized_message 兜底（data_type=text → danmaku）
//
// 规则：
// - 命中事件 type 前缀 ∈ 列表 → 视为该采集器产生的。
// - 多个前缀用 'room.message.*' 之类通配符（前端 startsWith 匹配）。
// - 未列出的采集器 → 默认按 room.message.* 兜底（保持旧行为，避免漏数据）。
const COLLECTOR_EVENT_FAMILIES: Record<string, readonly string[]> = {
  console_input: [
    'room.message.danmaku',
    'room.message.gift',
    'room.message.super_chat',
    'room.message.enter',
  ],
  bili_danmaku: ['room.message.danmaku'],
  bili_danmaku_official: [
    'room.message.danmaku',
    'room.message.gift',
    'room.message.super_chat',
    'room.message.enter',
  ],
  mock: [
    'room.message.danmaku',
    'room.message.gift',
    'room.message.super_chat',
    'room.message.enter',
  ],
  screen_change: ['room.message.danmaku'],
  stt: ['room.message.danmaku'],
};

const DEFAULT_FAMILY_PREFIXES = ['room.message.'];

function getFamiliesFor(name: string | null): readonly string[] {
  if (!name) return DEFAULT_FAMILY_PREFIXES;
  return COLLECTOR_EVENT_FAMILIES[name] ?? DEFAULT_FAMILY_PREFIXES;
}

const attributionMode = computed(() => {
  const sel = selectedName.value;
  if (!sel) return '按事件族兜底（room.message.*）';
  return COLLECTOR_EVENT_FAMILIES[sel] ? '按事件族近似归属' : '按事件族兜底（room.message.*）';
});

/** 事件族 → 人话标签（归因族 chip 展示用，避免拼技术事件名） */
const FAMILY_HUMAN_LABELS: Record<string, string> = {
  'room.message.danmaku': '弹幕',
  'room.message.gift': '礼物',
  'room.message.super_chat': 'SC',
  'room.message.enter': '进场',
};

const attributionFamiliesLabel = computed(() =>
  getFamiliesFor(selectedName.value)
    .map(f => FAMILY_HUMAN_LABELS[f] ?? f.replace('room.message.', ''))
    .join(' / '),
);

// ============================================================
// Store + 状态
// ============================================================

const componentsStore = useComponentsStore();
const eventsStore = useEventsStore();
const { collectorsList, loading } = storeToRefs(componentsStore);
const { events } = storeToRefs(eventsStore);

const STREAM_CAP = 100;

const totalCount = computed(() => collectorsList.value.length);
const startedCount = computed(() => collectorsList.value.filter(c => c.is_started).length);

// ----- 选中状态（默认首个 RUNNING，否则首个 enabled，否则首个） -----
const selectedName = ref<string | null>(null);

function pickDefault(): string | null {
  const list = collectorsList.value;
  if (list.length === 0) return null;
  const running = list.find(c => c.is_started);
  if (running) return running.name;
  const enabled = list.find(c => c.is_enabled);
  if (enabled) return enabled.name;
  return list[0].name;
}

watch(
  collectorsList,
  list => {
    if (list.length === 0) {
      selectedName.value = null;
      return;
    }
    // 选中项仍存在 → 保持
    if (selectedName.value && list.some(c => c.name === selectedName.value)) return;
    // 否则重选默认
    selectedName.value = pickDefault();
  },
  { immediate: true },
);

const selectedCollector = computed<ComponentSummary | null>(
  () => collectorsList.value.find(c => c.name === selectedName.value) ?? null,
);

function select(name: string): void {
  selectedName.value = name;
}

// ----- 启停状态：按 (name, action) 维度跟踪 loading -----
const actionLoading = reactive<Record<string, boolean>>({});
const batchLoading = ref<'start' | 'stop' | null>(null);

async function handleControl(action: ComponentControlAction): Promise<void> {
  const name = selectedName.value;
  if (!name) return;
  const key = `${name}-${action}`;
  actionLoading[key] = true;
  try {
    const result = await componentsStore.controlComponent('collectors', name, action);
    ElMessage.success(result.message);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '操作失败');
  } finally {
    actionLoading[key] = false;
  }
}

async function runBatch(action: 'start' | 'stop'): Promise<void> {
  batchLoading.value = action;
  try {
    const { succeeded, failed, messages } = await componentsStore.batchControl(
      'collectors',
      action,
    );
    if (succeeded === 0 && failed === 0) {
      ElMessage.info(action === 'start' ? '所有采集器已在运行中' : '所有采集器已停止');
    } else if (failed === 0) {
      ElMessage.success(`已${action === 'start' ? '启动' : '停止'} ${succeeded} 个采集器`);
    } else {
      const failureHints = messages
        .filter(m => m.includes('失败') || m.includes('未启动') || m.includes('已停止'))
        .slice(0, 2)
        .join('；');
      ElMessage.warning(
        `${action === 'start' ? '启动' : '停止'}完成：${succeeded} 成功, ${failed} 失败${failureHints ? `（${failureHints}）` : ''}`,
      );
    }
  } catch (error) {
    ElMessage.error('批量操作失败');
    console.error('Batch control error:', error);
  } finally {
    batchLoading.value = null;
  }
}

function statusLabel(c: ComponentSummary): string {
  if (c.is_started) return '运行中';
  if (c.is_enabled) return '已停止';
  return '未启用';
}

// ============================================================
// 数据流：基于归因映射过滤 + 本地缓冲 + 暂停/清空 + 自动滚动
// ============================================================

interface StreamItem {
  id: string;
  eventType: string;
  summary: string;
  timestamp: number;
}

// 暂停时不再向 UI 追加（counter 也不前进——按需求"暂停=停渲染"）
// 但 store 仍持续接收（不消费 = 不丢消息）。
const paused = ref(false);

// "last shown" 缓冲：累计 view-ready 流条目；最多 STREAM_CAP；超出从头丢。
const streamBuffer = ref<StreamItem[]>([]);

// 从 events store → 按归因映射 → 本地缓冲
watch(
  [events, selectedName, paused],
  ([evts, sel, isPaused]) => {
    if (isPaused || !sel) return;
    const prefixes = COLLECTOR_EVENT_FAMILIES[sel] ?? DEFAULT_FAMILY_PREFIXES;
    // 取 store 末尾一段（最多 STREAM_CAP * 2），按时间升序，过滤归属，写入缓冲。
    const slice = evts.slice(-STREAM_CAP * 2);
    const fresh: StreamItem[] = [];
    for (const e of slice) {
      if (!prefixes.some(p => e.type.startsWith(p))) continue;
      fresh.push({
        id: e.id,
        eventType: e.type,
        summary: summarizeEvent(e.type, e.data),
        timestamp: e.timestamp,
      });
    }
    streamBuffer.value = fresh.slice(-STREAM_CAP);
  },
  { immediate: true },
);

// 视图层：从缓冲里取最后 STREAM_CAP 条；保持时间升序展示（新条目在末尾）。
const streamEntries = computed<StreamItem[]>(() => streamBuffer.value);

function togglePause(): void {
  paused.value = !paused.value;
}

function clearStream(): void {
  streamBuffer.value = [];
}

// ============================================================
// 自动滚动：新条目追加时滚到底部，除非用户已向上滚动
// ============================================================

const streamScrollRef = ref<HTMLElement | null>(null);
// 距底 < 32px 视为"在底部"
const SCROLL_BOTTOM_THRESHOLD_PX = 32;

function isAtBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_THRESHOLD_PX;
}

watch(streamEntries, async () => {
  await nextTick();
  const el = streamScrollRef.value;
  if (!el) return;
  // 用户滚到底 → 跟到底；用户向上滚动则不强制。
  if (isAtBottom(el)) {
    el.scrollTop = el.scrollHeight;
  }
});

// ============================================================
// 工具
// ============================================================

function relativeTime(timestampMs: number): string {
  // 后端事件 timestamp 是 Unix 秒（参见 utils/eventSummary.ts 注释）
  const nowSec = Date.now() / 1000;
  const tsSec = timestampMs > 1e12 ? timestampMs / 1000 : timestampMs;
  const diffSec = Math.max(0, Math.floor(nowSec - tsSec));
  if (diffSec < 5) return '刚刚';
  if (diffSec < 60) return `${diffSec}s 前`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m 前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h 前`;
  return `${Math.floor(diffSec / 86400)}d 前`;
}

// ============================================================
// 生命周期
// ============================================================

onMounted(() => {
  componentsStore.fetchComponents();
});
</script>

<style scoped>
/* ============================================================ */
/* 页面布局：左 240 + 右 flex-1                                  */
/* ============================================================ */
.collectors-page {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: var(--spacing-md);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
  min-height: 640px;
}

/* ============================================================ */
/* LEFT：列表                                                    */
/* ============================================================ */
.list-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.list-header {
  padding: var(--spacing-md);
  border-bottom: 1px solid var(--border-color-light);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  background: var(--bg-card);
  flex-shrink: 0;
}

.list-header-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--spacing-sm);
}

.list-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}

.list-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-secondary);
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
}

.list-count-running {
  color: var(--color-collector);
  font-weight: 600;
  font-size: 13px;
}

.list-count-divider {
  color: var(--text-placeholder);
  margin: 0 1px;
}

.list-count-total {
  color: var(--text-regular);
}

.batch-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.batch-actions :deep(.el-button) {
  margin-left: 0;
  width: 100%;
}

.list-empty {
  padding: var(--spacing-lg) var(--spacing-sm);
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.collector-list {
  list-style: none;
  margin: 0;
  padding: var(--spacing-xs);
  overflow-y: auto;
  flex: 1;
}

.collector-list::-webkit-scrollbar {
  width: 6px;
}
.collector-list::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 3px;
}

.collector-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition:
    background var(--transition-fast),
    transform var(--transition-fast);
  margin-bottom: 2px;
  user-select: none;
}

.collector-row:hover {
  background: var(--bg-hover);
}

.collector-row.is-selected {
  background: var(--color-collector-bg);
  box-shadow: inset 3px 0 0 0 var(--color-collector);
}

.collector-row.is-selected .collector-name {
  color: var(--text-primary);
  font-weight: 600;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  border: 1.5px solid var(--text-placeholder);
  background: transparent;
  transition: background var(--transition-normal);
}

.collector-row.is-running .status-dot {
  background: var(--color-success);
  border-color: var(--color-success);
  box-shadow: 0 0 0 0 var(--color-success);
  animation: pulse-running 2s ease-in-out infinite;
}

.collector-row.is-stopped .status-dot {
  background: var(--color-info);
  border-color: var(--color-info);
}

.collector-row.is-disabled .status-dot {
  border-style: dashed;
  border-color: var(--text-placeholder);
  background: transparent;
}

@keyframes pulse-running {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(103, 194, 58, 0.4);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(103, 194, 58, 0);
  }
}

.collector-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
}

.running-tag {
  flex-shrink: 0;
  font-size: 10px;
  height: 18px;
  padding: 0 6px;
  line-height: 16px;
}

/* ============================================================ */
/* RIGHT：详情面板                                              */
/* ============================================================ */
.detail-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  min-width: 0;
  overflow: hidden;
}

/* ----- 1. 详情头 ----- */
.detail-header {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  padding: var(--spacing-md) var(--spacing-lg);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--spacing-md);
  flex-shrink: 0;
}

.detail-title-block {
  flex: 1;
  min-width: 0;
}

.detail-title-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.detail-name {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
  font-family: var(--font-mono);
  letter-spacing: -0.01em;
}

.status-tag {
  font-weight: 500;
}

.type-chip {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 8px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
  background: var(--bg-page);
}

.detail-description {
  font-size: 13px;
  color: var(--text-regular);
  margin: var(--spacing-sm) 0 0;
  line-height: 1.6;
  max-width: 720px;
}

.detail-links {
  margin-top: var(--spacing-sm);
}

.config-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--color-collector);
  text-decoration: none;
  padding: 2px 0;
}

.config-link:hover {
  text-decoration: underline;
}

.detail-actions {
  display: flex;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

/* ----- 2. 元信息条 ----- */
.details-strip {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
  flex-shrink: 0;
}

.stat-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 6px 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  font-size: 12px;
}

.stat-chip--accent {
  background: var(--color-collector-bg);
  border-color: transparent;
}

.chip-label {
  color: var(--text-secondary);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.chip-value {
  color: var(--text-primary);
  font-weight: 600;
}

.chip-yes {
  color: var(--color-success);
}

.chip-no {
  color: var(--text-placeholder);
}

/* ----- 3. 数据流：主角 ----- */
.stream-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.stream-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-md) var(--spacing-lg);
  border-bottom: 1px solid var(--border-color-light);
  gap: var(--spacing-md);
  flex-shrink: 0;
}

.stream-title-block {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-width: 0;
}

.stream-pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--color-collector);
  box-shadow: 0 0 0 0 var(--color-collector);
  animation: pulse-stream 2.5s ease-in-out infinite;
  flex-shrink: 0;
}

@keyframes pulse-stream {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.5);
  }
  50% {
    box-shadow: 0 0 0 6px rgba(59, 130, 246, 0);
  }
}

.stream-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.stream-subtitle {
  font-size: 11px;
  color: var(--text-placeholder);
  font-style: italic;
}

.stream-count {
  flex-shrink: 0;
  font-family: var(--font-mono);
}

.stream-controls {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.stream-note {
  font-size: 11px;
  color: var(--text-placeholder);
  margin: 0;
  padding: var(--spacing-xs) var(--spacing-lg);
  background: var(--bg-page);
  border-bottom: 1px solid var(--border-color-light);
  line-height: 1.5;
}

.stream-note code {
  font-family: var(--font-mono);
  background: var(--bg-hover);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
  color: var(--text-regular);
}

.stream-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--spacing-xs) 0;
}

.stream-scroll::-webkit-scrollbar {
  width: 8px;
}

.stream-scroll::-webkit-scrollbar-thumb {
  background: var(--border-color-dark);
  border-radius: 4px;
}

.stream-empty {
  height: 100%;
  min-height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-md);
  color: var(--text-placeholder);
  font-size: 13px;
  text-align: center;
  padding: var(--spacing-lg);
}

.stream-empty p {
  margin: 0;
  max-width: 320px;
  line-height: 1.6;
}

.stream-empty-icon {
  font-size: 36px;
  color: var(--border-color-dark);
  font-family: var(--font-mono);
}

.stream-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.stream-item {
  display: grid;
  grid-template-columns: 12px 168px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--spacing-sm);
  padding: 7px var(--spacing-lg);
  font-size: 12.5px;
  border-bottom: 1px solid var(--border-color-light);
  transition: background var(--transition-fast);
  animation: fade-in 0.2s ease-out;
}

.stream-item:hover {
  background: var(--bg-hover);
}

@keyframes fade-in {
  from {
    opacity: 0;
    transform: translateY(-2px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stream-item-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-collector);
  box-shadow: 0 0 6px var(--color-collector);
  flex-shrink: 0;
}

.stream-item-type {
  color: var(--color-collector);
  font-size: 11px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stream-item-content {
  color: var(--text-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.stream-item-time {
  color: var(--text-placeholder);
  font-size: 11px;
  white-space: nowrap;
}

/* ============================================================ */
/* Empty 详情                                                    */
/* ============================================================ */
.detail-empty {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ============================================================ */
/* Responsive                                                   */
/* ============================================================ */
@media (max-width: 1023px) {
  .collectors-page {
    grid-template-columns: 200px minmax(0, 1fr);
  }

  .detail-name {
    font-size: 20px;
  }

  .stream-item {
    grid-template-columns: 12px 120px minmax(0, 1fr) auto;
  }
}

@media (max-width: 768px) {
  .collectors-page {
    grid-template-columns: 1fr;
    height: auto;
    min-height: 0;
  }

  .list-panel {
    max-height: 280px;
  }

  .detail-header {
    flex-direction: column;
    align-items: stretch;
  }

  .detail-actions {
    flex-wrap: wrap;
  }

  .stream-item {
    grid-template-columns: 8px minmax(0, 1fr) auto;
    row-gap: 2px;
  }

  .stream-item-type {
    grid-column: 2;
    grid-row: 1;
  }

  .stream-item-content {
    grid-column: 2;
    grid-row: 2;
  }

  .stream-item-time {
    grid-column: 3;
    grid-row: 1 / span 2;
    align-self: center;
  }
}
</style>
