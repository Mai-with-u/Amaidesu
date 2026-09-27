// apiError.ts API 错误消息提取的测试
//
// 覆盖：axios {detail} 穿透、detail 异常形态回落、Error.message、
// 非 Error 值与空消息兜底。

import { describe, expect, it } from 'vitest';

import { getApiErrorMessage } from './apiError';

function axiosLike(detail: unknown): unknown {
  return { response: { data: { detail } } };
}

describe('getApiErrorMessage', () => {
  it('axios 错误穿透后端中文 detail', () => {
    expect(getApiErrorMessage(axiosLike('目标拒收'), '兜底')).toBe('目标拒收');
  });

  it('detail 非字符串或为空串时回落', () => {
    expect(getApiErrorMessage(axiosLike({ errors: [] }), '兜底')).toBe('兜底');
    expect(getApiErrorMessage(axiosLike(''), '兜底')).toBe('兜底');
    expect(getApiErrorMessage(axiosLike(undefined), '兜底')).toBe('兜底');
  });

  it('非 axios 错误取 Error.message', () => {
    expect(getApiErrorMessage(new Error('network down'), '兜底')).toBe('network down');
  });

  it('空 Error.message 与非 Error 值返回 fallback', () => {
    expect(getApiErrorMessage(new Error(''), '兜底')).toBe('兜底');
    expect(getApiErrorMessage('字符串错误', '兜底')).toBe('兜底');
    expect(getApiErrorMessage(null, '兜底')).toBe('兜底');
    expect(getApiErrorMessage(undefined, '兜底')).toBe('兜底');
  });
});
