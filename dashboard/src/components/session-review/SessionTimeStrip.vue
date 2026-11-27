<template>
  <section v-if="hasContent" class="rv-panel" aria-label="场次时间条">
    <div class="rv-panel-title">场次时间条</div>
    <div class="strip">
      <router-link
        v-for="(seg, i) in segs"
        :key="`seg-${i}`"
        class="strip-seg"
        :style="{ left: `${seg.left}%`, width: `${seg.width}%` }"
        :to="jumpLink(seg.atMs)"
        :title="seg.title"
        :aria-label="`环节 ${seg.title}，点击回看该环节起点`"
      />
      <router-link
        v-for="(tick, i) in ticks"
        :key="`tick-${i}`"
        class="strip-tick"
        :style="{ left: `${tick.left}%` }"
        :to="jumpLink(tick.atMs)"
        :title="tick.label"
        :aria-label="tick.label"
      />
      <router-link
        v-for="anchor in anchors"
        :key="anchor.kind"
        class="strip-anchor"
        :class="`is-${anchor.kind}`"
        :style="{ left: `${anchor.left}%` }"
        :to="jumpLink(anchor.atMs)"
        :title="anchor.label"
        :aria-label="`${anchor.label}，点击回看该时刻`"
      />
    </div>
    <div class="strip-axis">
      <span class="rv-mono">{{ formatTimeHMS(startMs) }}</span>
      <span class="rv-mono">{{ formatTimeHMS(endMs) }}</span>
    </div>
    <p class="rv-note">
      色块=流程单环节（点击回看环节起点）· 竖线=游戏事件 · 圆点：红=最长沉默起点、黄=最慢决策开口
    </p>
  </section>
</template>

<script setup lang="ts">
/**
 * 场次时间条：单场视图的叙事主轴——流程单环节段按实际时间比例铺底，场内游戏
 * 事件竖线与"最长沉默起点 / 最慢决策开口"两锚点叠加，点击任一元素时刻锚定跳转
 * 控制台回看（/live?session=&at=）。无流程单数据时退化为纯锚点条；全部内容为空
 * 或零宽窗口不渲染。单场语义（合并视图页面不传本卡数据）。
 */
import { computed } from 'vue';
import type { ReviewGameEventItem, RundownSegmentRun } from '@/types';
import { formatTimeHMS } from '@/utils/format';
import { consoleReplayLink } from '@/utils/sessionReview';

const props = defineProps<{
  /** 回看跳转的目标场次 */
  sessionId: number;
  startMs: number;
  endMs: number;
  /** 流程单环节段（未装配/无事实行为空数组） */
  segments: RundownSegmentRun[];
  /** 场内游戏事件明细（时间正序） */
  gameItems: ReviewGameEventItem[];
  /** 最长沉默起点（供回看定位；无锚点为 null） */
  silenceAtMs: number | null;
  /** 最慢决策的开口时刻 */
  latencyMaxAtMs: number | null;
}>();

const spanMs = computed(() => props.endMs - props.startMs);

function pct(atMs: number): number {
  if (spanMs.value <= 0) return 0;
  return Math.min(100, Math.max(0, ((atMs - props.startMs) / spanMs.value) * 100));
}

/** 环节段：未封口的环节延伸到条右端；过窄的段给最小可见宽度 */
const segs = computed(() =>
  props.segments.map(seg => {
    const endAt = seg.left_at_ms ?? props.endMs;
    const left = pct(seg.entered_at_ms);
    return {
      title: `#${seg.seg_index} ${seg.seg_title || seg.seg_id || '（无标题）'}`,
      left,
      width: Math.max(0.5, pct(endAt) - left),
      atMs: seg.entered_at_ms,
    };
  }),
);

const ticks = computed(() =>
  props.gameItems.map(item => ({
    left: pct(item.timestamp_ms),
    atMs: item.timestamp_ms,
    label: `${formatTimeHMS(item.timestamp_ms)} [${item.game}/${item.event_type}] ${item.message}`,
  })),
);

const anchors = computed(() => {
  const list: Array<{ kind: 'silence' | 'latency'; left: number; atMs: number; label: string }> =
    [];
  if (props.silenceAtMs !== null) {
    list.push({
      kind: 'silence',
      left: pct(props.silenceAtMs),
      atMs: props.silenceAtMs,
      label: '最长沉默起点',
    });
  }
  if (props.latencyMaxAtMs !== null) {
    list.push({
      kind: 'latency',
      left: pct(props.latencyMaxAtMs),
      atMs: props.latencyMaxAtMs,
      label: '最慢决策开口',
    });
  }
  return list;
});

const hasContent = computed(
  () =>
    spanMs.value > 0 &&
    (segs.value.length > 0 || ticks.value.length > 0 || anchors.value.length > 0),
);

function jumpLink(atMs: number): string {
  return consoleReplayLink(props.sessionId, atMs);
}
</script>

<style scoped>
.strip {
  position: relative;
  height: 26px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  overflow: hidden;
}

/* 环节段：流程单域色半透明铺底，段间以卡片底色细分隔 */
.strip-seg {
  position: absolute;
  top: 0;
  bottom: 0;
  min-width: 2px;
  background: var(--color-rundown);
  opacity: 0.5;
  border-right: 1px solid var(--bg-card);
}

.strip-seg:hover,
.strip-seg:focus-visible {
  opacity: 0.75;
}

.strip-tick {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--color-game);
  opacity: 0.85;
}

.strip-anchor {
  position: absolute;
  top: 50%;
  width: 11px;
  height: 11px;
  margin: -5.5px 0 0 -5.5px;
  border-radius: 50%;
  border: 2px solid var(--bg-card);
}

.strip-anchor.is-silence {
  background: var(--color-danger);
}

.strip-anchor.is-latency {
  background: var(--color-warning);
}

.strip-axis {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
