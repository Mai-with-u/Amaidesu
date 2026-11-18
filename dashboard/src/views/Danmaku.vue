<template>
  <div class="danmaku-container">
    <div v-if="isPreview && widgetDisabled" class="preview-disabled-banner">
      弹幕小部件已停用，到管理页开启并重启后生效
    </div>
    <div v-else-if="isPreview && messages.length === 0" class="preview-placeholder">
      弹幕小部件预览——连接后此处显示实时弹幕
    </div>
    <div v-show="messages.length > 0" ref="messagesRef" class="messages">
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

import { configApi } from '@/api';
import { useRawWebSocket } from '@/composables/useRawWebSocket';
import { isPreviewMode } from '@/composables/useWidgetCatalog';
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
/** 弹幕缓冲上限；连接握手时随 style 消息以配置值覆盖（后端保证 style 先于 history 下发） */
const maxMessages = ref<number>(15);
/** 预览态（管理页"打开"带 ?preview=1）才显示占位与停用提示；嵌入态保持全透明 */
const isPreview = isPreviewMode();
/** 预览态停用判定：undefined = 未知（读取失败），不显示停用横幅 */
const widgetDisabled = ref<boolean>(false);
/** 弹幕字号（像素）；连接握手时随 style 消息以配置值覆盖 */
const fontSize = ref<number>(16);

function generateId(): string {
  return Date.now().toString(36) + Math.random().toString(36).substring(2);
}

function addMessage(msg: DanmakuMessage) {
  const messageWithId = { ...msg, id: generateId() };
  messages.value.push(messageWithId);

  if (messages.value.length > maxMessages.value) {
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
// - 连接握手：{ type: "style", font_size, max_messages }（先于 history，前端须先拿到缓冲上限再收历史）
// - 连接握手：{ type: "history", messages: [...] }（最近 max_messages 条）
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
        if (typeof data.max_messages === 'number' && data.max_messages >= 1) {
          maxMessages.value = data.max_messages;
        }
      }
    } catch (e) {
      console.error('解析消息失败:', e);
    }
  },
});

// 预览态停用判定：读 dashboard 段 danmaku_widget.enabled；读取失败按"未知"处理，
// 不显示停用横幅，空态占位照旧。嵌入态不做此请求（直播画面不碰）
if (isPreview) {
  void (async () => {
    try {
      const resp = await configApi.getConfig();
      const cfg = (resp.data?.config ?? {}) as Record<string, unknown>;
      const dashboard = cfg['dashboard'];
      const widget =
        dashboard && typeof dashboard === 'object'
          ? (dashboard as Record<string, unknown>)['danmaku_widget']
          : undefined;
      const enabled =
        widget && typeof widget === 'object'
          ? (widget as Record<string, unknown>)['enabled']
          : undefined;
      widgetDisabled.value = enabled === false;
    } catch (error) {
      console.error('读取弹幕小部件配置失败', error);
    }
  })();
}
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

/* 以下两条只在预览态渲染（嵌入态不进模板分支），样式轻量、不遮全屏 */
.preview-placeholder {
  color: rgba(128, 128, 128, 0.85);
  font-size: 18px;
}

.preview-disabled-banner {
  align-self: flex-start;
  padding: 10px 18px;
  border-radius: 6px;
  background: rgba(255, 136, 0, 0.85);
  color: #fff;
  font-size: 18px;
  font-weight: bold;
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

/* 左侧彩边 = 消息类型的观众可读编码（弹幕流无其他 UI 承载类型信息），经审保留 */
.message.text {
  border-left: 3px solid #00ff88;
}

.message.gift {
  background: rgba(255, 136, 0, 0.25);
  border-left: 3px solid #ff8800;
}

.message.super_chat {
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
