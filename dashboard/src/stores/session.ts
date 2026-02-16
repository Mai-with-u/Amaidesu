import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useWebSocketStore } from './websocket';
import { debugApi, messageApi } from '@/api';
import type { ChatMessage, InjectIntentRequest, MessageItem, WebSocketMessage } from '@/types';

export const useSessionStore = defineStore('session', () => {
  const messages = ref<ChatMessage[]>([]);
  const maxMessages = 100;
  const sending = ref(false);
  const sessionId = 'dashboard'; // 默认会话 ID

  // 处理 WebSocket 消息
  function handleEvent(message: WebSocketMessage) {
    // WebSocket 事件类型来自 broadcaster.py 的 EVENT_TYPE_MAP:
    // - "message.received" 对应 CoreEvents.INPUT_MESSAGE_READY
    // - "decision.intent" 对应 CoreEvents.DECISION_INTENT_GENERATED

    // 定义消息数据结构接口
    interface NormalizedMessageData {
      text?: string;
      timestamp?: number;
      importance?: number;
    }

    interface IntentData {
      id?: string;
      response_text?: string;
      emotion?: string;
    }

    interface MessageReceivedData {
      message?: NormalizedMessageData;
      source?: string;
    }

    interface DecisionIntentData {
      intent_data?: IntentData;
      provider?: string;
    }

    if (message.type === 'message.received') {
      const data = message.data as MessageReceivedData;
      // 添加 NormalizedMessage 到左侧
      // 数据结构: message.data.message (NormalizedMessage), message.data.source
      const msgId = String(data.message?.timestamp) || crypto.randomUUID();

      // 去重检查
      if (messages.value.some((m) => m.id === msgId)) {
        return;
      }

      messages.value.push({
        id: msgId,
        type: 'normalized_message',
        sender: data.source || '弹幕',
        content: data.message?.text || '',
        timestamp: message.timestamp,
        priority: data.message?.importance,
      });
    } else if (message.type === 'decision.intent') {
      const data = message.data as DecisionIntentData;
      // 添加 Intent 到右侧
      // 数据结构: message.data.intent_data (Intent 字典), message.data.provider
      const msgId = data.intent_data?.id || crypto.randomUUID();

      // 去重检查
      if (messages.value.some((m) => m.id === msgId)) {
        return;
      }

      messages.value.push({
        id: msgId,
        type: 'intent',
        sender: '主播',
        content: data.intent_data?.response_text || '',
        timestamp: message.timestamp,
        emotion: data.intent_data?.emotion,
      });
    }

    // 限制消息数量
    if (messages.value.length > maxMessages) {
      messages.value.shift();
    }
  }

  // 连接 WebSocket 并加载历史消息
  async function connect() {
    // 先加载历史消息
    try {
      const response = await messageApi.getSessionMessages(sessionId, maxMessages);
      if (response.data.messages) {
        // 将历史消息转换为 ChatMessage
        const historyMessages: ChatMessage[] = response.data.messages.map((msg: MessageItem) => {
          const isUser = msg.role === 'user';
          return {
            id: msg.id,
            type: isUser ? ('normalized_message' as const) : ('intent' as const),
            sender: isUser ? (msg.metadata?.source as string) || '弹幕' : '主播',
            content: msg.content,
            timestamp: msg.timestamp,
            priority: msg.metadata?.importance as number | undefined,
            emotion: msg.metadata?.emotion as string | undefined,
          };
        });
        // 按时间正序排列
        historyMessages.sort((a, b) => a.timestamp - b.timestamp);
        messages.value = historyMessages;
      }
    } catch (e) {
      console.warn('加载历史消息失败:', e);
    }

    // 连接 WebSocket 接收实时消息
    const wsStore = useWebSocketStore();
    wsStore.subscribe(handleEvent);
    wsStore.connect();
  }

  // 发送 NormalizedMessage（弹幕输入）
  async function sendNormalizedMessage(text: string, source: string = 'dashboard') {
    sending.value = true;
    try {
      await debugApi.injectMessage({
        text,
        source,
        importance: 1, // 最高优先级
      });
    } finally {
      sending.value = false;
    }
  }

  // 发送 Intent（主播回应）
  async function sendIntent(text: string, responseText?: string) {
    sending.value = true;
    try {
      const request: InjectIntentRequest = {
        text,
        responseText,
        emotion: 'neutral',
        source: 'dashboard',
      };
      await debugApi.injectIntent(request);
    } finally {
      sending.value = false;
    }
  }

  // 清空消息
  function clearMessages() {
    messages.value = [];
  }

  // 断开连接（取消订阅）
  function disconnect() {
    const wsStore = useWebSocketStore();
    wsStore.unsubscribe(handleEvent);
  }

  return {
    messages,
    sending,
    connect,
    disconnect,
    sendNormalizedMessage,
    sendIntent,
    clearMessages,
  };
});
