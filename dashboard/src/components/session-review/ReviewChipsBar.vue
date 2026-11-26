<template>
  <section class="rv-chips" aria-label="场次信息">
    <span
      v-for="chip in chips"
      :key="chip.label"
      class="rv-chip rv-mono"
      :class="{ 'rv-chip--dim': chip.dimmed }"
      >{{ chip.label }}</span
    >
  </section>
</template>

<script setup lang="ts">
/**
 * 场次信息 chips 条：单场显示起止/时长/来源/人气/累计观看，多选合并显示窗口时长。
 * 互动类零值 chip 属弱信息（没有互动发生），降饱和弱化；时间/时长/来源等信息
 * chip 恒常显示。来源中文标签复用场次显示口径的单一来源映射（utils/sessionManager）。
 */
import { computed } from 'vue';
import type { SessionReviewResponse } from '@/types';
import { formatTimeHMS } from '@/utils/format';
import { formatDurationMs } from '@/utils/sessionReview';
import { sourceLabel } from '@/utils/sessionManager';

const props = defineProps<{ review: SessionReviewResponse }>();

/** 单枚信息 chip：dimmed=零值弱信息降饱和展示 */
interface ReviewChip {
  label: string;
  dimmed: boolean;
}

const chips = computed<ReviewChip[]>(() => {
  const r = props.review;
  const list: ReviewChip[] = r.sessions.map(s => ({
    label: `场次 #${s.live_session_id}${s.title ? ` · ${s.title}` : ''}`,
    dimmed: false,
  }));
  if (r.sessions.length === 1) {
    const s = r.sessions[0];
    const endLabel = s.ended_at_ms !== null ? formatTimeHMS(s.ended_at_ms) : '进行中';
    list.push({ label: `${formatTimeHMS(s.started_at_ms)} – ${endLabel}`, dimmed: false });
    list.push({ label: formatDurationMs(s.duration_ms), dimmed: false });
    list.push({ label: `来源 ${sourceLabel(s.source)}`, dimmed: false });
    list.push({ label: `人气 ${s.heat}`, dimmed: s.heat === 0 });
    // 累计观看人次（UV 口径；协议无"当前在线"推送，viewer_count 恒 0 不上屏）
    list.push({ label: `累计观看 ${s.audience_total}`, dimmed: s.audience_total === 0 });
  } else {
    list.push({ label: formatDurationMs(r.window_end_ms - r.window_start_ms), dimmed: false });
    list.push({ label: `${r.sessions.length} 场合并`, dimmed: false });
  }
  // 金额为付费折算人民币（银瓜子等免费币种不计入；无可折算金额不追加）
  const giftCny = r.chips.gift_total_cny;
  const giftAmount = giftCny != null && giftCny > 0 ? ` · ¥${giftCny.toFixed(2)}` : '';
  const scCny = r.chips.super_chat_total_cny;
  const scAmount = scCny != null && scCny > 0 ? ` · ¥${scCny.toFixed(2)}` : '';
  list.push({ label: `弹幕 ${r.chips.danmaku_count}`, dimmed: r.chips.danmaku_count === 0 });
  list.push({ label: `进入 ${r.chips.enter_count}`, dimmed: r.chips.enter_count === 0 });
  list.push({ label: `礼物 ${r.chips.gift_count}${giftAmount}`, dimmed: r.chips.gift_count === 0 });
  list.push({
    label: `SC ${r.chips.super_chat_count}${scAmount}`,
    dimmed: r.chips.super_chat_count === 0,
  });
  return list;
});
</script>
