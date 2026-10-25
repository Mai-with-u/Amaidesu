import { createRouter, createWebHistory } from 'vue-router';

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/Dashboard.vue'),
    },
    {
      // 场次复盘（只读聚合 + 超阈染色 + 数据包导出）
      path: '/review',
      name: 'session-review',
      component: () => import('@/views/SessionReview.vue'),
    },
    {
      path: '/collectors',
      name: 'collectors',
      component: () => import('@/views/Collectors.vue'),
    },
    {
      path: '/agents',
      name: 'agents',
      component: () => import('@/views/Agents.vue'),
    },
    {
      path: '/tools',
      name: 'tools',
      component: () => import('@/views/Tools.vue'),
    },
    {
      path: '/memory',
      name: 'memory',
      component: () => import('@/views/Memory.vue'),
    },
    {
      path: '/eventlog',
      name: 'eventlog',
      component: () => import('@/views/EventLog.vue'),
    },
    {
      path: '/logs',
      name: 'logs',
      component: () => import('@/views/LogViewer.vue'),
    },
    {
      path: '/live',
      name: 'live-observer',
      component: () => import('@/views/LiveObserver.vue'),
    },
    {
      // 静态段须先于 /viewers/:viewerId 声明，避免被动态段吞掉
      path: '/viewers/insights',
      name: 'viewer-insights',
      component: () => import('@/views/ViewerInsights.vue'),
    },
    {
      path: '/viewers',
      name: 'viewers',
      component: () => import('@/views/Viewers.vue'),
    },
    {
      // 按内部观众 ID 定位档案页；路径段是数字，转成 number 传给页面
      path: '/viewers/:viewerId(\d+)',
      name: 'viewer-detail',
      component: () => import('@/views/ViewerDetail.vue'),
      props: route => ({ viewerId: Number(route.params.viewerId) }),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/Settings.vue'),
    },
    {
      path: '/llm/usage',
      name: 'llm-usage',
      component: () => import('@/views/LLMUsage.vue'),
    },
    {
      path: '/llm/history',
      name: 'llm-history',
      component: () => import('@/views/LLMHistory.vue'),
    },
    {
      // 小部件管理页：嵌入页列表、复制嵌入网址与配置入口（壳内常规页面）
      path: '/widgets',
      name: 'widgets',
      component: () => import('@/views/Widgets.vue'),
    },
    {
      // 弹幕嵌入页（给 OBS/直播姬浏览器源嵌的全屏透明页，不在壳内导航）
      path: '/widget/danmaku',
      name: 'danmaku',
      component: () => import('@/views/Danmaku.vue'),
      meta: { layout: false },
    },
    {
      // 字幕嵌入页（同上）
      path: '/widget/subtitle',
      name: 'subtitle',
      component: () => import('@/views/Subtitle.vue'),
      meta: { layout: false },
    },
    {
      // 表情差分嵌入页（同上）
      path: '/widget/expression_sheet',
      name: 'expression-sheet-widget',
      component: () => import('@/views/ExpressionSheet.vue'),
      meta: { layout: false },
    },
    {
      // 2048 嵌入页（同上）
      path: '/widget/game2048',
      name: 'game2048',
      component: () => import('@/views/Game2048.vue'),
      meta: { layout: false },
    },
    {
      path: '/simulator',
      name: 'simulator',
      component: () => import('@/views/SimulatorPanel.vue'),
    },
    {
      path: '/debug',
      name: 'debug',
      component: () => import('@/views/Debug.vue'),
    },
    {
      // 形象表情调试台（四平台连接态 / 表情触发 / 参数直调）
      path: '/avatar-debug',
      name: 'avatar-debug',
      component: () => import('@/views/AvatarDebug.vue'),
    },
    {
      path: '/outline',
      name: 'outline-workbench',
      component: () => import('@/views/OutlineWorkbench.vue'),
    },
  ],
});

// 后端重建后懒加载 chunk 的 hash 已更换，长开标签页里旧引用的 import 会 404；
// 整页跳转目标路由，让浏览器拉取新 index 与新 chunk 完成自愈。
// 30 秒窗口内只自愈一次：服务端持续异常时避免整页刷新死循环。
const SELF_HEAL_THROTTLE_KEY = 'router-chunk-self-heal-at';

router.onError((error, to) => {
  const message = error instanceof Error ? error.message : String(error);
  if (
    !message.includes('Failed to fetch dynamically imported module') &&
    !message.includes('Importing a module script failed')
  ) {
    return;
  }
  const lastHealAt = Number(sessionStorage.getItem(SELF_HEAL_THROTTLE_KEY) ?? 0);
  if (Number.isFinite(lastHealAt) && Date.now() - lastHealAt < 30_000) return;
  sessionStorage.setItem(SELF_HEAL_THROTTLE_KEY, String(Date.now()));
  window.location.href = to.fullPath;
});

export default router;
