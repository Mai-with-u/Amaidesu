<!-- 控制台实时计分板：六胶囊状态条（时长/发言数/弹幕数/礼物折算¥/tokens/缓存命中率）。
     数据源与口径见 useLiveScoreboard；简洁档由父级整条隐藏，组件内部不做档位判断。
     胶囊按统计窗口分三组（本场 / 近期窗口 / 进程累计），组间细分隔线标注口径 -->
<script setup lang="ts">
import { computed, type PropType } from 'vue';
import { useLiveScoreboard } from '@/composables/live/useLiveScoreboard';
import { formatCountCompact, formatElapsedMs } from '@/utils/format';
import type { LiveSessionItem } from '@/types';

const props = defineProps({
  /** 当前进行中的场次（null = 测试模式未开场次，时长胶囊显示占位） */
  activeSession: { type: Object as PropType<LiveSessionItem | null>, default: null },
});

const { counts, durationMs, llmTotals } = useLiveScoreboard(computed(() => props.activeSession));

const durationLabel = computed(() =>
  durationMs.value !== null ? formatElapsedMs(durationMs.value) : '—',
);

const giftLabel = computed(() =>
  counts.value.giftCny !== null ? `¥${counts.value.giftCny.toFixed(2)}` : '—',
);

const tokensLabel = computed(() =>
  llmTotals.value.totalTokens !== null ? formatCountCompact(llmTotals.value.totalTokens) : '—',
);

const cacheRateLabel = computed(() => {
  const rate = llmTotals.value.cacheHitRate;
  return rate !== null ? `${Math.round(rate * 100)}%` : '—';
});
</script>

<template>
  <div class="scoreboard" aria-label="实时计分板">
    <div class="scoreboard-group">
      <span class="group-cap">本场</span>
      <span class="pill mono" title="当前场次已播时长">
        <span class="pill-label">时长</span>{{ durationLabel }}
      </span>
    </div>
    <div class="group-divider" aria-hidden="true" />
    <div class="scoreboard-group">
      <span class="group-cap">近期窗口</span>
      <span class="pill mono" title="本场主播发言条数（近期事件窗口口径）">
        <span class="pill-label">发言</span>{{ counts.speechCount }}
      </span>
      <span class="pill mono" title="本场弹幕条数（近期事件窗口口径）">
        <span class="pill-label">弹幕</span>{{ counts.danmakuCount }}
      </span>
      <span
        class="pill mono"
        title="礼物折算人民币（金瓜子 1000=1 元 + SC 原生金额；银瓜子等免费币种不计入）"
      >
        <span class="pill-label">礼物</span>{{ giftLabel }}
      </span>
    </div>
    <div class="group-divider" aria-hidden="true" />
    <div class="scoreboard-group">
      <span class="group-cap">进程累计</span>
      <span class="pill mono" title="LLM token 用量汇总（进程累计口径）">
        <span class="pill-label">tokens</span>{{ tokensLabel }}
      </span>
      <span
        class="pill mono"
        title="缓存命中率 = cache_hit / prompt_tokens（进程累计口径；— 表示上游未上报）"
      >
        <span class="pill-label">缓存</span>{{ cacheRateLabel }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.scoreboard {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: stretch;
}

.scoreboard-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

/* 组名：口径标注（对齐首页 stats-strip 的窗口小标做法） */
.group-cap {
  font-size: 10px;
  color: var(--text-placeholder);
}

.group-divider {
  width: 1px;
  align-self: stretch;
  background: var(--border-color-light);
}

.pill {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--border-color);
  font-size: 12px;
  color: var(--text-primary);
  background: var(--bg-card);
}

.pill-label {
  font-size: 11px;
  color: var(--text-secondary);
}
</style>
