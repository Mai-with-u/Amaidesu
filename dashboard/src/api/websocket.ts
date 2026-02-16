import type { WebSocketMessage, SubscribeRequest } from '@/types';

type MessageCallback = (message: WebSocketMessage) => void;
type ConnectCallback = () => void;
type DisconnectCallback = () => void;

class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 3000;
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
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url);

        this.ws.onopen = () => {
          console.log('WebSocket connected');
          this.reconnectAttempts = 0;

          // 重新订阅之前的事件
          if (this.subscribedEvents.size > 0) {
            this.sendSubscribe(Array.from(this.subscribedEvents));
          }

          this.connectCallbacks.forEach((cb) => cb());
          resolve();
        };

        this.ws.onmessage = (event) => {
          try {
            const message: WebSocketMessage = JSON.parse(event.data);
            this.messageCallbacks.forEach((cb) => cb(message));
          } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
          }
        };

        this.ws.onclose = () => {
          console.log('WebSocket disconnected');
          this.disconnectCallbacks.forEach((cb) => cb());
          this.attemptReconnect();
        };

        this.ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          reject(error);
        };
      } catch (error) {
        reject(error);
      }
    });
  }

  private attemptReconnect() {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      this.reconnectAttempts++;
      console.log(
        `Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`,
      );
      setTimeout(() => this.connect(), this.reconnectDelay);
    }
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  subscribe(events: string[]) {
    this.subscribedEvents = new Set([...this.subscribedEvents, ...events]);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.sendSubscribe(events);
    }
  }

  unsubscribe(events: string[]) {
    events.forEach((e) => this.subscribedEvents.delete(e));
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
    this.messageCallbacks.push(callback);
  }

  onConnect(callback: ConnectCallback) {
    this.connectCallbacks.push(callback);
  }

  onDisconnect(callback: DisconnectCallback) {
    this.disconnectCallbacks.push(callback);
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}

// Singleton instance
export const wsClient = new WebSocketClient();

export default wsClient;
