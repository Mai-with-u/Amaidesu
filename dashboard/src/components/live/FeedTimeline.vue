<template>
  <div class="feed-timeline" :class="{ 'is-compact': compact, 'is-simple': simple }">
    <div v-if="entries.length === 0" class="stage-empty">
      <el-icon class="stage-empty-icon"><Monitor /></el-icon>
      <p class="stage-empty-text">{{ emptyText }}</p>
    </div>

    <ol v-else class="feed">
      <li
        v-for="entry in entries"
        :key="entry.id"
        class="feed-row"
        :class="{ 'is-anchored': entry.id === anchorEntryId }"
        :data-entry-id="entry.id"
      >
        <!-- 注册表直派：卡片按 kind 查表渲染（registry.ts 是分发的唯一缝隙，
             全 kind 注册由类型穷尽保证）；宿主上下文经 provide 下发卡片 -->
        <Component :is="CARD_REGISTRY[entry.kind]" :entry="entry" :now-ms="nowMs" />
      </li>
    </ol>
  </div>
</template>

<script setup lang="ts">
/**
 * 直播时间线视图（直播控制台 + 首页共用）
 *
 * 外壳只管三件事：列表布局与模式类（timeline/chat/compact/simple）、滚动锚定
 * 之外的行序合成（会话模式的过程折叠）、宿主上下文下发。全部行类型由
 * cards/registry.ts 按条目 kind 查表分发，卡片的版式与取值逻辑内聚在各自的
 * 卡片文件里；token 统计 / 思考正文 / 上下文分段三套懒取缓存在此单例持有，
 * 经 context.ts 的宿主上下文供卡片按条目取值。
 *
 * 输入：已折叠的 ShowEntry[]（顺序即展示顺序——上游负责按时间归并，
 *      控制台把思考行合成进事件条目流，首页只取事件条目）
 * 控制台独占能力（暂停/清空、注入面板、滚动跟随）留在 LiveObserver；
 * 本组件对上游数据来源无要求，可被任何 Vue 页面复用。
 *
 * 可选的决策轮点击（roundClickable）：开启后带轮次 ID 的决策/裁决/工具/发言行
 * 可点并上抛 roundClick，由页面决定跳转去向；默认关闭，首页复用不受影响。
 */
import { computed } from 'vue';
import { Monitor } from '@element-plus/icons-vue';
import { CARD_REGISTRY } from '@/components/live/cards/registry';
import { useCardHost } from '@/components/live/cards/useCardHost';
import '@/components/live/cards/cards-shared.css';
import type { ShowEntry } from '@/utils/liveFeed';
import { useNowTick } from '@/composables/useNowTick';

interface Props {
  /** 已折叠好的时间线条目（顺序即展示顺序） */
  entries: ShowEntry[];
  /** 紧凑模式：用于首页缩略展示，缩短行距/字号 */
  compact?: boolean;
  /** 行布局：timeline=单列沿脊线的时间线；chat=会话模式（观众左 / 主播右 / 过程行居中） */
  /** entries 为空时的提示语 */
  emptyText?: string;
  /** 决策轮可点：带轮次 ID 的决策/裁决/工具/发言行点击后上抛 roundClick
   *  （默认关闭——首页复用本组件时行不可点） */
  roundClickable?: boolean;
  /** 简洁档：藏卡面上的诊断胶囊行（缓存/Token/模型/上下文/完整请求），
   *  只留内容本体——管线诊断卡由页面层条目过滤排除，这里兜卡面上的统计胶囊 */
  simple?: boolean;
  /** 锚定高亮条目 id（回看深链 ?at= 定位；非空时该行加 is-anchored 类） */
  anchorEntryId?: string | null;
}

const props = withDefaults(defineProps<Props>(), {
  compact: false,
  emptyText: '静候消息与决策',
  roundClickable: false,
  simple: false,
  anchorEntryId: null,
});

const emit = defineEmits<{
  /** 决策轮行点击（roundClickable 开启且行带轮次 ID 时触发），页面据此跳 Agent 页 */
  roundClick: [entry: ShowEntry];
}>();

// 宿主上下文由本组合式 provide 给卡片
useCardHost({
  entries: computed(() => props.entries),
  compact: () => props.compact,
  roundClickable: () => props.roundClickable,
  onRoundClick: entry => emit('roundClick', entry),
});

// 1s tick：让相对时间标签（"刚刚 / 12s 前"）每秒刷新一次；独立维护不依赖父组件
const nowMs = useNowTick();
</script>

<style scoped>
/* 空态                                                          */
.stage-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: 40px 0;
}
.stage-empty-icon {
  font-size: 44px;
  color: var(--border-color-dark);
}
.stage-empty-text {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.3px;
  color: var(--text-placeholder);
}

/* 流：单列居左时间轴脊线——所有条目沿轴排布，靠样式区分             */
.feed {
  position: relative;
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.feed::before {
  content: '';
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 14px;
  width: 1px;
  background: var(--border-color);
}

.feed-row {
  display: flex;
  flex-direction: column;
  animation: rowIn 0.22s cubic-bezier(0.33, 1, 0.68, 1);
}

/* 回看深链锚定高亮（?at= 定位的目标行，数秒后自动熄灭） */
.feed-row.is-anchored {
  outline: 2px solid var(--color-primary);
  outline-offset: 4px;
  border-radius: var(--radius-md);
  background: var(--bg-hover);
  transition: background 0.6s ease;
}

@keyframes rowIn {
  from {
    opacity: 0;
    transform: translateY(5px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 紧凑模式（首页缩略视图）；卡片内部字号经 :deep 下探                */
.feed-timeline.is-compact .feed {
  gap: 6px;
}
.feed-timeline.is-compact .feed-row {
  /* 取消逐行入场动画，避免首页连续刷新时频繁闪烁 */
  animation: none;
}
.feed-timeline.is-compact .chat,
.feed-timeline.is-compact .decision,
.feed-timeline.is-compact .act {
  max-width: 100%;
}
.feed-timeline.is-compact :deep(.say),
.feed-timeline.is-compact :deep(.act-text) {
  font-size: 12px;
}
.feed-timeline.is-compact :deep(.stamp) {
  font-size: 9px;
}

/* 窄屏                                                          */
@media (max-width: 860px) {
  .chat,
  .chat--gift,
  .chat--super_chat,
  .decision,
  .act {
    max-width: 100%;
    margin-left: 0;
  }
}
</style>
