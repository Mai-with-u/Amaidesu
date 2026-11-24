// Clips.vue 段卡片操作排布与核对自动前进的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件
// 组件），按既有惯例（SegmentTimeline.template.test.ts）降级为源码结构断言。
//
// 守护的语义：核对阶段的扫描效率——操作按钮收成卡片底部横排（纵向按钮列不再
// 撑高卡片），低频操作（看产物/删除）收进"更多"菜单；确认/弃用后自动跳到
// 下一个待核对段并预览，省掉逐段手动定位。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(fileURLToPath(new URL('./Clips.vue', import.meta.url)), 'utf-8');

describe('段卡片操作排布', () => {
  it('操作区是卡片底部横排，不再是纵向按钮列', () => {
    const css = sfcSource.slice(sfcSource.indexOf('<style scoped>'));
    expect(css).toMatch(/\.clip-segment-row \{[\s\S]*?flex-direction: column/);
    expect(css).toMatch(/\.clip-segment-actions \{[\s\S]*?align-items: center/);
    expect(css).not.toMatch(/\.clip-segment-actions \{[\s\S]*?flex-direction: column/);
  });

  it('主操作（预览/确认/切这段/弃用）仍是独立按钮，弃用为 text 型', () => {
    expect(sfcSource).toContain('@click="onPreviewSegment(segment)"');
    expect(sfcSource).toContain('@click="onConfirmSegment(segment)"');
    expect(sfcSource).toContain('@click="onCutOne(segment)"');
    expect(sfcSource).toMatch(
      /v-if="segment\.status !== 'dismissed'"[\s\S]*?text[\s\S]*?@click="onDismissSegment\(segment\)"/,
    );
  });

  it('看产物与删除收进"更多"下拉菜单，删除走 command 分发', () => {
    expect(sfcSource).toContain(
      '@command="(command: string) => onSegmentMoreCommand(segment, command)"',
    );
    expect(sfcSource).toMatch(/v-if="outputsOf\(segment\)\.length" command="output"/);
    expect(sfcSource).toContain('command="delete"');
    expect(sfcSource).toMatch(/function onSegmentMoreCommand[\s\S]*?'output'[\s\S]*?'delete'/);
    // 删除按钮本体不再直接出现在操作行（只能经菜单触发）
    expect(sfcSource).not.toContain('@click="onDeleteSegment(segment)"');
  });

  it('标题/时间输入平时无边框，hover 或 focus 才显边框', () => {
    const css = sfcSource.slice(sfcSource.indexOf('.clip-title-input :deep(.el-input__wrapper)'));
    expect(css).toMatch(/box-shadow: none/);
    expect(css).toMatch(/\.clip-title-input:hover :deep\(\.el-input__wrapper\)/);
    expect(css).toMatch(/\.el-input__wrapper\.is-focus/);
  });
});

describe('核对工作流与播放器接线', () => {
  it('核对逻辑收拢在 useClipReview，预览联动接到页面播放器入口', () => {
    expect(sfcSource).toContain('useClipReview({');
    expect(sfcSource).toContain('previewSegment: segment => onPreviewSegment(segment)');
    // 页面不再内联核对逻辑（草稿/前进已搬走，行为由 useClipReview.test.ts 锁定）
    expect(sfcSource).not.toContain('function advanceToNextReviewable');
    expect(sfcSource).not.toContain('interface RowDraft');
  });

  it('播放器交互收拢在 ClipPreviewPlayer，页面只接播放头/就绪/关闭', () => {
    expect(sfcSource).toContain('<ClipPreviewPlayer');
    expect(sfcSource).toContain('@playhead="onPlayhead"');
    expect(sfcSource).toContain('@ready="onPreviewReady"');
    expect(sfcSource).toContain('@close="closePreview"');
    // <video> 本体已不在页面（事件绑定随之消失）
    expect(sfcSource).not.toContain('onloadedmetadata');
  });

  it('时间轴的调界/建段事件接到 useClipReview 的同名处理函数', () => {
    expect(sfcSource).toContain('@resize="onResize"');
    expect(sfcSource).toContain('@create="onCreate"');
    expect(sfcSource).toContain('@seek="seekPreviewTo"');
  });

  it('点行即预览（行点击与时间轴点块都走 selectSegment），入出点标记接到播放器事件', () => {
    expect(sfcSource).toContain('@click="selectSegment(segment.id)"');
    expect(sfcSource).toContain('@select="selectSegment($event)"');
    expect(sfcSource).toContain('@mark-start="wall => markSegmentStart(wall)"');
    expect(sfcSource).toContain('@mark-end="wall => markSegmentEnd(wall)"');
    // 核对键盘：Enter 确认 / X 弃用在页面层监听
    expect(sfcSource).toMatch(/function onReviewKeydown[\s\S]*?Enter[\s\S]*?onConfirmSegment/);
    expect(sfcSource).toMatch(/function onReviewKeydown[\s\S]*?'x'[\s\S]*?onDismissSegment/);
  });
});

describe('播放器空态与页头细节', () => {
  it('空态改为概览 + 快捷动作，保留固定播放器说明', () => {
    expect(sfcSource).toContain('class="clip-player-empty-stat"');
    expect(sfcSource).toContain('@click="onPreviewSegment(segments[0])"');
    expect(sfcSource).toContain('播放器固定在这里，调段不用来回滚');
    expect(sfcSource).not.toContain('点右侧段行的「预览」核对切点');
  });

  it('页头"已出片"chip 仅在有已出片时显示，刷新按钮为 text 型', () => {
    expect(sfcSource).toContain('v-if="doneCount" class="clip-meta-chip is-done"');
    expect(sfcSource).toContain('text :loading="detailLoading" @click="openSessionSegments"');
  });
});
