<template>
  <div class="collectors-page">
    <!-- LEFT：采集器列表（narrow, 240px） -->
    <aside class="md-list-panel" aria-label="采集器列表">
      <header class="md-list-header">
        <div class="md-list-header-main">
          <h2 class="md-list-title">采集器</h2>
          <span class="md-list-count">
            <span class="md-list-count-running">{{ startedCount }}</span>
            <span class="md-list-count-divider">/</span>
            <span class="md-list-count-total">{{ totalCount }}</span>
          </span>
        </div>
        <div class="md-batch-actions">
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

      <div v-if="totalCount === 0 && !loading" class="md-list-empty">
        <el-empty :image-size="64" description="暂无采集器" />
      </div>
      <ul v-else class="md-list" role="listbox">
        <li
          v-for="c in collectorsList"
          :key="c.name"
          class="md-row"
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
          <span class="md-status-dot" aria-hidden="true" />
          <span class="md-row-name" :title="c.name">{{ c.name }}</span>
          <el-tag
            v-if="c.is_started"
            size="small"
            type="success"
            effect="plain"
            class="md-running-tag"
          >
            运行
          </el-tag>
        </li>
      </ul>
    </aside>

    <!-- RIGHT：详情 + 数据流（flex-1, the star） -->
    <main class="md-detail-panel" aria-label="采集器详情">
      <template v-if="selectedCollector">
        <!-- 详情头：名称 + 状态 + 操作 -->
        <header class="md-detail-header">
          <div class="md-detail-title-block">
            <div class="md-detail-title-row">
              <h1 class="md-detail-name">{{ selectedCollector.name }}</h1>
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
                class="md-status-tag"
              >
                {{ statusLabel(selectedCollector) }}
              </el-tag>
              <span class="md-type-chip">类型：采集器</span>
            </div>
            <p class="md-detail-description">
              {{ selectedCollector.description || '（暂无描述）' }}
            </p>
            <div class="detail-links">
              <router-link to="/settings" class="config-link">
                <el-icon><Setting /></el-icon>
                <span>查看配置</span>
              </router-link>
            </div>
          </div>
          <div class="md-detail-actions">
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

        <!-- 元信息条：compact stat chips -->
        <div class="md-details-strip" aria-label="状态摘要">
          <div class="md-stat-chip">
            <span class="md-chip-label">已启用</span>
            <span
              class="md-chip-value"
              :class="selectedCollector.is_enabled ? 'md-chip-yes' : 'md-chip-no'"
            >
              {{ selectedCollector.is_enabled ? '是' : '否' }}
            </span>
          </div>
          <div class="md-stat-chip">
            <span class="md-chip-label">运行中</span>
            <span
              class="md-chip-value"
              :class="selectedCollector.is_started ? 'md-chip-yes' : 'md-chip-no'"
            >
              {{ selectedCollector.is_started ? '是' : '否' }}
            </span>
          </div>
          <div class="md-stat-chip">
            <span class="md-chip-label">类型</span>
            <span class="md-chip-value mono">{{ selectedCollector.type || '—' }}</span>
          </div>
          <div class="md-stat-chip md-stat-chip--accent">
            <span class="md-chip-label">归因族</span>
            <span class="md-chip-value mono">{{ attributionFamiliesLabel }}</span>
          </div>
        </div>

        <!-- 数据流 -->
        <section class="md-stream-panel" aria-label="采集数据流">
          <header class="stream-header">
            <div class="md-stream-title-block">
              <span class="md-stream-pulse" aria-hidden="true" />
              <h3 class="md-stream-title">采集数据流</h3>
              <span class="stream-subtitle">· {{ attributionMode }}</span>
              <el-tag size="small" type="info" effect="plain" class="md-stream-count">
                {{ streamEntries.length }} / {{ STREAM_CAP }}
              </el-tag>
            </div>
            <div class="md-stream-controls">
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
            <code>source</code> 字段——此处按采集器已知事件族做近似匹配
          </p>

          <div ref="streamScrollRef" class="md-stream-scroll">
            <div v-if="streamEntries.length === 0" class="md-stream-empty">
              <span class="md-stream-empty-icon" aria-hidden="true">∅</span>
              <p>暂无数据——该采集器尚未产生事件（未启用或无流量）</p>
            </div>
            <ul v-else class="md-stream-list">
              <li v-for="item in streamEntries" :key="item.id" class="md-stream-item">
                <span class="stream-item-dot" aria-hidden="true" />
                <span class="md-stream-item-type mono">{{ item.eventType }}</span>
                <span class="md-stream-item-content">{{ item.summary }}</span>
                <span class="md-stream-item-time mono">{{ relativeTime(item.timestamp) }}</span>
              </li>
            </ul>
          </div>
        </section>
      </template>

      <div v-else class="md-detail-empty">
        <el-empty description="从左侧选择一个采集器查看详情与数据流" />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * Collectors 页面 —— Master-Detail 版
 *
 * 数据流归因：RoomMessagePayload（payloads/room.py）与 BasePayload
 * （payloads/base.py）均无 source 字段，运行轨迹按事件族近似归属；
 * COLLECTOR_EVENT_FAMILIES 依据 collectors/*.py 的实际 emit 代码核对。
 *
 * 主从通用逻辑（选中保持 / 控制 / 批量 / 事件流缓冲）见 useComponentMasterDetail。
 */
import { computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { Setting } from '@element-plus/icons-vue';
import { useComponentsStore, useEventsStore } from '@/stores';
import { useScrollFollow } from '@/composables/useScrollFollow';
import {
  STREAM_CAP,
  useComponentMasterDetail,
  type ComponentEvent,
  type ComponentStreamItem,
} from '@/composables/useComponentMasterDetail';
import { summarizeEvent } from '@/utils/eventSummary';
import { relativeTime as relativeTimeLabel, toSeconds } from '@/utils/liveFeed';
import '@/styles/component-master-detail.css';

// 归因映射：采集器名 → 消息族白名单（payload.message_type 判别）
//
// 核对来源（采集器在 EventBus 上发 room.message.* 事件；Dashboard WS 层把
// 4 种事件统一广播为 "room.message"，消息种类由 payload.message_type 携带）：
// - console_input_collector.py: `_emit_semantic_event` 按 data_type 发 4 种事件
// - bili_danmaku_official_collector.py: WS 弹幕/礼物/SC/进房全部走 _emit_semantic_event
// - bili_danmaku_collector.py (legacy): 仅 _emit_semantic_event 发 room.message.danmaku
// - mock_collector.py: 既有 _emit_danmaku 也有 _emit_semantic；开启时覆盖全部 4 种
// - screen_change_collector.py: 基类 _emit_normalized_message 兜底（data_type=text → danmaku）
// - stt_collector.py: 基类 _emit_normalized_message 兜底（data_type=text → danmaku）
//
// 规则：
// - 列表列出该采集器会产生的 message_type 值。
// - 未列出的采集器 → 空列表 = 匹配全部消息族（兜底，避免漏数据）。
const COLLECTOR_EVENT_FAMILIES: Record<string, readonly string[]> = {
  console_input: ['danmaku', 'gift', 'super_chat', 'enter'],
  bili_danmaku: ['danmaku'],
  bili_danmaku_official: ['danmaku', 'gift', 'super_chat', 'enter'],
  mock: ['danmaku', 'gift', 'super_chat', 'enter'],
  screen_change: ['danmaku'],
  stt: ['danmaku'],
};

/** Dashboard WS 把 4 种 room.message.* EventBus 事件统一广播为此类型 */
const ROOM_MESSAGE_WS_TYPE = 'room.message';

/** 空列表 = 不区分消息族，匹配全部 room.message 事件 */
const DEFAULT_FAMILIES: readonly string[] = [];

function getFamiliesFor(name: string | null): readonly string[] {
  if (!name) return DEFAULT_FAMILIES;
  return COLLECTOR_EVENT_FAMILIES[name] ?? DEFAULT_FAMILIES;
}

/** message_type → 人话标签（归因族 chip 展示用，避免拼技术事件名） */
const FAMILY_HUMAN_LABELS: Record<string, string> = {
  danmaku: '弹幕',
  gift: '礼物',
  super_chat: 'SC',
  enter: '进场',
};

// Store + 状态

const componentsStore = useComponentsStore();
const eventsStore = useEventsStore();
const { collectorsList, loading } = storeToRefs(componentsStore);
const { events } = storeToRefs(eventsStore);

const totalCount = computed(() => collectorsList.value.length);
const startedCount = computed(() => collectorsList.value.filter(c => c.is_started).length);

type CollectorStreamItem = ComponentStreamItem;

// 主从通用逻辑：选中保持 / 启停控制 / 批量 / 状态文案 / 事件流缓冲
const {
  selectedName,
  selected: selectedCollector,
  select,
  controlPending: actionLoading,
  batchLoading,
  handleControl,
  runBatch,
  statusLabel,
  paused,
  streamItems: streamBuffer,
  togglePause,
  clearStream,
} = useComponentMasterDetail({
  list: collectorsList,
  domain: 'collectors',
  noun: '采集器',
  events,
  mapEvent: mapStreamEvent,
});

/**
 * 判断一条 WS 事件是否归属给定消息族。
 * WS 层把 4 种 room.message.* EventBus 事件统一广播为 "room.message"，
 * 由 payload.message_type 判别；families 为空 = 全部消息。
 */
function matchesRoomMessageFamily(
  type: string,
  data: unknown,
  families: readonly string[],
): boolean {
  if (type !== ROOM_MESSAGE_WS_TYPE) return false;
  if (families.length === 0) return true;
  const messageType = (data as Record<string, unknown> | null)?.message_type;
  return typeof messageType === 'string' && families.includes(messageType);
}

function mapStreamEvent(
  e: ComponentEvent,
  ctx: { selectedName: string | null },
): CollectorStreamItem | null {
  const families = getFamiliesFor(ctx.selectedName);
  if (!matchesRoomMessageFamily(e.type, e.data, families)) return null;
  return {
    id: e.id,
    eventType: e.type,
    summary: summarizeEvent(e.type, e.data),
    timestamp: e.timestamp,
  };
}

const attributionMode = computed(() => {
  const sel = selectedName.value;
  if (!sel) return '按消息族兜底（room.message）';
  return COLLECTOR_EVENT_FAMILIES[sel] ? '按消息族近似归属' : '按消息族兜底（room.message）';
});

const attributionFamiliesLabel = computed(() => {
  const families = getFamiliesFor(selectedName.value);
  if (families.length === 0) return '全部消息族';
  return families.map(f => FAMILY_HUMAN_LABELS[f] ?? f).join(' / ');
});

// 视图层：从缓冲里取最后 STREAM_CAP 条；保持时间升序展示（新条目在末尾）。
const streamEntries = computed<CollectorStreamItem[]>(() => streamBuffer.value);

// 自动滚动：新条目追加时滚到底部，除非用户已向上滚动

const { scrollRef: streamScrollRef } = useScrollFollow(streamEntries);

// 工具

function relativeTime(timestampMs: number): string {
  // 后端事件 timestamp 秒/毫秒并存，归一后走共享短标签
  return relativeTimeLabel(toSeconds(timestampMs), Date.now() / 1000);
}

// 生命周期

onMounted(() => {
  componentsStore.fetchComponents();
});
</script>

<style scoped>
/* 页面级：强调色注入 + 网格布局；主从通用样式见 styles/component-master-detail.css */
.collectors-page {
  --md-accent: var(--color-collector);
  --md-accent-bg: var(--color-collector-bg);
  --md-pulse-ring: rgba(59, 130, 246, 0.5);

  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: var(--spacing-md);
  height: calc(100vh - var(--header-height) - 2 * var(--spacing-lg));
  min-height: 640px;
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

/* 数据流头部：单行布局（本页特有） */
.stream-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-md) var(--spacing-lg);
  border-bottom: 1px solid var(--border-color-light);
  gap: var(--spacing-md);
  flex-shrink: 0;
}

.md-stream-title-block {
  flex: unset;
}

.stream-subtitle {
  font-size: 11px;
  color: var(--text-placeholder);
  font-style: italic;
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

/* 流条目网格与圆点（本页特有） */
.md-stream-item {
  grid-template-columns: 12px 168px minmax(0, 1fr) auto;
}

.stream-item-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-collector);
  box-shadow: 0 0 6px var(--color-collector);
  flex-shrink: 0;
}

.md-stream-item-type {
  color: var(--color-collector);
}

/* 响应式 */
@media (max-width: 1023px) {
  .collectors-page {
    grid-template-columns: 200px minmax(0, 1fr);
  }

  .md-detail-name {
    font-size: 20px;
  }

  .md-stream-item {
    grid-template-columns: 12px 120px minmax(0, 1fr) auto;
  }
}

@media (max-width: 768px) {
  .collectors-page {
    grid-template-columns: 1fr;
    height: auto;
    min-height: 0;
  }

  .md-stream-item {
    grid-template-columns: 8px minmax(0, 1fr) auto;
  }
}
</style>
