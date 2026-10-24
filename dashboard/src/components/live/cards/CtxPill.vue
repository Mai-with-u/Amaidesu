<!-- 上下文水位胶囊：迷你进度条 + 百分比，悬浮展开请求分段明细（决策/响应/发言卡共用） -->
<script setup lang="ts">
import ContextBreakdownCard from '@/components/llm/ContextBreakdownCard.vue';
import type { ShowEntry } from '@/utils/liveFeed';
import { useTimelineCardHost } from './context';

defineProps<{ entry: ShowEntry }>();

const host = useTimelineCardHost();
</script>

<template>
  <el-popover placement="top-start" :width="400" trigger="hover" @show="host.onCtxPillShow(entry)">
    <template #reference>
      <span
        v-if="host.ctxRatioOf(entry) !== null"
        class="d-pill d-pill--ctx"
        :class="{ 'is-warn': (host.ctxRatioOf(entry) ?? 0) > 0.8 }"
        :title="entry.llmRequestId ? undefined : host.ctxTitleOf(entry)"
      >
        <span class="d-pill-ctx-bar">
          <span class="d-pill-ctx-bar-fill" :style="host.ctxBarStyleOf(entry)"></span>
        </span>
        上下文 {{ host.ctxLabelOf(entry) }}
      </span>
    </template>
    <ContextBreakdownCard :breakdown="host.ctxBreakdownOf(entry)" />
  </el-popover>
</template>
