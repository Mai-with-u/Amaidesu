<template>
  <div :class="flat ? undefined : 'rv-panel'" aria-label="本场记忆线索">
    <div v-if="!flat" class="rv-panel-title">
      <span>本场记忆线索</span>
      <span class="rv-hint">代码按线索推给规划器的旧事；"用上"是她点名说了这件事</span>
    </div>
    <p v-if="flat" class="rv-hint flat-hint">
      代码按线索推给规划器的旧事；"用上"是她点名说了这件事
    </p>
    <el-radio-group
      v-if="sourceOptions.length > 1"
      v-model="source"
      size="small"
      class="cue-filter"
      aria-label="按来源筛选"
    >
      <el-radio-button value="">全部</el-radio-button>
      <el-radio-button v-for="item in sourceOptions" :key="item" :value="item">
        {{ cueSourceLabel(item) }}
      </el-radio-button>
    </el-radio-group>
    <p v-if="error" class="rv-line-error error-first">{{ error }}</p>
    <div v-else v-loading="loading" class="rv-lines">
      <ul v-if="visibleCues.length > 0" class="rv-line-list">
        <li v-for="cue in visibleCues" :key="cue.id" class="rv-line-item cue-item">
          <span class="rv-mono rv-line-time">{{ formatTimeHMS(cue.pushed_at_ms) }}</span>
          <el-tag size="small" effect="plain">{{ cueKindLabel(cue.card_kind) }}</el-tag>
          <router-link
            v-if="cue.archive_line_id !== null || cue.commitment_id !== null"
            class="rv-link rv-mono cue-label"
            :to="{ name: 'memory' }"
            >{{ cue.label }}</router-link
          >
          <span v-else class="rv-mono cue-label">{{ cue.label }}</span>
          <span class="rv-line-content" :class="{ 'rv-line-content--dropped': cueItemGone(cue) }">
            {{ cueItemGone(cue) ? '（条目已删掉）' : cue.title }}
            <span class="cue-why">
              {{ cue.sources.map(cueSourceLabel).join(' + ') }}
              <template v-if="cueScoreText(cue)"> · {{ cueScoreText(cue) }}</template>
            </span>
          </span>
          <el-tag v-if="cue.self_triggered" size="small" type="warning" effect="plain"
            >自触发</el-tag
          >
          <el-tag v-if="cue.used_at_ms !== null" size="small" type="success" effect="plain">
            {{ formatTimeHMS(cue.used_at_ms) }} 用上
          </el-tag>
          <el-tag v-else size="small" type="info" effect="plain">没用上</el-tag>
        </li>
      </ul>
      <p v-else class="rv-line-empty">
        {{ items.length > 0 ? '这个来源没有推过卡' : '这一场没有推过记忆线索' }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 本场记忆线索面板：代码每批推给规划器的旧事（承诺线索与被动想起；"用上" =
 * 她点名说了这件事）。来源筛选只在本卡本地做，条目更新（换场或重拉）时复位为
 * "全部"。单场语义（多选合并视图页面不传本卡数据）。
 */
import { computed, ref, watch } from 'vue';
import type { ReviewCueItem } from '@/types';
import { formatTimeHMS } from '@/utils/format';
import {
  cueItemGone,
  cueKindLabel,
  cueScoreText,
  cueSourceLabel,
  cueSourceOptions,
  filterCuesBySource,
} from '@/utils/memoryCues';

const props = defineProps<{
  items: ReviewCueItem[];
  loading: boolean;
  /** 加载错误：非空时渲染错误、不渲染列表 */
  error: string;
  /** tab 化呈现：去面板外框与标题（tab 标签已命名），只渲染内容与口径说明 */
  flat?: boolean;
}>();

/** 来源筛选：空串 = 全部 */
const source = ref('');
const sourceOptions = computed(() => cueSourceOptions(props.items));
const visibleCues = computed(() => filterCuesBySource(props.items, source.value));

// 条目更新即复位筛选（换场先清空、同场重拉同口径），与加载状态机同拍
watch(
  () => props.items,
  () => {
    source.value = '';
  },
);
</script>

<style scoped>
.cue-filter {
  margin-bottom: 6px;
}

/* flat 形态的口径说明与内容留一行间距 */
.flat-hint {
  display: block;
  margin: 0 0 10px;
}

.cue-label {
  flex: none;
  font-size: 12px;
}

/* 来源与分数：跟在标题后面的弱化说明 */
.cue-why {
  margin-left: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}

/* 加载错误紧贴标题 */
.error-first {
  margin: 0;
}
</style>
