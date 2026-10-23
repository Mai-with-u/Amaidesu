<template>
  <div class="danmaku-container">
    <div ref="messagesRef" class="messages">
      <TransitionGroup name="message">
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="message"
          :class="msg.message_type"
          :style="{ fontSize: `${fontSize}px` }"
        >
          <span class="username">{{ msg.user_name }}</span>
          <span class="content">{{ getDisplayText(msg) }}</span>
        </div>
      </TransitionGroup>
    </div>
    <WidgetStatusBadge :status="status" />
  </div>
</template>

<script setup lang="tsx">
import { ref, nextTick } from 'vue';

import { useRawWebSocket } from '@/composables/useRawWebSocket';
import WidgetStatusBadge from '@/components/widget/WidgetStatusBadge.vue';

interface DanmakuMessage {
  id: string;
  user_name: string;
  content: string;
  message_type: string;
  gift_name?: string;
  gift_count?: number;
  sc_price?: number;
  sc_message?: string;
  guard_level?: number;
  /** 模拟消息标记（simulator/mock 产生），弹幕小部件不展示 */
  simulated?: boolean;
}

const messages = ref<DanmakuMessage[]>([]);
const messagesRef = ref<HTMLElement | null>(null);
const maxMessages = 15;
/** 弹幕字号（像素）；连接握手时随 style 消息以配置值覆盖 */
const fontSize = ref<number>(16);

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).substring(2);
}

function addMessage(msg: DanmakuMessage) {
  const messageWithId = { ...msg, id: generateId() };
  messages.value.push(messageWithId);

  if (messages.value.length > maxMessages) {
    messages.value.shift();
  }

  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight;
    }
  });
}

function getDisplayText(msg: DanmakuMessage): string {
  switch (msg.message_type) {
    case 'gift':
      return `送出 ${msg.gift_name || '礼物'} x${msg.gift_count || 1}`;
    case 'super_chat':
      return `¥${msg.sc_price || 0} ${msg.sc_message || msg.content}`;
    case 'guard': {
      const guardNames: Record<number, string> = { 1: '总督', 2: '提督', 3: '舰长' };
      return `开通了 ${guardNames[msg.guard_level || 3] || '大航海'}`;
    }
    case 'enter':
      return '进入了直播间';
    default:
      return msg.content;
  }
}

// /ws/widget/danmaku 专用协议（与主 WS 事件流无关）：
// - 实时推送：{ type: "new_message", message: {...} }
// - 连接握手：{ type: "history", messages: [...] }（最近 15 条）
// - 连接握手：{ type: "style", font_size }（配置的弹幕字号，随历史之后下发）
// 模拟消息（simulated=true）不在弹幕小部件展示
const { status } = useRawWebSocket('/ws/widget/danmaku', {
  onMessage: event => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'new_message') {
        const msg = data.message as DanmakuMessage | undefined;
        if (msg && msg.simulated !== true) {
          addMessage(msg);
        }
      } else if (data.type === 'history') {
        messages.value = (data.messages as DanmakuMessage[])
          .filter(m => m.simulated !== true)
          .map(m => ({ ...m, id: generateId() }));
      } else if (data.type === 'style') {
        if (typeof data.font_size === 'number') {
          fontSize.value = data.font_size;
        }
      }
    } catch (e) {
      console.error('解析消息失败:', e);
    }
  },
});
</script>

<style>
html,
body,
#app {
  background: transparent !important;
}
</style>

<style scoped>
/*
 * 观众面配色与麦麦的角色色绑定：普通弹幕用叶冠绿、礼物与麦麦回复用发色橙，
 * 全部压掉荧光感；底色统一暖黑半透明，靠描边色区分消息类型。
 */
.danmaku-container {
  width: 100%;
  height: 100vh;
  background: transparent !important;
  padding: 15px 20px;
  box-sizing: border-box;
  overflow: hidden;
  font-family: 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif;
}

.messages {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  overflow-y: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.messages::-webkit-scrollbar {
  display: none;
}

.message {
  padding: 8px 14px;
  border-radius: 8px;
  color: #fff;
  text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.8);
  background: rgba(14, 11, 8, 0.38);
  backdrop-filter: blur(4px);
}

.message.text {
  border-left: 3px solid #93cf87;
}

.message.gift {
  background: rgba(255, 162, 89, 0.22);
  border-left: 3px solid #ffa259;
}

.message.super_chat {
  background: linear-gradient(90deg, rgba(255, 177, 92, 0.34), rgba(255, 122, 89, 0.22));
  border-left: 3px solid #ffb35c;
}

.message.guard {
  background: rgba(90, 181, 170, 0.22);
  border-left: 3px solid #5ab5aa;
}

.message.enter {
  background: rgba(120, 110, 100, 0.18);
  border-left: 3px solid #8f887d;
}

.message.reply {
  background: rgba(255, 154, 85, 0.16);
  border-left: 3px solid #ff9a55;
}

.username {
  color: #9fdc8f;
  font-weight: 600;
  margin-right: 6px;
}

.message.reply .username {
  color: #ffb27a;
}

.content {
  color: #fff;
}

.message-enter-active {
  animation: slideIn 0.4s ease-out;
}

.message-leave-active {
  animation: slideOut 0.3s ease-in;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateX(-20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes slideOut {
  from {
    opacity: 1;
    transform: translateX(0);
  }
  to {
    opacity: 0;
    transform: translateX(20px);
  }
}
</style>
