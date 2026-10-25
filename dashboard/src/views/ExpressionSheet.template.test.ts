// ExpressionSheet.vue 事件订阅语义的源码结构守护测试
//
// 消费的事件订阅逻辑原为 useExpressionSheetWidget 组合式（有行为级测试），
// 因单消费者就地内联进本页，而项目无组件挂载测试环境（无 jsdom 环境下的
// vue 插件，无法编译 .vue 单文件组件），按既有惯例降级为源码结构断言，
// 守护订阅语义的关键不变量：事件类型过滤、非法 emotion 兜底 neutral、
// setup 订阅 + 作用域销毁退订。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(
  fileURLToPath(new URL('./ExpressionSheet.vue', import.meta.url)),
  'utf-8',
);

describe('ExpressionSheet 事件订阅语义', () => {
  it('只消费 streamer.speech 事件（类型不过滤会把无关事件误当情绪信号）', () => {
    expect(sfcSource).toContain('msg.type !== STREAMER_SPEECH_TYPE) return;');
    expect(sfcSource).toContain("const STREAMER_SPEECH_TYPE = 'streamer.speech';");
  });

  it('非法/缺失 emotion 兜底 neutral（生产者契约之外的数据不得直接上屏）', () => {
    expect(sfcSource).toContain(
      "emotion.value = typeof raw === 'string' && raw ? raw : NEUTRAL_EMOTION;",
    );
  });

  it('setup 订阅 + 作用域销毁退订（嵌入页长驻，泄漏订阅会随重挂载累积）', () => {
    expect(sfcSource).toContain('wsStore.subscribe(onSpeechMessage);');
    expect(sfcSource).toContain('onScopeDispose(() => wsStore.unsubscribe(onSpeechMessage));');
    expect(sfcSource).toContain('wsStore.connect();');
  });

  it('兜底情绪名对应后端 NEUTRAL 枚举值', () => {
    expect(sfcSource).toContain("const NEUTRAL_EMOTION = 'neutral';");
  });
});
