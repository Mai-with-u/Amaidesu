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
