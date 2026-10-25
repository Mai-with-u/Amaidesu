// 屏幕区域捕获的纯几何换算与 region 归一化。
//
// 从 VisionCapturePanel.vue 抽出的无副作用逻辑：浏览器鼠标坐标 → 图片 CSS 像素 →
// 显示器原始像素的两级换算，以及 region（显示器相对坐标四元组）的归一化与判等。
// region 语义：null 表示全屏（未框选），[0,0,0,0] 只是输入框的中性占位，
// 两者在归一化后等价。

/** 显示器相对坐标区域 [x1, y1, x2, y2]，单位为显示器原始像素 */
export type ScreenRegion = [number, number, number, number];

/** DOMRect 的最小形状（便于测试用普通对象构造） */
export interface RectLike {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * 鼠标 client 坐标 → 图内 CSS 像素坐标。
 * 限定在图片框内；越界裁剪。容器零尺寸或负尺寸时返回 null（不可拖框）。
 */
export function clientToImgCss(
  clientX: number,
  clientY: number,
  imgRect: RectLike,
): { x: number; y: number } | null {
  if (imgRect.width <= 0 || imgRect.height <= 0) return null;
  const x = Math.max(0, Math.min(imgRect.width, clientX - imgRect.left));
  const y = Math.max(0, Math.min(imgRect.height, clientY - imgRect.top));
  return { x, y };
}

/**
 * 图内 CSS 像素坐标 → 显示器原始像素坐标（相对显示器左上角）。
 * 用图片自然宽高与显示框宽高之比做缩放换算，图片被 CSS 缩放时比例仍正确；
 * 显示框零尺寸或自然尺寸未知时退化为四舍五入（坐标不变）。
 */
export function imgCssToMonitor(
  cssX: number,
  cssY: number,
  imgRect: RectLike,
  naturalSize: { width: number; height: number },
): { x: number; y: number } {
  if (imgRect.width === 0 || imgRect.height === 0) {
    return { x: Math.round(cssX), y: Math.round(cssY) };
  }
  const ratioX = naturalSize.width / imgRect.width;
  const ratioY = naturalSize.height / imgRect.height;
  return {
    x: Math.round(cssX * ratioX),
    y: Math.round(cssY * ratioY),
  };
}

/**
 * 输入框四元组 → region 规范值。
 * 全零（[0,0,0,0]，el-input-number 不接受 null 的中性占位）归一化为 null（全屏）；
 * 其余原样返回副本。
 */
export function normalizeRegion(input: ScreenRegion): ScreenRegion | null {
  if (input[0] === 0 && input[1] === 0 && input[2] === 0 && input[3] === 0) {
    return null;
  }
  return [...input] as ScreenRegion;
}

/** 两个 region 是否等价（null 与 null 等价；null 与任何四元组不等价） */
export function regionsEqual(a: ScreenRegion | null, b: ScreenRegion | null): boolean {
  if (a === null && b === null) return true;
  if (a === null || b === null) return false;
  return a.every((v, i) => v === b[i]);
}
