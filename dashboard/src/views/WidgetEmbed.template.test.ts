// 弹幕/字幕/2048 三个嵌入页预览态与握手上限的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件组件），
// 按既有惯例降级为源码结构断言，守护关键不变量：max_messages 以 style 消息校验后
// 覆盖缓冲上限；占位与停用横幅只出现在预览态；停用读取失败不显示横幅。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

function sfc(name: string): string {
  return readFileSync(fileURLToPath(new URL(`./${name}`, import.meta.url)), 'utf-8');
}

describe('Danmaku 握手上限打通', () => {
  const source = sfc('Danmaku.vue');

  it('缓冲上限是响应式变量，style 消息校验通过才覆盖', () => {
    expect(source).toContain('const maxMessages = ref<number>(15);');
    expect(source).toContain('data.max_messages >= 1');
    expect(source).toContain('maxMessages.value = data.max_messages;');
    expect(source).toContain('messages.value.length > maxMessages.value');
  });

  it('占位与停用横幅只在预览态渲染', () => {
    expect(source).toContain('isPreviewMode()');
    expect(source).toContain('v-if="isPreview && widgetDisabled"');
    expect(source).toContain('v-else-if="isPreview && messages.length === 0"');
  });

  it('停用判定读 dashboard 段 danmaku_widget.enabled，读取失败不显示横幅', () => {
    expect(source).toContain("cfg['dashboard']");
    expect(source).toContain("['danmaku_widget']");
    expect(source).toContain('widgetDisabled.value = enabled === false;');
  });
});

describe('嵌入页预览态占位', () => {
  it('字幕页占位以"收到过字幕"撤下，停用只改展示不改数据流', () => {
    const source = sfc('Subtitle.vue');
    expect(source).toContain('v-else-if="isPreview && !everReceivedSubtitle"');
    expect(source).toContain('everReceivedSubtitle.value = true;');
    expect(source).toContain("['subtitle_widget']");
  });

  it('2048 页占位文案对齐"等待棋盘状态"语义，以"收到过快照"撤下', () => {
    const source = sfc('Game2048.vue');
    expect(source).toContain('等待棋盘状态');
    expect(source).toContain('v-else-if="isPreview && !everReceivedState"');
    expect(source).toContain('everReceivedState.value = true;');
    expect(source).toContain("['game2048_widget']");
  });
});
