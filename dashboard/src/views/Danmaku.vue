<template>
  <div class="danmaku-container">
    <div class="messages" ref="messagesRef">
      <TransitionGroup name="message">
        <div v-for="msg in messages" :key="msg.id" class="message" :class="msg.message_type">
          <span class="username">{{ msg.user_name }}</span>
          <span class="content">{{ getDisplayText(msg) }}</span>
        </div>
      </TransitionGroup>
    </div>
  </div>
</template>

<script setup lang="tsx">
import { ref, onMounted, onUnmounted, nextTick } from 'vue';

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
}

const messages = ref<DanmakuMessage[]>([]);
const messagesRef = ref<HTMLElement | null>(null);
const maxMessages = 15;
let ws: WebSocket | null = null;

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
    case 'superchat':
      return `¥${msg.sc_price || 0} ${msg.sc_message || msg.content}`;
    case 'guard':
      const guardNames: Record<number, string> = { 1: '总督', 2: '提督', 3: '舰长' };
      return `开通了 ${guardNames[msg.guard_level || 3] || '大航海'}`;
    case 'enter':
      return '进入了直播间';
    default:
      return msg.content;
  }
}

function connect() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${window.location.host}/ws/danmaku`);

  ws.onmessage = event => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'new_message') {
        addMessage(data.message);
      } else if (data.type === 'history') {
        messages.value = data.messages.map((m: DanmakuMessage) => ({
          ...m,
          id: generateId(),
        }));
      }
    } catch (e) {
      console.error('解析消息失败:', e);
    }
  };

  ws.onclose = () => {
    setTimeout(connect, 3000);
  };

  ws.onerror = () => {
    ws?.close();
  };
}

onMounted(() => {
  connect();
});

onUnmounted(() => {
  if (ws) {
    ws.close();
  }
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
.danmaku-container {
  width: 100%;
  height: 100vh;
  background: transparent !important;
  padding: 15px 20px;
  box-sizing: border-box;
  overflow: hidden;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
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
  border-radius: 6px;
  color: #fff;
  text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.8);
  background: rgba(0, 0, 0, 0.3);
  backdrop-filter: blur(4px);
}

.message.danmaku {
  border-left: 3px solid #00ff88;
}

.message.gift {
  background: rgba(255, 136, 0, 0.25);
  border-left: 3px solid #ff8800;
}

.message.superchat {
  background: linear-gradient(90deg, rgba(255, 107, 107, 0.3), rgba(107, 255, 107, 0.2));
  border-left: 3px solid #ff6b6b;
}

.message.guard {
  background: rgba(78, 205, 196, 0.25);
  border-left: 3px solid #4ecdc4;
}

.message.enter {
  background: rgba(100, 100, 100, 0.2);
  border-left: 3px solid #888;
}

.message.reply {
  background: rgba(64, 158, 255, 0.25);
  border-left: 3px solid #409eff;
}

.username {
  color: #00ff88;
  font-weight: 600;
  margin-right: 6px;
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
