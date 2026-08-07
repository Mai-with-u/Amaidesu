import type { WebSocketMessage, SubscribeRequest } from '@/types';

type MessageCallback = (message: WebSocketMessage) => void;
type ConnectCallback = () => void;
type DisconnectCallback = () => void;

class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectDelay = 3000;
  private readonly maxReconnectDelay = 30000;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private manualClose = false;
  private messageCallbacks: MessageCallback[] = [];
  private connectCallbacks: ConnectCallback[] = [];
  private disconnectCallbacks: DisconnectCallback[] = [];
  private subscribedEvents: Set<string> = new Set();

  constructor() {
    // 根据当前页面协议选择 ws 或 wss
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.url = `${protocol}//${window.location.host}/ws`;
  }

  connect(): Promise<void> {
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)
    ) {
      return Promise.resolve();
    }
    // 显式 connect 表示用户希望建立连接，清除手动关闭标记（允许自动重连）
    this.manualClose = false;
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url);

        this.ws.onopen = () => {
          console.log('WebSocket connected');
          this.reconnectDelay = 3000;

          if (this.subscribedEvents.size > 0) {
            this.sendSubscribe(Array.from(this.subscribedEvents));
          }
          this.sendSubscribe(['*']);

          this.connectCallbacks.forEach(cb => cb());
          resolve();
        };

        this.ws.onmessage = event => {
          try {
            const message: WebSocketMessage = JSON.parse(event.data);
            // 应用层心跳：响应 ping，供后端检测半开连接
            if (message.type === 'ping') {
              this.sendRaw({ type: 'pong' });
              return;
            }
            this.messageCallbacks.forEach(cb => cb(message));
          } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
          }
        };

        this.ws.onclose = () => {
          console.log('WebSocket disconnected');
          this.disconnectCallbacks.forEach(cb => cb());
          if (!this.manualClose) {
            this.scheduleReconnect();
          }
        };

        this.ws.onerror = error => {
          console.error('WebSocket error:', error);
          reject(error);
        };
      } catch (error) {
        reject(error);
      }
    });
  }

  /** 指数退避调度重连：3s → 6s → 12s → ... → 30s 封顶，无限重试 */
  private scheduleReconnect() {
    this.reconnectTimer = setTimeout(() => {
      this.connect().catch(err => console.error('WebSocket reconnect failed:', err));
    }, this.reconnectDelay);
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
  }

  disconnect() {
    this.manualClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private sendRaw(payload: object) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  subscribe(events: string[]) {
    this.subscribedEvents = new Set([...this.subscribedEvents, ...events]);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.sendSubscribe(events);
    }
  }

  unsubscribe(events: string[]) {
    events.forEach(e => this.subscribedEvents.delete(e));
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const request: SubscribeRequest = {
        action: 'unsubscribe',
        events,
      };
      this.ws.send(JSON.stringify(request));
    }
  }

  private sendSubscribe(events: string[]) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const request: SubscribeRequest = {
        action: 'subscribe',
        events,
      };
      this.ws.send(JSON.stringify(request));
    }
  }

  onMessage(callback: MessageCallback) {
    if (!this.messageCallbacks.includes(callback)) {
      this.messageCallbacks.push(callback);
    }
  }

  onConnect(callback: ConnectCallback) {
    if (!this.connectCallbacks.includes(callback)) {
      this.connectCallbacks.push(callback);
    }
  }

  onDisconnect(callback: DisconnectCallback) {
    if (!this.disconnectCallbacks.includes(callback)) {
      this.disconnectCallbacks.push(callback);
    }
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}

// Singleton instance
export const wsClient = new WebSocketClient();

export default wsClient;
