// SegmentTimeline.vue 交互语义的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件
// 组件），按既有惯例（Sidebar.template.test.ts）降级为源码结构断言。
//
// 守护的语义：时间轴必须自解释——单击跳转、按住拖动建段、段块选中跳段起点、
// 播放头实时显示、调边把手常显。历史教训：双击建段只靠标题旁一行灰色小字提示，
// 用户完全发现不了（等于功能不存在）；这里同时锁死"双击建段不得回归"。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(
  fileURLToPath(new URL('./SegmentTimeline.vue', import.meta.url)),
  'utf-8',
);
const clipsSource = readFileSync(
  fileURLToPath(new URL('../../views/Clips.vue', import.meta.url)),
  'utf-8',
);

describe('切片段时间轴交互', () => {
  it('双击建段已移除，不回归', () => {
    expect(sfcSource).not.toContain('@dblclick');
    expect(sfcSource).not.toContain('双击');
  });

  it('空白按下拖动建段：track 接 pointerdown，框选后抛出 create', () => {
    expect(sfcSource).toContain('@pointerdown="onTrackPointerDown"');
    expect(sfcSource).toContain("emit('create'");
    // 拖动距离阈值与最短建段时长都须存在（防止误触建段/零长段）
    expect(sfcSource).toContain('DRAG_THRESHOLD_PX');
    expect(sfcSource).toContain('MIN_CREATE_SPAN_MS');
  });

  it('单击空白跳转：抛出 seek，Clips.vue 接到播放器定位', () => {
    expect(sfcSource).toContain("(e: 'seek', startMs: number): void;");
    expect(clipsSource).toContain('@seek="seekPreviewTo"');
    expect(clipsSource).toContain('function seekPreviewTo');
  });

  it('播放头：组件接收 playheadMs 并渲染，Clips.vue 传当前播放位置', () => {
    expect(sfcSource).toContain('playheadMs: number | null;');
    expect(sfcSource).toContain('class="clip-playhead"');
    expect(clipsSource).toContain(':playhead-ms="playheadMs"');
    expect(clipsSource).toContain(
      'playheadMs.value = file.started_at_ms + player.currentTime * 1000;',
    );
  });

  it('段块边缘把手常显，不再悬停才出现', () => {
    const edgeCss = sfcSource.slice(sfcSource.indexOf('.clip-block-edge {'));
    expect(edgeCss).toMatch(/\.clip-block-edge \{[\s\S]*?opacity: 0\.45/);
  });

  it('点段块选中并跳段起点：Clips.vue 用 onSelectSegment 同时做两件事', () => {
    expect(clipsSource).toContain('@select="onSelectSegment"');
    expect(clipsSource).toMatch(
      /function onSelectSegment[\s\S]*?seekPreviewTo\(segment\.start_ms\)/,
    );
  });

  it('时间轴建段仍接到后端建段接口', () => {
    expect(clipsSource).toContain('@create="onCreate"');
    expect(clipsSource).toContain('createSegment(selectedSession.value.live_session_id');
  });

  it('段列表滚动定位：段行收集 ref，选中变化时 scrollIntoView', () => {
    expect(clipsSource).toContain('setSegmentRowEl(segment.id, el)');
    expect(clipsSource).toContain("scrollIntoView({ block: 'nearest' })");
  });
});
