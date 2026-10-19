/**
 * 小部件目录：管理页列表与嵌入页共用的静态元数据。
 *
 * 嵌入页指供 OBS/直播姬浏览器源捕获的全屏透明页，路由统一挂在
 * /widget/<name> 前缀下；管理页（/widgets）据本目录渲染列表与详情。
 */

export interface WidgetMeta {
  /** 稳定标识（嵌入页路由末段，也是后续配置键面的归属名） */
  key: string;
  name: string;
  /** 一句话说明用途，并指向配套配置所在 */
  description: string;
  /** 嵌入页路由路径 */
  path: string;
  /** 建议在直播软件浏览器源里设置的宽高 */
  recommendedSize: string;
}

const WIDGET_CATALOG: readonly WidgetMeta[] = [
  {
    key: 'danmaku',
    name: '弹幕',
    description: '实时展示直播间的弹幕、礼物、SC 与大航海。显示哪几类内容在设置页调整。',
    path: '/widget/danmaku',
    recommendedSize: '1920 × 1080（也可窄条 400 × 1080）',
  },
  {
    key: 'subtitle',
    name: '字幕',
    description: '跟随语音显示主播正在说的话，画面置底。字号、颜色、位置在设置页调整。',
    path: '/widget/subtitle',
    recommendedSize: '1920 × 1080',
  },
  {
    key: 'expression_sheet',
    name: '差分立绘',
    description: '跟随主播说话内容自动切换表情差分立绘。素材目录在设置页 avatar 域配置。',
    path: '/widget/expression_sheet',
    recommendedSize: '1920 × 1080',
  },
  {
    key: 'game2048',
    name: '2048',
    description: '观众发弹幕指令一起玩的 2048，实时棋盘与操作历史。',
    path: '/widget/game2048',
    recommendedSize: '约 900 × 560',
  },
];

export function useWidgetCatalog(): { widgets: readonly WidgetMeta[] } {
  return { widgets: WIDGET_CATALOG };
}

/** 嵌入页预览态的查询参数名：带参=从管理页打开的预览，允许显示占位与提示 */
export const PREVIEW_QUERY = 'preview';

/**
 * 拼嵌入页完整网址。嵌入态（复制进直播软件）不带查询参数，页面保持极简；
 * 预览态（管理页打开）带 preview=1，允许占位文字等不上屏的提示显示。
 */
export function buildWidgetUrl(origin: string, path: string, options?: { preview?: boolean }): string {
  const url = new URL(path, origin);
  if (options?.preview) url.searchParams.set(PREVIEW_QUERY, '1');
  return url.toString();
}

/** 当前页面是否为预览态（管理页"打开"带 ?preview=1）；嵌入直播软件的裸网址返回 false */
export function isPreviewMode(): boolean {
  return new URLSearchParams(window.location.search).get(PREVIEW_QUERY) === '1';
}
