<template>
  <section class="rv-panel" aria-label="流程单执行">
    <div class="rv-panel-title">流程单执行</div>
    <p class="rv-note table-note-top">
      实际停留为墙钟口径（含暂停）；超预计 25% 染黄、一倍染红（阈值为前端常量）
    </p>
    <table class="rv-table">
      <thead>
        <tr>
          <th>环节</th>
          <th>进入</th>
          <th class="num">实际停留</th>
          <th class="num">预计</th>
          <th class="num">暂停</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(seg, i) in rundown.segments" :key="i">
          <td>#{{ seg.seg_index }} {{ seg.seg_title || seg.seg_id || '（无标题）' }}</td>
          <td class="num">{{ formatTimeHMS(seg.entered_at_ms) }}</td>
          <td class="num" :class="dwellCellClass(seg)">
            {{ seg.left_at_ms === null ? '→ 收播' : formatDurationMs(seg.dwell_ms) }}
          </td>
          <td class="num">
            {{ seg.expected_ms === null ? '—' : formatDurationMs(seg.expected_ms) }}
          </td>
          <td class="num" :class="{ 'num--zero': seg.pause_ms === 0 }">
            {{ seg.pause_ms === 0 ? '—' : formatDurationMs(seg.pause_ms) }}
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="rundown.pause_total_ms > 0" class="rv-note">
      总暂停 {{ formatDurationMs(rundown.pause_total_ms) }}（已含在各段停留内，未从停留扣除）
    </p>
  </section>
</template>

<script setup lang="ts">
/**
 * 流程单执行面板：rundown_transitions 事实行重放为"实际停留 vs 预计"。
 * 单场语义（多选合并视图页面不传本卡数据）。
 */
import type { RundownSegmentRun, SessionRundownExecutionResponse } from '@/types';
import { dwellLevel, formatDurationMs } from '@/utils/sessionReview';
import { formatTimeHMS } from '@/utils/format';

defineProps<{ rundown: SessionRundownExecutionResponse }>();

/** 停留超时染色类（未封口/无预计不参与比较） */
function dwellCellClass(seg: RundownSegmentRun): string {
  const level = dwellLevel(seg.dwell_ms, seg.expected_ms, seg.left_at_ms);
  return level === 'red' ? 'dwell-red' : level === 'yellow' ? 'dwell-yellow' : '';
}
</script>

<style scoped>
/* 口径说明贴着面板标题（其余脚注在表格下方，走共享 rv-note 的上边距） */
.table-note-top {
  margin: 0 0 10px;
}
</style>
