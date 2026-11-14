// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { renderMarkdown } from './markdown';

describe('renderMarkdown', () => {
  it('空输入返回空串，不产出空 <p>', () => {
    expect(renderMarkdown('')).toBe('');
    expect(renderMarkdown('   \n  ')).toBe('');
  });

  it('渲染基础 Markdown 结构', () => {
    const html = renderMarkdown('# 标题\n\n- 甲\n- 乙');
    expect(html).toContain('<h1');
    expect(html).toContain('<ul>');
    expect(html).toContain('<li>甲</li>');
  });

  it('脚本与事件属性被 DOMPurify 净化掉', () => {
    expect(renderMarkdown('<script>alert(1)</script>正文')).not.toContain('script');
    expect(renderMarkdown('<img src=x onerror=alert(1)>')).not.toContain('onerror');
  });
});
