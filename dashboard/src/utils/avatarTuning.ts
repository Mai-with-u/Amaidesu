// 形象调校页的纯逻辑：调参字段分组、通道中文名、眼神方框坐标换算、配方编辑、拖动限流。
//
// 字段的标题、说明、取值范围都由后端从配置 Schema 现取（单一事实源），这里只决定
// "放在哪一组、按什么顺序"——分组是页面排版，新加的字段没登记分组时落进"其他"，
// 不会凭空消失。

import type { AvatarTuningField } from '@/types';

/** 调参分组：标题 + 组内字段（按显示顺序） */
export interface TuningFieldGroup {
  title: string;
  fields: AvatarTuningField[];
}

/** 页面上的分组与顺序；眼神方向类字段由眼神方框单独编辑，不在这里重复 */
const GROUP_LAYOUT: ReadonlyArray<{ title: string; keys: readonly string[] }> = [
  { title: '人设', keys: ['rest_smile'] },
  {
    title: '眼神',
    keys: [
      'gaze_enabled',
      'thinking_gaze_enabled',
      'thinking_gaze_mirror',
      'head_follow_deg',
      'reading_ms',
    ],
  },
  { title: '头部', keys: ['head_wander_enabled', 'head_wander_deg'] },
  {
    title: '眨眼',
    keys: [
      'blink_enabled',
      'blink_per_min_idle',
      'blink_per_min_speaking',
      'blink_per_min_reading',
    ],
  },
  {
    title: '表情',
    keys: [
      'expression_linger_ms',
      'expression_min_dwell_ms',
      'expression_tool_hold_ms',
      'intensity_floor',
      'intensity_gain',
    ],
  },
  { title: '心情', keys: ['mood_enabled', 'mood_half_life_ms', 'mood_gain'] },
  {
    title: '反应',
    keys: ['room_reactions_enabled', 'game_reactions_enabled', 'speech_nods_enabled'],
  },
];

/** 眼神方框里拖动编辑的目标点：名字、对应的两个字段、点的颜色 */
export const GAZE_TARGETS = [
  { id: 'camera', label: '镜头', xKey: 'camera_gaze_x', yKey: 'camera_gaze_y', color: '#409eff' },
  { id: 'chat', label: '弹幕区', xKey: 'chat_gaze_x', yKey: 'chat_gaze_y', color: '#e6a23c' },
  {
    id: 'thinking',
    label: '思考',
    xKey: 'thinking_gaze_x',
    yKey: 'thinking_gaze_y',
    color: '#67c23a',
  },
] as const;

const GAZE_KEYS = new Set<string>(GAZE_TARGETS.flatMap(t => [t.xKey, t.yKey]));

/** 按页面分组排好字段；眼神方向字段交给眼神方框，没登记的字段收进"其他" */
export function groupTuningFields(fields: AvatarTuningField[]): TuningFieldGroup[] {
  const byKey = new Map(fields.map(f => [f.key, f]));
  const placed = new Set<string>(GAZE_KEYS);
  const groups: TuningFieldGroup[] = [];
  for (const { title, keys } of GROUP_LAYOUT) {
    const groupFields = keys.flatMap(key => {
      const field = byKey.get(key);
      if (!field) return [];
      placed.add(key);
      return [field];
    });
    if (groupFields.length) groups.push({ title, fields: groupFields });
  }
  const rest = fields.filter(f => !placed.has(f.key));
  if (rest.length) groups.push({ title: '其他', fields: rest });
  return groups;
}

/** 姿态通道与单个（不分左右）表情系数的中文名（括号里注明单位或方向） */
const DIRECT_LABELS: Readonly<Record<string, string>> = {
  head_yaw: '头左右转（度）',
  head_pitch: '头上下点（度）',
  head_roll: '歪头（度）',
  body_yaw: '身体左右转（度）',
  body_pitch: '身体前后倾（度）',
  body_roll: '身体左右歪（度）',
  body_x: '身体左右位置',
  body_y: '身体上下位置',
  body_z: '身体前后（正数前倾）',
  gaze_x: '视线左右',
  gaze_y: '视线上下',
  browInnerUp: '眉心上提',
  cheekPuff: '鼓腮',
  jawOpen: '下巴张开',
  jawForward: '下巴前伸',
  jawLeft: '下巴往她的左边',
  jawRight: '下巴往她的右边',
  mouthClose: '嘴唇合拢（下巴张着时）',
  mouthFunnel: '撮唇（O 型）',
  mouthPucker: '撅嘴',
  mouthLeft: '嘴往她的左边',
  mouthRight: '嘴往她的右边',
  mouthRollLower: '下唇内卷',
  mouthRollUpper: '上唇内卷',
  mouthShrugLower: '下唇上嘟',
  mouthShrugUpper: '上唇上嘟',
  tongueOut: '吐舌',
  aa: '口型 a',
  ih: '口型 i',
  ou: '口型 u',
  ee: '口型 e',
  oh: '口型 o',
};

/** 分左右的表情系数：去掉 Left / Right 后的名字（也是配方里的双侧简写）→ 中文名 */
const SIDED_LABELS: Readonly<Record<string, string>> = {
  eyeBlink: '闭眼',
  eyeSquint: '下眼睑上推（笑眼）',
  eyeWide: '睁大',
  browDown: '压眉',
  browOuterUp: '眉外侧上提',
  cheekSquint: '脸颊上提',
  noseSneer: '皱鼻',
  mouthSmile: '嘴角上扬',
  mouthFrown: '嘴角下垂',
  mouthDimple: '嘴角后收（酒窝）',
  mouthStretch: '嘴角往两边咧',
  mouthPress: '抿嘴',
  mouthLowerDown: '下唇下拉',
  mouthUpperUp: '上唇上提',
};

