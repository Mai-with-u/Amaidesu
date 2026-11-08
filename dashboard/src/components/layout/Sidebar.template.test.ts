// Sidebar.vue 导航信息架构的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件
// 组件），按既有惯例降级为源码结构断言。
//
// 守护的语义：侧边栏的分组与条目顺序是运营者按「现在该做什么」找页面的
// 依据——直播主循环（运行总览 / 直播控制台 / 流程单工作台）必须留在首位且
// 不被系统类页面隔断；组件启停、运行记录、调试与集成类页面按使用时机沉到
// 后面。误排会让高频页面掉进列表深处，运营者只能靠滚动找页面。
//
// 分组语义：直播中 = 直播现场的操作台；档案与复盘 = 积累下来的记录（场次
// 复盘 / 记忆账本 / 观众名单）；系统组件 = 组件注册与启停；运行记录 = 系统
// 跑出来的痕迹；调试 = 把自己系统调到预期状态的手段（基础设施直测、形象
// 链路排查与调参、注入模拟观众）；外部集成 = 对外输出页（OBS 嵌入页）。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(fileURLToPath(new URL('./Sidebar.vue', import.meta.url)), 'utf-8');
const routerSource = readFileSync(
  fileURLToPath(new URL('../../router/index.ts', import.meta.url)),
  'utf-8',
);

/** 按模板出现顺序取「分组小标题 / 菜单条目路由」序列 */
const navTokens = [
  ...sfcSource.matchAll(/<el-menu-item-group title="([^"]+)"|<el-menu-item index="([^"]+)"/g),
].map(match => match[1] ?? match[2]);

/** 菜单条目的（路由, 显示名），顺序同模板 */
const menuEntries = [
  ...sfcSource.matchAll(/<el-menu-item index="([^"]+)">([\s\S]*?)<\/el-menu-item>/g),
].map(match => ({
  path: match[1],
  label: /<span>([^<]+)<\/span>/.exec(match[2])?.[1] ?? '',
}));

/** 按分组小标题切分导航条目：小标题开启一组，条目归入当前组 */
function groupEntries(): Map<string, string[]> {
  const groups = new Map<string, string[]>();
  let current: string[] | null = null;
  for (const token of navTokens) {
    if (token.startsWith('/')) {
      current?.push(token);
      continue;
    }
    current = [];
    groups.set(token, current);
  }
  return groups;
}

describe('侧边栏导航信息架构', () => {
  it('分组顺序按直播工作流排布', () => {
    expect([...groupEntries().keys()]).toEqual([
      '直播中',
      '档案与复盘',
      '系统组件',
      '运行记录',
      '调试',
      '外部集成',
      '设置',
    ]);
  });

  it('直播主循环条目连续排在首位，观众归档案侧', () => {
    expect(groupEntries().get('直播中')).toEqual(['/', '/live', '/outline']);
    expect(groupEntries().get('档案与复盘')).toEqual(['/review', '/clips', '/memory', '/viewers']);
  });

  it('调试组收齐把自己系统调到预期状态的手段', () => {
    expect(groupEntries().get('调试')).toEqual([
      '/debug',
      '/avatar-debug',
      '/avatar-tuning',
      '/simulator',
    ]);
    expect(groupEntries().get('外部集成')).toEqual(['/widgets']);
  });

  it('条目路由与显示名按既定顺序成对出现', () => {
    expect(menuEntries).toEqual([
      { path: '/', label: '运行总览' },
      { path: '/live', label: '直播控制台' },
      { path: '/outline', label: '流程单工作台' },
      { path: '/review', label: '场次复盘' },
      { path: '/clips', label: '直播切片' },
      { path: '/memory', label: '记忆账本' },
      { path: '/viewers', label: '观众' },
      { path: '/collectors', label: '采集器' },
      { path: '/agents', label: 'Agent' },
      { path: '/tools', label: '工具' },
      { path: '/eventlog', label: '事件流' },
      { path: '/logs', label: '日志' },
      { path: '/llm/usage', label: 'LLM 用量' },
      { path: '/llm/history', label: 'LLM 历史' },
      { path: '/debug', label: '调试' },
      { path: '/avatar-debug', label: '形象调试' },
      { path: '/avatar-tuning', label: '形象调校' },
      { path: '/simulator', label: '观众模拟器' },
      { path: '/widgets', label: '小部件' },
      { path: '/settings', label: '设置' },
    ]);
  });

  it('菜单入口都有对应路由，不留悬空入口', () => {
    for (const entry of menuEntries) {
      expect(routerSource, `侧边栏入口 ${entry.path} 在路由表里不存在`).toContain(
        `path: '${entry.path}'`,
      );
    }
  });

  it('壳内页面都有侧边栏入口，新增页面漏挂会被此用例拦下', () => {
    const routerPaths = [...routerSource.matchAll(/path:\s*'([^']+)'/g)].map(match => match[1]);
    // 嵌页（/widget/*）是给 OBS 用的独立页面，不进壳内导航；
    // 观众子页（洞察 / 待处理 / 详情）由观众列表页进入，同样不是独立入口
    const shellPaths = routerPaths.filter(
      path =>
        !path.startsWith('/widget/') && (path === '/viewers' || !path.startsWith('/viewers/')),
    );
    expect([...shellPaths].sort()).toEqual(menuEntries.map(entry => entry.path).sort());
  });
});
