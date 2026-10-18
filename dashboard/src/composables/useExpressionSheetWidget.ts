/**
 * 表情差分挂件页（/widget/expression_sheet）的组合式。
 *
 * 订阅主事件 WS 流（订阅句柄由页面从 useWebSocketStore → api/websocket 的
 * 共享连接注入，不自绘重连循环），过滤 streamer.speech 事件，取 payload 的
 * emotion 字段驱动差分图切换。streamer.speech 的 emotion 由生产者保证为
 * 17 枚举值之一，非法值兜底回 neutral。
 *
 * 事件消费边界（v1 范围）：只跟随 streamer.speech 事件；LLM 直调
 * expression_sheet_set_expression 工具的场景不经事件流，不反射到挂件页。
 *
 * 生命周期语义：非 keep-alive 全屏展示页——setup 订阅 + 连接，作用域销毁
 * （onScopeDispose）退订；不进主导航，也不置于 keep-alive 容器。若未来
 * 挂进 keep-alive，须改为 activated/deactivated 启停订阅，避免隐藏页白耗。
 */

import { onScopeDispose, ref, type Ref } from 'vue';

import type { WebSocketMessage } from '@/types';

/** 兜底差分图的情绪名（对应后端 Emotion.NEUTRAL 枚举值） */
export const NEUTRAL_EMOTION = 'neutral';

/** streamer.speech 事件名（WS 广播 type 沿用事件名） */
const STREAMER_SPEECH_TYPE = 'streamer.speech';

/** 订阅句柄形状（由共享 useWebSocketStore 提供，测试可注入替身） */
interface WidgetDeps {
  subscribe: (handler: (msg: WebSocketMessage) => void) => void;
  unsubscribe: (handler: (msg: WebSocketMessage) => void) => void;
  connect?: () => void;
}

export interface ExpressionSheetWidget {
  /** 当前展示的情绪（17 枚举值之一，小写；初始 neutral） */
  emotion: Ref<string>;
}

export function useExpressionSheetWidget(deps: WidgetDeps): ExpressionSheetWidget {
  const emotion = ref<string>(NEUTRAL_EMOTION);

  const handler = (msg: WebSocketMessage): void => {
    if (msg.type !== STREAMER_SPEECH_TYPE) return;
    const raw = msg.data?.emotion;
    emotion.value = typeof raw === 'string' && raw ? raw : NEUTRAL_EMOTION;
  };

  deps.connect?.();
  deps.subscribe(handler);
  onScopeDispose(() => deps.unsubscribe(handler));

  return { emotion };
}
