// apiError.ts 的测试
//
// 覆盖：axios {detail} 字符串穿透、FastAPI 422 的 detail 数组取首项 msg、
// detail 异常形态回落、Error.message、非 Error 值与空消息兜底、
// HTTP 状态码提取（供 409 这类按状态分支的调用方使用）。

import { describe, expect, it } from 'vitest';

import { getApiErrorMessage, getApiErrorStatus } from './apiError';

function axiosLike(detail: unknown, status = 400): unknown {
  return { response: { status, data: { detail } } };
}

describe('getApiErrorMessage', () => {
  it('axios 错误穿透后端中文 detail', () => {
    expect(getApiErrorMessage(axiosLike('目标拒收'), '兜底')).toBe('目标拒收');
  });

  it('FastAPI 422 的 detail 数组取首项 msg 作为文案', () => {
    const detail = [
      { loc: ['body', 'what'], msg: '标题不能为空', type: 'value_error' },
      { loc: ['body', 'quote'], msg: '原话太长', type: 'value_error' },
    ];
    expect(getApiErrorMessage(axiosLike(detail, 422), '兜底')).toBe('标题不能为空');
  });

  it('detail 数组跳过无 msg 的项，取第一条可用文案', () => {
    const detail = [{ loc: ['body'] }, '纯文本错误', { msg: '后面的不取' }];
    expect(getApiErrorMessage(axiosLike(detail, 422), '兜底')).toBe('纯文本错误');
    expect(getApiErrorMessage(axiosLike([{ loc: ['body'] }], 422), '兜底')).toBe('兜底');
  });

  it('detail 非字符串/数组或为空时回落', () => {
    expect(getApiErrorMessage(axiosLike({ errors: [] }), '兜底')).toBe('兜底');
    expect(getApiErrorMessage(axiosLike(''), '兜底')).toBe('兜底');
    expect(getApiErrorMessage(axiosLike([]), '兜底')).toBe('兜底');
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

describe('getApiErrorStatus', () => {
  it('取 axios 错误的后端状态码', () => {
    expect(getApiErrorStatus(axiosLike('冲突', 409))).toBe(409);
    expect(getApiErrorStatus(axiosLike('校验失败', 422))).toBe(422);
  });

  it('没有 response 或 response 状态非数字时返回 null', () => {
    expect(getApiErrorStatus(new Error('network down'))).toBeNull();
    expect(getApiErrorStatus({ response: {} })).toBeNull();
    expect(getApiErrorStatus('字符串错误')).toBeNull();
    expect(getApiErrorStatus(null)).toBeNull();
  });
});
