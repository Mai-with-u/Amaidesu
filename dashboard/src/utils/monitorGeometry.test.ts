// monitorGeometry.ts 屏幕区域几何换算与 region 归一化的测试
//
// 重点锁定三类事实：坐标两级换算在多种缩放比下的正确性、边界输入（零尺寸）下的
// 退化行为、region 的 null（全屏）与 [0,0,0,0]（输入占位）双语义归一化表。

import { describe, expect, it } from 'vitest';

import {
  clientToImgCss,
  imgCssToMonitor,
  normalizeRegion,
  regionsEqual,
  type RectLike,
  type ScreenRegion,
} from './monitorGeometry';

const IMG_RECT: RectLike = { left: 100, top: 50, width: 800, height: 400 };
const NATURAL = { width: 1920, height: 1080 };

describe('clientToImgCss', () => {
  it('client 坐标减去图片框偏移得到图内坐标', () => {
    expect(clientToImgCss(100, 50, IMG_RECT)).toEqual({ x: 0, y: 0 });
    expect(clientToImgCss(500, 250, IMG_RECT)).toEqual({ x: 400, y: 200 });
    expect(clientToImgCss(900, 450, IMG_RECT)).toEqual({ x: 800, y: 400 });
  });

  it('越界坐标裁剪到图片框内', () => {
    expect(clientToImgCss(50, 20, IMG_RECT)).toEqual({ x: 0, y: 0 });
    expect(clientToImgCss(1200, 700, IMG_RECT)).toEqual({ x: 800, y: 400 });
  });

  it('容器零尺寸返回 null', () => {
    expect(clientToImgCss(10, 10, { left: 0, top: 0, width: 0, height: 0 })).toBeNull();
    expect(clientToImgCss(10, 10, { left: 0, top: 0, width: -5, height: 100 })).toBeNull();
  });
});

describe('imgCssToMonitor', () => {
  it.each([
    [800, 400],
    [1920, 1080],
    [7680, 4320],
    [100, 100],
  ])('缩放比 %ix%i：CSS 坐标按比例映射到显示器像素', (natW, natH) => {
    const natural = { width: natW, height: natH };
    // 角点恒等：图片框右下角映射到自然尺寸右下角
    expect(imgCssToMonitor(800, 400, IMG_RECT, natural)).toEqual({ x: natW, y: natH });
    // 半程点按比例换算并取整
    expect(imgCssToMonitor(400, 200, IMG_RECT, natural)).toEqual({
      x: Math.round((400 / 800) * natW),
      y: Math.round((200 / 400) * natH),
    });
  });

  it('图片被 CSS 缩放显示时比例换算仍正确', () => {
    // 800 CSS 宽对应 1920 原始宽，比例 2.4
    expect(imgCssToMonitor(400, 200, IMG_RECT, NATURAL)).toEqual({ x: 960, y: 540 });
  });

  it('显示框零尺寸退化为四舍五入', () => {
    const zero: RectLike = { left: 0, top: 0, width: 0, height: 0 };
    expect(imgCssToMonitor(10.4, 20.6, zero, NATURAL)).toEqual({ x: 10, y: 21 });
  });
});

describe('normalizeRegion', () => {
  it.each([
    [[0, 0, 0, 0], null],
    [
      [100, 50, 800, 600],
      [100, 50, 800, 600],
    ],
    [
      [0, 0, 1, 1],
      [0, 0, 1, 1],
    ],
    [
      [1, 0, 0, 0],
      [1, 0, 0, 0],
    ],
    [
      [0, 0, 0, 1],
      [0, 0, 0, 1],
    ],
  ] as Array<[ScreenRegion, ScreenRegion | null]>)('%j → %j', (input, expected) => {
    expect(normalizeRegion(input)).toEqual(expected);
  });

  it('非全零输入返回副本而非原引用', () => {
    const input: ScreenRegion = [1, 2, 3, 4];
    const result = normalizeRegion(input);
    expect(result).not.toBe(input);
    expect(result).toEqual([1, 2, 3, 4]);
  });
});

describe('regionsEqual', () => {
  it.each([
    [null, null, true],
    [[0, 0, 0, 0], [0, 0, 0, 0], true],
    [[1, 2, 3, 4], [1, 2, 3, 4], true],
    [null, [0, 0, 0, 0], false],
    [[1, 2, 3, 4], [1, 2, 3, 5], false],
    [[1, 2, 3, 4], [4, 3, 2, 1], false],
  ] as Array<[ScreenRegion | null, ScreenRegion | null, boolean]>)(
    '%j vs %j → %j',
    (a, b, expected) => {
      expect(regionsEqual(a, b)).toBe(expected);
    },
  );
});
