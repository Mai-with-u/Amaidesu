// useExpressionSheetWidget 组合式的测试
//
// 覆盖：streamer.speech 事件驱动情绪切换、非目标事件过滤、非法/缺失 emotion
// 兜底 neutral、作用域销毁后退订。node 环境运行：订阅/退订用可注入替身。

import { afterEach, describe, expect, it, vi } from 'vitest';

import { effectScope, type EffectScope } from 'vue';

import { NEUTRAL_EMOTION, useExpressionSheetWidget } from '@/composables/useExpressionSheetWidget';
import type { WebSocketMessage } from '@/types';

/** 可手动驱动回调的订阅替身 */
class FakeSubscription {
  handler: ((msg: WebSocketMessage) => void) | null = null;
  unsubscribed = false;

  subscribe = (handler: (msg: WebSocketMessage) => void): void => {
    this.handler = handler;
  };

  unsubscribe = (handler: (msg: WebSocketMessage) => void): void => {
    if (this.handler === handler) this.unsubscribed = true;
  };

  emit(type: string, data: Record<string, unknown>): void {
    this.handler?.({ type, data, timestamp_ms: 0 } as WebSocketMessage);
  }
}

describe('useExpressionSheetWidget', () => {
  const scopes: EffectScope[] = [];

  afterEach(() => {
    scopes.splice(0).forEach(scope => scope.stop());
    vi.restoreAllMocks();
  });

  type WidgetDeps = Parameters<typeof useExpressionSheetWidget>[0];

function run(deps: WidgetDeps) {
    const scope = effectScope();
    scopes.push(scope);
    return scope.run(() => useExpressionSheetWidget(deps))!;
  }

  it('初始情绪为 neutral 且连接后完成订阅', () => {
    const connect = vi.fn();
    const sub = new FakeSubscription();
    const { emotion } = run({ subscribe: sub.subscribe, unsubscribe: sub.unsubscribe, connect });

    expect(emotion.value).toBe(NEUTRAL_EMOTION);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(sub.handler).toBeTypeOf('function');
  });

  it('streamer.speech 事件驱动情绪切换', () => {
    const sub = new FakeSubscription();
    const { emotion } = run({ subscribe: sub.subscribe, unsubscribe: sub.unsubscribe });

    sub.emit('streamer.speech', { emotion: 'happy', emotion_intensity: 0.8 });
    expect(emotion.value).toBe('happy');
  });

  it('非 streamer.speech 事件不切换情绪', () => {
    const sub = new FakeSubscription();
    const { emotion } = run({ subscribe: sub.subscribe, unsubscribe: sub.unsubscribe });

    sub.emit('rundown.changed', { emotion: 'angry' });
    expect(emotion.value).toBe(NEUTRAL_EMOTION);
  });

  it('emotion 缺失或为空时兜底 neutral', () => {
    const sub = new FakeSubscription();
    const { emotion } = run({ subscribe: sub.subscribe, unsubscribe: sub.unsubscribe });

    sub.emit('streamer.speech', { text: '无情绪字段' });
    expect(emotion.value).toBe(NEUTRAL_EMOTION);

    sub.emit('streamer.speech', { emotion: '' });
    expect(emotion.value).toBe(NEUTRAL_EMOTION);
  });

  it('作用域销毁后完成退订', () => {
    const sub = new FakeSubscription();
    const scope = effectScope();
    scopes.push(scope);
    scope.run(() => useExpressionSheetWidget({ subscribe: sub.subscribe, unsubscribe: sub.unsubscribe }));
    expect(sub.unsubscribed).toBe(false);

    scope.stop();
    expect(sub.unsubscribed).toBe(true);
  });
});