/**
 * 通道与配方键的中文名。表情系数用 ARKit 原名，左右指形象自己的左右；
 * 去掉左右的名字是配方里的双侧简写。
 */
export function channelLabel(key: string): string {
  if (key in DIRECT_LABELS) return DIRECT_LABELS[key];
  if (key in SIDED_LABELS) return `${SIDED_LABELS[key]}（双侧）`;
  const sided = /^(.*)(Left|Right)$/.exec(key);
  if (sided && sided[1] in SIDED_LABELS) {
    return `${SIDED_LABELS[sided[1]]}（${sided[2] === 'Left' ? '左' : '右'}）`;
  }
  return key;
}

/** 通道按部位分组的组名与顺序（实时通道、配方编辑共用） */
export const CHANNEL_GROUPS = [
  '头部',
  '身体',
  '视线',
  '眉',
  '眼',
  '脸颊与鼻',
  '嘴',
  '下巴',
  '舌',
  '口型',
] as const;

const VISEME_NAMES = new Set(['aa', 'ih', 'ou', 'ee', 'oh']);

export function channelGroup(name: string): (typeof CHANNEL_GROUPS)[number] {
  if (name.startsWith('head_')) return '头部';
  if (name.startsWith('body_')) return '身体';
  if (name.startsWith('gaze_')) return '视线';
  if (name.startsWith('brow')) return '眉';
  if (name.startsWith('eye')) return '眼';
  if (name.startsWith('cheek') || name.startsWith('nose')) return '脸颊与鼻';
  if (name.startsWith('jaw')) return '下巴';
  if (name.startsWith('tongue')) return '舌';
  if (VISEME_NAMES.has(name)) return '口型';
  return '嘴';
}

/** 把一串通道名按部位分组（组按 CHANNEL_GROUPS 排序，组内保持原顺序，空组不列） */
export function groupChannels<T>(
  items: T[],
  nameOf: (item: T) => string,
): { group: string; items: T[] }[] {
  return CHANNEL_GROUPS.map(group => ({
    group,
    items: items.filter(item => channelGroup(nameOf(item)) === group),
  })).filter(entry => entry.items.length > 0);
}

/** 情绪名的中文说明（配方列表显示用） */
export const EMOTION_LABELS: Readonly<Record<string, string>> = {
  happy: '开心',
  excited: '兴奋',
  love: '喜爱',
  sad: '难过',
  crying: '哭',
  angry: '生气',
  disgusted: '嫌弃',
  surprised: '惊讶',
  scared: '害怕',
  confused: '疑惑',
  serious: '认真',
  shy: '害羞',
  embarrassed: '尴尬',
  smug: '得意',
  speechless: '无语',
  tired: '困',
};

export function emotionLabel(emotion: string): string {
  const label = EMOTION_LABELS[emotion];
  return label ? `${label}（${emotion}）` : emotion;
}

/**
 * 视线坐标（-1~1，右、上为正）→ 方框内百分比位置（左上角为原点）。
 * 方框按观众看直播画面的方向画：右边是 +x、上边是 +y。
 */
export function gazeToPercent(x: number, y: number): { left: number; top: number } {
  const clamp = (v: number): number => Math.max(-1, Math.min(1, v));
  return { left: ((clamp(x) + 1) / 2) * 100, top: ((1 - clamp(y)) / 2) * 100 };
}

/** 方框内的鼠标位置 → 视线坐标（保留两位小数，越界裁到 ±1） */
export function pointerToGaze(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
): { x: number; y: number } | null {
  if (rect.width <= 0 || rect.height <= 0) return null;
  const round = (v: number): number => Math.round(Math.max(-1, Math.min(1, v)) * 100) / 100;
  const x = ((clientX - rect.left) / rect.width) * 2 - 1;
  const y = 1 - ((clientY - rect.top) / rect.height) * 2;
  return { x: round(x), y: round(y) };
}

/**
 * 配方键的滑条范围：跨度来自后端（通道取值范围的跨度），头部角度类给整数步长，
 * 其余 0.05 一档。
 */
export function recipeSliderStep(span: number): number {
  return span >= 10 ? 1 : 0.05;
}

/** 配方里还能加哪些键（已有的不重复列） */
export function addableRecipeKeys(allKeys: string[], offsets: Record<string, number>): string[] {
  return allKeys.filter(key => !(key in offsets));
}

/**
 * 拖滑条时限流：最多每 intervalMs 发一次，停手后补发最后一次（形象最终落在松手时的值上）。
 * 拖动时每秒几十个 input 事件，原样全发会把后端和 VTS 的注入队列堆满。
 */
export function throttleLatest<A extends unknown[]>(
  fn: (...args: A) => void,
  intervalMs: number,
): (...args: A) => void {
  let lastAt = -Infinity;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: A | null = null;
  const flush = (): void => {
    timer = null;
    if (pending === null) return;
    const args = pending;
    pending = null;
    lastAt = Date.now();
    fn(...args);
  };
  return (...args: A): void => {
    pending = args;
    if (timer !== null) return;
    const wait = lastAt + intervalMs - Date.now();
    if (wait <= 0) flush();
    else timer = setTimeout(flush, wait);
  };
}
