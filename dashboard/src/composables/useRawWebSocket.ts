/**
 * 小部件页裸 WebSocket 连接的组合式（弹幕 / 字幕 / 小游戏等专用协议共用）。
 *
 * 与主事件流 websocket.ts 的 store 化连接无关：这些小部件页各自有专属
 * 的轻量协议，只收不发的消息流。setup 中调用即连接，样板收敛于此：
 * 异常关闭后 3 秒重连；onerror 主动 close 交给 onclose 统一调度；
 * 作用域销毁时置 disposed 并关闭，之后的 close 不再触发重连
 * （并清掉未触发的重连定时器，避免销毁后幽灵重连）。
 *
 * 返回的 status 反映连接生命周期（connecting → connected ⇄ disconnected），
 * 供页面渲染断线指示；重连等待期与连接建立前都是 connecting/disconnected，
 * 由页面自行决定展示强度。
 *
 * 生命周期语义：非 keep-alive 小部件页，随组件卸载（onScopeDispose）断开。
 */

import { onScopeDispose, ref, type Ref } from 'vue';

export type WidgetSocketStatus = 'connecting' | 'connected' | 'disconnected';

interface RawWebSocketHandlers {
  /** 原始消息回调；JSON 解析留在调用方（各页协议不同） */
  onMessage: (event: MessageEvent) => void;
}

export function useRawWebSocket(
  path: string,
  handlers: RawWebSocketHandlers,
): {
  status: Ref<WidgetSocketStatus>;
} {
  let ws: WebSocket | null = null;
  // 主动关闭标志：作用域销毁后的 close 不应触发重连
  let disposed = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  const status = ref<WidgetSocketStatus>('connecting');

  function connect(): void {
    status.value = 'connecting';
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}${path}`);

    ws.onopen = () => {
      status.value = 'connected';
    };

    ws.onmessage = handlers.onMessage;

    ws.onclose = () => {
      if (disposed) return;
      status.value = 'disconnected';
      reconnectTimer = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws?.close();
    };
  }

  connect();

  onScopeDispose(() => {
    disposed = true;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    ws?.close();
  });

  return { status };
}
