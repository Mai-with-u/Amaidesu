<!-- 聊天气泡卡：弹幕 / 礼物 / SC / 上舰 / 游戏内聊天共用，chat--{kind} 修饰符区分配色 -->
<script setup lang="ts">
import { relativeTime } from '@/utils/liveFeed';
import type { CardProps } from './registry';

defineProps<CardProps>();
</script>

<template>
  <div class="chat" :class="`chat--${entry.kind}`">
    <span class="avatar" aria-hidden="true">{{ entry.initial }}</span>
    <div class="bubble">
      <div class="bubble-head">
        <span class="who" :title="entry.actor">{{ entry.actor }}</span>
        <span v-if="entry.badge" class="chip">{{ entry.badge }}</span>
        <span v-if="entry.simulated" class="chip" title="来自控制台注入的模拟消息"> 模拟 </span>
        <span v-if="entry.money" class="money mono">{{ entry.money }}</span>
        <span class="grow" />
        <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
      </div>
      <p class="say">{{ entry.text }}</p>
    </div>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 82%;
}

.avatar {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-card);
  border: 1px solid var(--color-collector);
  color: var(--color-collector);
  box-shadow: 0 0 0 3px var(--bg-card);
  z-index: 1;
}

.bubble {
  flex: 1;
  min-width: 0;
  padding: 7px 12px;
  border-radius: 4px 12px 12px 12px;
  background: var(--bg-hover);
}

.bubble-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.who {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-collector);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.say {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

/* --- 礼物：暖色高亮 --- */
.chat--gift {
  max-width: 88%;
}
.chat--gift .avatar {
  border-color: var(--color-warning);
  color: var(--color-warning);
}
.chat--gift .bubble {
  background: var(--color-warning-bg);
  border-left: 2px solid var(--color-warning);
  box-shadow: var(--shadow-sm);
}
.chat--gift .who {
  color: var(--color-warning);
}
.chat--gift .say {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

/* --- SC：最强高亮（暖色渐变 + 金额） --- */
.chat--super_chat {
  max-width: 92%;
}
.chat--super_chat .avatar {
  border-color: var(--color-danger);
  color: var(--color-danger);
}
.chat--super_chat .bubble {
  padding: 10px 14px;
  border-left: 3px solid var(--color-danger);
  background: linear-gradient(100deg, var(--color-danger-bg), var(--color-warning-bg));
  box-shadow: var(--shadow-md);
}
.chat--super_chat .who {
  font-size: 12px;
  color: var(--color-danger);
}
.chat--super_chat .chip {
  background: var(--color-danger);
  color: var(--text-inverse);
  border-color: var(--color-danger);
}
.chat--super_chat .money {
  font-size: 14px;
  color: var(--color-danger);
}
.chat--super_chat .say {
  font-size: 15px;
  font-weight: 500;
  line-height: 1.55;
  color: var(--text-primary);
}

/* --- 上舰（guard）：付费卡片形态，绿色系（与游戏域同色系区分于礼物暖色/SC 红色） --- */
.chat--guard {
  max-width: 90%;
}
.chat--guard .avatar {
  border-color: var(--color-success);
  color: var(--color-success);
}
.chat--guard .bubble {
  padding: 10px 14px;
  border-left: 3px solid var(--color-success);
  background: var(--color-success-bg);
  box-shadow: var(--shadow-sm);
}
.chat--guard .who {
  color: var(--color-success);
}
.chat--guard .chip {
  background: var(--color-success);
  color: var(--text-inverse);
  border-color: var(--color-success);
}
.chat--guard .say {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

/* --- 游戏内聊天（game_chat）：与 B 站弹幕区分的游戏域绿色系 --- */
.chat--game_chat {
  max-width: 82%;
}
.chat--game_chat .avatar {
  border-color: var(--color-game);
  color: var(--color-game);
  background: var(--color-game-bg);
}
.chat--game_chat .bubble {
  border-left: 2px solid var(--color-game);
  background: var(--color-game-bg);
}
.chat--game_chat .who {
  color: var(--color-game);
}
</style>
