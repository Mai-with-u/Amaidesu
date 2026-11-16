// App.vue 全局浮层与 OBS 嵌入页隔离测试
//
// 回归背景：断线续播弹窗曾无条件挂在 App.vue 全局层，而字幕/弹幕等 OBS
// 嵌入页与主界面是同一个 SPA 入口——弹窗跟着渲染进浏览器源。嵌入页里
// 没人能点按钮、也不感知主界面已处置完，弹窗就永久糊在直播画面上。
//
// 守护的语义：嵌入页（layout=false 路由）只渲染小部件本体，任何全局
// 浮层（弹窗/命令面板类）挂进 App.vue 时必须带上嵌入页门控。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const appSource = readFileSync(fileURLToPath(new URL('./App.vue', import.meta.url)), 'utf-8');
const routerSource = readFileSync(
  fileURLToPath(new URL('./router/index.ts', import.meta.url)),
  'utf-8',
);

describe('App.vue 全局浮层的嵌入页隔离', () => {
  it('续播弹窗渲染受嵌入页标记门控', () => {
    expect(appSource).toContain('<ResidualSessionDialog v-if="!isEmbedPage"');
  });

  it('嵌入页判定与布局分支用同一个路由标记，改标记两边同步', () => {
    // AppLayout 的分支（meta.layout !== false）与弹窗门控必须同源，
    // 否则会出现"无壳页面却弹全局弹窗"的错层
    expect(appSource).toContain('route.meta.layout === false');
    expect(appSource).toContain('route.meta.layout !== false');
  });

  it('layout=false 标记只用于 /widget/ 嵌入路由，壳内页面不受门控影响', () => {
    const layoutFalseBlocks = [...routerSource.matchAll(/meta:\s*\{\s*layout:\s*false\s*\}/g)];
    expect(layoutFalseBlocks.length, '路由表中应存在嵌入页标记').toBeGreaterThan(0);
    // 每处 layout=false 的宿主路由 path 都必须是 /widget/ 前缀（OBS 嵌入页约定）
    for (const match of layoutFalseBlocks) {
      const before = routerSource.slice(0, match.index);
      const pathMatch = [...before.matchAll(/path:\s*'([^']+)'/g)].pop();
      expect(pathMatch, 'layout=false 路由缺少 path 声明').not.toBeUndefined();
      expect(pathMatch![1].startsWith('/widget/')).toBe(true);
    }
  });
});
