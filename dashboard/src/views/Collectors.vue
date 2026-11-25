<template>
  <div class="collectors-page">
    <!-- LEFT：采集器列表 -->
    <ComponentListPanel
      title="采集器"
      list-label="采集器列表"
      :items="listItems"
      :selected-key="selectedName"
      :loading="loading"
      :error="componentsStore.error"
      empty-text="暂无采集器"
      :count-running="startedCount"
      :count-total="totalCount"
      @select="select"
      @retry="componentsStore.fetchComponents()"
    >
      <template #batch>
        <el-button
          size="small"
          type="success"
          plain
          class="batch-btn"
          :loading="batchLoading === 'start'"
          :disabled="totalCount === 0 || startedCount === totalCount"
          title="启动全部"
          @click="handleBatch('start')"
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
          @click="handleBatch('stop')"
        >
          停止
        </el-button>
      </template>
    </ComponentListPanel>

    <!-- RIGHT：详情 + 数据流 -->
    <main class="md-detail-panel" aria-label="采集器详情">
      <template v-if="selectedCollector">
        <DetailHeader
          :name="selectedCollector.name"
          :status-label="statusLabel(selectedCollector)"
          :status-type="statusType"
          type-chip="类型：采集器"
          :description="selectedCollector.description"
        >
          <template #links>
            <router-link
              :to="`/settings?key=collectors.${selectedCollector.name}`"
              class="config-link"
            >
              <el-icon><Setting /></el-icon>
              <span>查看配置</span>
            </router-link>
          </template>
          <template #actions>
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
          </template>
        </DetailHeader>

        <StatChipBar :chips="detailChips" />

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
              <el-button :type="paused ? 'primary' : 'default'" @click="togglePause">
                {{ paused ? '继续' : '暂停' }}
              </el-button>
              <el-button :disabled="streamEntries.length === 0" @click="clearStream">
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
                <span class="md-stream-item-time mono">{{ relativeTime(item.timestampMs) }}</span>
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
 * 主从通用逻辑（选中保持 / 控制 / 批量 / 事件流缓冲）见 useComponentMasterDetail；
 * 左栏面板 / 详情头 / 状态摘要条为三页共享组件（components/master-detail/）。
 */
import { computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { Setting } from '@element-plus/icons-vue';
import { useComponentsStore, useEventsStore } from '@/stores';
import { EVENT } from '@/types/events';
import { useNowTick } from '@/composables/useNowTick';
import { useScrollFollow } from '@/composables/useScrollFollow';
import { useKeepAlivePolling } from '@/composables/useKeepAlivePolling';
import { confirmAction } from '@/utils/confirmAction';
import {
  STREAM_CAP,
  useComponentMasterDetail,
  type ComponentEvent,
  type ComponentStreamItem,
} from '@/composables/useComponentMasterDetail';
import ComponentListPanel from '@/components/master-detail/ComponentListPanel.vue';
import type { ListPanelItem } from '@/components/master-detail/ComponentListPanel.vue';
import DetailHeader from '@/components/master-detail/DetailHeader.vue';
import StatChipBar from '@/components/master-detail/StatChipBar.vue';
import type { StatChip } from '@/components/master-detail/StatChipBar.vue';
import { summarizeEvent } from '@/utils/eventSummary';
import { relativeTime as relativeTimeLabel } from '@/utils/liveFeed';
import '@/styles/component-master-detail.css';

// 归因映射：采集器名 → 消息族白名单（payload.message_type 判别）
//
// 核对来源（采集器在 EventBus 上发 room.message.* 事件；Dashboard WS 层把
// 整族折叠广播为 "room.message"，消息种类由 payload.message_type 携带）：
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

/** Dashboard WS 把 room.message.* 族 EventBus 事件统一折叠广播为此类型 */
const ROOM_MESSAGE_WS_TYPE = EVENT.roomMessage;

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

// 左栏列表行（共享面板数据形态）
const listItems = computed<ListPanelItem[]>(() =>
  collectorsList.value.map(c => ({
    key: c.name,
    name: c.name,
    isStarted: c.is_started,
    isEnabled: c.is_enabled,
    tag: c.is_started ? '运行' : null,
  })),
);

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
    timestampMs: e.timestamp_ms,
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

// 详情头状态标签三态与状态摘要条
const statusType = computed<'success' | 'warning' | 'info'>(() => {
  const c = selectedCollector.value;
  if (!c) return 'info';
  return c.is_started ? 'success' : c.is_enabled ? 'warning' : 'info';
});

const detailChips = computed<StatChip[]>(() => {
  const c = selectedCollector.value;
  if (!c) return [];
  return [
    { label: '已启用', value: c.is_enabled ? '是' : '否', tone: c.is_enabled ? 'yes' : 'no' },
    { label: '运行中', value: c.is_started ? '是' : '否', tone: c.is_started ? 'yes' : 'no' },
    { label: '类型', value: c.type || '—', tone: 'mono' },
    { label: '归因族', value: attributionFamiliesLabel.value, tone: 'mono', accent: true },
  ];
});

// 视图层：从缓冲里取最后 STREAM_CAP 条；保持时间升序展示（新条目在末尾）。
const streamEntries = computed<CollectorStreamItem[]>(() => streamBuffer.value);

// 自动滚动：新条目追加时滚到底部，除非用户已向上滚动

const { scrollRef: streamScrollRef } = useScrollFollow(streamEntries);

// 批量启停影响所有采集器，先确认再执行（取消路径零请求），与 Agent 页同标准
async function handleBatch(action: 'start' | 'stop'): Promise<void> {
  const verb = action === 'start' ? '启动' : '停止';
  const ok = await confirmAction(
    `确认${verb}全部 ${totalCount.value} 个采集器？将同时改变所有采集器的运行状态`,
    `批量${verb}确认`,
    { confirmButtonText: `确认${verb}` },
  );
  if (!ok) return;
  await runBatch(action);
}

// 工具

// 响应式时钟驱动相对时间自动刷新（此前用 Date.now() 非响应式，空闲时标签冻结）
const nowMs = useNowTick();

function relativeTime(timestampMs: number): string {
  return relativeTimeLabel(nowMs.value, timestampMs);
}

// 生命周期

// 清单 8s 静默轮询随激活态启停（与 Agent 页同节奏）；首屏由显式拉取负责
const COMPONENTS_POLL_INTERVAL_MS = 8000;
useKeepAlivePolling(() => componentsStore.fetchComponents(true), COMPONENTS_POLL_INTERVAL_MS);

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
