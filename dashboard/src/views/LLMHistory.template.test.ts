// LLMHistory.vue 查询参数直达筛选的源码结构守护测试
//
// 项目无组件挂载测试环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件
// 组件），按既有惯例（BackgroundTaskBar.template.test.ts）降级为源码结构断言。
//
// 守护的语义：外部页面（LLM 用量表、场次复盘等）经 URL 查询参数跳入本页时，
// 每个支持的参数都必须在 setup 期写入 queryParams（首拉之前），漏写会导致
// 带参跳入后筛选不生效——本文件守护各参数的读取分支都在且写在首拉调用之前。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(
  fileURLToPath(new URL('./LLMHistory.vue', import.meta.url)),
  'utf-8',
);

describe('LLMHistory 直达筛选参数', () => {
  it('model_name / profile / start_time / end_time 四个查询参数都有读取分支', () => {
    expect(sfcSource).toContain('route.query.model_name');
    expect(sfcSource).toContain('route.query.profile');
    expect(sfcSource).toContain('route.query.start_time');
    expect(sfcSource).toContain('route.query.end_time');
    expect(sfcSource).toContain('queryParams.profile_name = queryProfile');
  });

  it('直达筛选写入发生在首拉（fetchHistory 调用）之前', () => {
    const profileRead = sfcSource.indexOf('route.query.profile');
    const onMountedFetch = sfcSource.indexOf('fetchHistory()');
    expect(profileRead).toBeGreaterThan(-1);
    expect(onMountedFetch).toBeGreaterThan(profileRead);
  });
});
