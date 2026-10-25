<!-- 控制台实时计分板：六胶囊状态条（时长/发言数/弹幕数/礼物折算¥/tokens/缓存命中率）。
     数据源与口径见 useLiveScoreboard；简洁档由父级整条隐藏，组件内部不做档位判断 -->
<script setup lang="ts">
import { computed, type PropType } from 'vue';
import { useLiveScoreboard } from '@/composables/live/useLiveScoreboard';
import { formatElapsedMs, formatNumber } from '@/utils/format';
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
  llmTotals.value.totalTokens !== null ? formatNumber(llmTotals.value.totalTokens) : '—',
);

const cacheRateLabel = computed(() => {
  const rate = llmTotals.value.cacheHitRate;
  return rate !== null ? `${Math.round(rate * 100)}%` : '—';
});
</script>

<template>
  <div class="scoreboard" aria-label="实时计分板">
    <span class="pill mono" title="当前场次已播时长">
      <span class="pill-label">时长</span>{{ durationLabel }}
    </span>
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
</template>

<style scoped>
.scoreboard {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.pill {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--color-border, #dcdfe6);
  font-size: 12px;
  color: var(--color-text, #303133);
  background: var(--color-bg, #fff);
}

.pill-label {
  font-size: 11px;
  color: var(--color-text-secondary, #909399);
}
</style>
