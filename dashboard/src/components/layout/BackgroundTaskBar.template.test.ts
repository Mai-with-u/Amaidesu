// BackgroundTaskBar.vue 挂载关系的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件
// 组件），按既有惯例（Sidebar.template.test.ts）降级为源码结构断言。
//
// 守护的语义：后台任务底栏是全局常驻状态条——挂在 AppLayout 的
// el-container（header / body 之后），任何页面都能看到在跑的后台任务；
// 误删挂载点会让任务在跑而界面零显示（本功能的原始痛点）。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(
  fileURLToPath(new URL('./BackgroundTaskBar.vue', import.meta.url)),
  'utf-8',
);
const appLayoutSource = readFileSync(
  fileURLToPath(new URL('./AppLayout.vue', import.meta.url)),
  'utf-8',
);

describe('后台任务底栏挂载关系', () => {
  it('AppLayout 挂了 BackgroundTaskBar 并完成导入', () => {
    expect(appLayoutSource).toContain('<BackgroundTaskBar />');
    expect(appLayoutSource).toContain("import BackgroundTaskBar from './BackgroundTaskBar.vue'");
  });

  it('底栏在主内容容器之后、整体布局容器收尾之前', () => {
    const bodyEnd = appLayoutSource.indexOf('</el-container>');
    const barTag = appLayoutSource.indexOf('<BackgroundTaskBar />');
    const layoutEnd = appLayoutSource.lastIndexOf('</el-container>');
    expect(bodyEnd).toBeGreaterThan(-1);
    expect(barTag).toBeGreaterThan(bodyEnd);
    expect(layoutEnd).toBeGreaterThan(barTag);
  });

  it('组件用 el-footer 常驻、经 el-popover 点击弹任务清单', () => {
    expect(sfcSource).toContain('<el-footer');
    expect(sfcSource).toContain('<el-popover');
    expect(sfcSource).toContain('trigger="click"');
  });

  it('数据面在 useBackgroundTasks（组件不做订阅细节）', () => {
    expect(sfcSource).toContain('useBackgroundTasks');
    expect(sfcSource).not.toMatch(/subscribe\(/);
  });
});

describe('整理任务的模型调用入口', () => {
  it('任务行给出跳转模型调用历史的链接', () => {
    expect(sfcSource).toContain('to="/llm/history?profile=settle"');
    expect(sfcSource).toContain('查看模型调用');
  });

  it('只给下播整理的任务行这个链接（按任务号前缀判断）', () => {
    expect(sfcSource).toContain('isSettleTask');
    expect(sfcSource).toContain("startsWith('settle-')");
    expect(sfcSource).toContain('v-if="isSettleTask(task)"');
  });
});

describe('模型调用的实时输出预览', () => {
  it('模型调用期间任务行下渲染输出尾巴预览', () => {
    expect(sfcSource).toContain('v-else-if="task.output_tail"');
    expect(sfcSource).toContain('正在写：');
    expect(sfcSource).toContain('task.output_tail');
  });
});

describe('并行子步骤的堆叠进度行', () => {
  it('带 progress_lines 的任务在任务行下堆叠子步骤行（每行一步）', () => {
    expect(sfcSource).toContain('v-if="task.progress_lines?.length"');
    expect(sfcSource).toContain('v-for="line in task.progress_lines"');
    expect(sfcSource).toContain('line.label');
    expect(sfcSource).toContain('line.progress_text');
  });

  it('子步骤行各带自己的输出尾巴预览（与单步骤预览互斥，不重复渲染）', () => {
    expect(sfcSource).toContain('v-if="line.output_tail"');
    expect(sfcSource).toContain('line.output_tail');
    // 单步骤预览降级为 v-else-if：同一任务行不会两种形态同时出现
    expect(sfcSource).toContain('v-else-if="task.output_tail"');
  });
});
