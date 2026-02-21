<template>
  <div class="session-history">
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">会话历史</h1>
        <p class="page-subtitle">实时显示输入消息与决策响应</p>
      </div>
      <div class="header-right">
        <el-button @click="clearMessages" :disabled="messages.length === 0">
          <el-icon><Delete /></el-icon>
          清空消息
        </el-button>
      </div>
    </header>

    <!-- 消息列表 -->
    <div class="chat-container" ref="chatContainer">
      <div v-if="messages.length === 0" class="empty-state">
        <el-icon class="empty-icon"><ChatLineRound /></el-icon>
        <p>暂无消息</p>
        <p class="empty-hint">在下方输入框发送消息开始对话</p>
      </div>
      <div
        v-for="msg in messages"
        :key="msg.id"
        :class="['message-row', msg.type === 'normalized_message' ? 'row-left' : 'row-right']"
      >
        <div
          :class="['message', msg.type === 'normalized_message' ? 'message-left' : 'message-right']"
        >
          <div class="message-sender">{{ msg.sender }}</div>
          <div class="message-bubble">{{ msg.content }}</div>
          <div class="message-time">{{ formatTime(msg.timestamp) }}</div>
        </div>
      </div>
    </div>

    <!-- 双输入框 -->
    <div class="input-area">
      <div class="input-group left">
        <div class="input-label">
          <el-tag size="small" type="info">弹幕输入</el-tag>
          <span class="priority-hint">优先级: 1 (最高)</span>
        </div>
        <el-input
          v-model="leftInput"
          type="textarea"
          placeholder="输入弹幕消息..."
          :rows="2"
          :disabled="sending"
          @keydown.enter.ctrl="sendNormalizedMessage"
        />
        <el-button type="primary" :loading="sending" @click="sendNormalizedMessage">
          <el-icon><Promotion /></el-icon>
          发送弹幕
        </el-button>
      </div>
      <div class="input-group right">
        <div class="input-label">
          <el-tag size="small" type="success">主播回应</el-tag>
        </div>
        <el-input
          v-model="rightInput"
          type="textarea"
          placeholder="输入主播回应..."
          :rows="2"
          :disabled="sending"
          @keydown.enter.ctrl="sendIntent"
        />
        <el-button type="success" :loading="sending" @click="sendIntent">
          <el-icon><Promotion /></el-icon>
          发送回应
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue';
import { storeToRefs } from 'pinia';
import { Delete, ChatLineRound, Promotion } from '@element-plus/icons-vue';
import { useSessionStore } from '@/stores/session';

const sessionStore = useSessionStore();
const { messages, sending } = storeToRefs(sessionStore);

const leftInput = ref('');
const rightInput = ref('');
const chatContainer = ref<HTMLElement | null>(null);

// 发送弹幕消息（NormalizedMessage）
async function sendNormalizedMessage() {
  const text = leftInput.value.trim();
  if (!text) return;

  await sessionStore.sendNormalizedMessage(text, 'dashboard');
  leftInput.value = '';

  // 滚动到底部
  await nextTick();
  scrollToBottom();
}

// 发送主播回应（Intent）
async function sendIntent() {
  const text = rightInput.value.trim();
  if (!text) return;

  await sessionStore.sendIntent(text);
  rightInput.value = '';

  // 滚动到底部
  await nextTick();
  scrollToBottom();
}

// 清空消息
function clearMessages() {
  sessionStore.clearMessages();
}

// 格式化时间
function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// 滚动到底部
function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight;
  }
}

// 生命周期
onMounted(() => {
  sessionStore.connect();
});

onUnmounted(() => {
  // 只断开 WebSocket 连接，不清空消息
  // 这样切换页面后回来仍然可以看到历史消息
  sessionStore.disconnect();
});
</script>

<style scoped>
.session-history {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: var(--spacing-lg);
  gap: var(--spacing-md);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
}

.page-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary);
}

.page-subtitle {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 4px 0 0 0;
}

/* 聊天容器 */
.chat-container {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-md);
  background-color: var(--bg-page);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  min-height: 300px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-placeholder);
}

.empty-icon {
  font-size: 48px;
  margin-bottom: var(--spacing-md);
}

.empty-hint {
  font-size: 12px;
  margin-top: var(--spacing-xs);
}

/* 消息行 */
.message-row {
  display: flex;
}

.row-left {
  justify-content: flex-start;
}

.row-right {
  justify-content: flex-end;
}

/* 消息气泡 */
.message {
  max-width: 70%;
  padding: var(--spacing-sm) var(--spacing-md);
  border-radius: var(--radius-md);
  position: relative;
}

.message-left {
  background-color: var(--bg-color);
  border: 1px solid var(--border-color-light);
}

.message-right {
  background-color: var(--color-primary-light-9);
  border: 1px solid var(--color-primary-light-8);
}

.message-sender {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.message-bubble {
  font-size: 14px;
  line-height: 1.5;
  color: var(--text-primary);
  word-break: break-word;
}

.message-time {
  font-size: 11px;
  color: var(--text-placeholder);
  margin-top: 4px;
  text-align: right;
}

/* 输入区域 */
.input-area {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--spacing-lg);
  flex-shrink: 0;
  padding: var(--spacing-md);
  background-color: var(--bg-page);
  border-radius: var(--radius-lg);
}

.input-group {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.input-label {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.priority-hint {
  font-size: 12px;
  color: var(--text-placeholder);
}

.input-group .el-button {
  align-self: flex-end;
}
</style>
