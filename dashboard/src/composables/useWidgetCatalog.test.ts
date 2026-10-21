import { describe, expect, it } from 'vitest';

import { buildWidgetUrl, useWidgetCatalog } from './useWidgetCatalog';

describe('useWidgetCatalog', () => {
  const { widgets } = useWidgetCatalog();

  it('目录含弹幕/字幕/差分立绘/2048 四个条目', () => {
    expect(widgets.map(w => w.key)).toEqual([
      'danmaku',
      'subtitle',
      'expression_sheet',
      'game2048',
    ]);
  });

  it('嵌入页路径唯一且统一挂在 /widget/ 前缀下', () => {
    const paths = widgets.map(w => w.path);
    expect(new Set(paths).size).toBe(paths.length);
    for (const path of paths) {
      expect(path.startsWith('/widget/')).toBe(true);
    }
  });

  it('每个条目的名称与描述非空', () => {
    for (const widget of widgets) {
      expect(widget.name.trim()).not.toBe('');
      expect(widget.description.trim()).not.toBe('');
      expect(widget.recommendedSize.trim()).not.toBe('');
    }
  });

  it('嵌入态网址不含查询参数，预览态追加 preview=1', () => {
    expect(buildWidgetUrl('http://localhost:60315', '/widget/danmaku')).toBe(
      'http://localhost:60315/widget/danmaku',
    );
    expect(buildWidgetUrl('http://localhost:60315', '/widget/danmaku', { preview: true })).toBe(
      'http://localhost:60315/widget/danmaku?preview=1',
    );
  });
});
