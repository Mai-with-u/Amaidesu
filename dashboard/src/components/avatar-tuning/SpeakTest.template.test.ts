// SpeakTest.vue 口型试播语义的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件
// 组件），按既有惯例降级为源码结构断言，守护与调试页试说同链路的关键不变量。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(fileURLToPath(new URL('./SpeakTest.vue', import.meta.url)), 'utf-8');

describe('SpeakTest 口型试播语义', () => {
  it('复用调试页试说端点，走真实扬声器链路（play_on_device: true）', () => {
    expect(sfcSource).toContain('debugApi.testTts({ text: content, play_on_device: true })');
  });

  it('TTS 装配状态在挂载与激活时各查一次（keep-alive 回来重查装配）', () => {
    expect(sfcSource).toContain('onMounted(() => void loadStatus());');
    expect(sfcSource).toContain('onActivated(() => void loadStatus());');
  });

  it('未装配时输入与按钮禁用', () => {
    expect(sfcSource).toContain(':disabled="!ttsAvailable"');
  });

  it('错误文案统一走 getApiErrorMessage（前端约定）', () => {
    expect(sfcSource).toContain("getApiErrorMessage(error, '试说失败')");
  });
});
