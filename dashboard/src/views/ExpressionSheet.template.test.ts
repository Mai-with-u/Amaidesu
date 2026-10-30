// ExpressionSheet.vue 取图语义的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件组件），
// 按既有惯例降级为源码结构断言，守护取图语义的关键不变量：显示哪张图由后端
// 决定（跟着面捕合成器的主表情）、404 才算未配置、网络抖动保持当前图、
// 轮询随作用域启停、加载失败的图不反复重试。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(
  fileURLToPath(new URL('./ExpressionSheet.vue', import.meta.url)),
  'utf-8',
);

describe('ExpressionSheet 取图语义', () => {
  it('图地址取后端解析好的结果（缺图回退与扩展名由后端按目录扫描决定）', () => {
    expect(sfcSource).toContain('await avatarApi.getExpressionSheetState();');
    expect(sfcSource).toContain('currentSrc.value = data.image_url;');
    expect(sfcSource).not.toContain('streamer.speech');
  });

  it('只有 404 算未配置：主程序重启、网络抖动时保持当前图，不闪成透明', () => {
    expect(sfcSource).toContain("if (status === 404) displayState.value = 'unconfigured';");
  });

  it('setup 即轮询、作用域销毁时停止（嵌入页不在 keep-alive 里，泄漏定时器会随重挂载累积）', () => {
    expect(sfcSource).toContain(
      'useKeepAlivePolling(refresh, POLL_INTERVAL_MS, { startOnSetup: true, stopOnDispose: true });',
    );
    expect(sfcSource).toContain('if (inFlight) return;');
  });

  it('加载失败过的图不再反复加载（否则每拍闪一次）', () => {
    expect(sfcSource).toContain('data.image_url !== brokenSrc');
    expect(sfcSource).toContain('brokenSrc = layer.src;');
  });
});
