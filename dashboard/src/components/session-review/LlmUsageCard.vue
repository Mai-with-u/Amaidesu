<template>
  <section class="rv-panel">
    <div class="rv-panel-title">LLM 用量（按 Profile）</div>
    <table class="rv-table">
      <thead>
        <tr>
          <th>Profile</th>
          <th class="num">笔数</th>
          <th class="num">慢调用</th>
          <th class="num">失败</th>
          <th class="num">输入</th>
          <th class="num">缓存</th>
          <th class="num">输出</th>
          <th class="num">成本</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in llm.by_profile" :key="row.profile_name">
          <td>
            <span class="tier" :style="{ borderColor: profileColor(row.profile_name) }">{{
              row.profile_name || 'unknown'
            }}</span>
          </td>
          <td class="num">{{ row.calls }}</td>
          <td class="num" :class="{ 'num--zero': row.slow_calls === 0 }">{{ row.slow_calls }}</td>
          <td class="num" :class="{ 'num--zero': row.failed_calls === 0 }">
            {{ row.failed_calls }}
          </td>
          <td class="num">{{ compactNumber(row.prompt_tokens) }}</td>
          <td class="num">{{ cacheRateLabel(row.cache_hit_rate) }}</td>
          <td class="num">{{ compactNumber(row.completion_tokens) }}</td>
          <td class="num">{{ formatCost(row.cost, 2) }}</td>
        </tr>
        <tr class="total-row">
          <td>合计</td>
          <td class="num">{{ llm.overall.calls }}</td>
          <td class="num" :class="{ 'num--zero': llm.overall.slow_calls === 0 }">
            {{ llm.overall.slow_calls }}
          </td>
          <td class="num" :class="{ 'num--zero': llm.overall.failed_calls === 0 }">
            {{ llm.overall.failed_calls }}
          </td>
          <td class="num">{{ compactNumber(llm.overall.prompt_tokens) }}</td>
          <td class="num">{{ cacheRateLabel(llm.overall.cache_hit_rate) }}</td>
          <td class="num">{{ compactNumber(llm.overall.completion_tokens) }}</td>
          <td class="num">{{ formatCost(llm.overall.cost, 2) }}</td>
        </tr>
      </tbody>
    </table>
    <p class="rv-note">成本按计价配置折算；缓存命中率 = cache_hit / (hit + miss)，未上报显示 —</p>
  </section>
</template>

<script setup lang="ts">
/**
 * LLM 用量表（按 Profile）：笔数 / 慢调用 / 失败 / 输入输出 tokens / 缓存命中率 / 成本。
 * profile 色圈与水位图折线共用同一取色入口（utils/sessionReview，随主题取域色）。
 */
import { computed } from 'vue';
import type { SessionReviewResponse } from '@/types';
import { compactNumber } from '@/utils/chartFormat';
import { formatCost } from '@/utils/format';
import { cacheRateLabel, waterlineColorForProfile } from '@/utils/sessionReview';
import { useChartPalette } from '@/composables/useECharts';

const props = defineProps<{ llm: SessionReviewResponse['llm'] }>();

const palette = useChartPalette();

const profileNames = computed<string[]>(() => props.llm.by_profile.map(row => row.profile_name));

function profileColor(profile: string): string {
  return waterlineColorForProfile(profile, profileNames.value, palette.value.readVar);
}
</script>

<style scoped>
.tier {
  display: inline-block;
  padding: 1px 8px;
  border: 1px solid;
  border-radius: 999px;
  font-size: 12px;
}
</style>
