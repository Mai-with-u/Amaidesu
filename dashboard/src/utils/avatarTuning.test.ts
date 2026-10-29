// avatarTuning.ts 形象调校页纯逻辑的测试
//
// 锁定四类事实：字段分组不丢字段（未登记的落进"其他"、眼神方向字段交给方框）、
// 眼神方框坐标与视线坐标互逆且越界裁剪、配方能加哪些键、拖动限流落在最后一个值上。

import { describe, expect, it, vi } from 'vitest';

import type { AvatarTuningField } from '@/types';

import {
  addableRecipeKeys,
  channelLabel,
  gazeToPercent,
  groupTuningFields,
  pointerToGaze,
  recipeSliderStep,
  throttleLatest,
} from './avatarTuning';

function field(key: string): AvatarTuningField {
  return {
    key,
    value: 0,
    title: key,
    description: '',
    type: 'number',
    min: null,
    max: null,
    advanced: false,
  };
}

describe('groupTuningFields', () => {
  it('按页面分组排好，眼神方向字段不重复出现，未登记字段收进"其他"', () => {
    const groups = groupTuningFields(
      ['rest_smile', 'chat_gaze_x', 'head_follow_deg', 'mood_gain', 'brand_new_field'].map(field),
    );
    expect(groups.map(g => g.title)).toEqual(['人设', '眼神', '心情', '其他']);
    expect(groups.flatMap(g => g.fields.map(f => f.key))).not.toContain('chat_gaze_x');
    expect(groups.at(-1)?.fields.map(f => f.key)).toEqual(['brand_new_field']);
  });
});

describe('眼神方框坐标', () => {
  const rect = { left: 100, top: 100, width: 200, height: 200 };

  it('视线坐标按观众视角画：右上为正', () => {
    expect(gazeToPercent(0, 0)).toEqual({ left: 50, top: 50 });
    expect(gazeToPercent(1, 1)).toEqual({ left: 100, top: 0 });
    expect(gazeToPercent(-5, -5)).toEqual({ left: 0, top: 100 });
  });

  it('鼠标位置换回视线坐标，与上面互逆，越界裁到 ±1', () => {
    expect(pointerToGaze(200, 200, rect)).toEqual({ x: 0, y: 0 });
    expect(pointerToGaze(300, 100, rect)).toEqual({ x: 1, y: 1 });
    expect(pointerToGaze(150, 250, rect)).toEqual({ x: -0.5, y: -0.5 });
    expect(pointerToGaze(0, 999, rect)).toEqual({ x: -1, y: -1 });
    expect(pointerToGaze(0, 0, { ...rect, width: 0 })).toBeNull();
  });
});

describe('配方编辑', () => {
  it('只列还没加的键', () => {
    expect(addableRecipeKeys(['brow', 'eye_open', 'mouth_smile'], { brow: 0.3 })).toEqual([
      'eye_open',
      'mouth_smile',
    ]);
  });

  it('角度类给整数步长，未知键名原样显示', () => {
    expect(recipeSliderStep(40)).toBe(1);
    expect(recipeSliderStep(2)).toBe(0.05);
    expect(channelLabel('mouth_smile')).toBe('嘴角（笑 / 撇）');
    expect(channelLabel('nose')).toBe('nose');
  });
});

describe('throttleLatest', () => {
  it('第一下立刻发，间隔内只记最新值，到点补发最后一次', () => {
    vi.useFakeTimers();
    const sent: number[] = [];
    const send = throttleLatest((v: number) => sent.push(v), 100);
    send(1);
    send(2);
    send(3);
    expect(sent).toEqual([1]);
    vi.advanceTimersByTime(100);
    expect(sent).toEqual([1, 3]);
    vi.advanceTimersByTime(500);
    send(4);
    expect(sent).toEqual([1, 3, 4]);
    vi.useRealTimers();
  });
});
