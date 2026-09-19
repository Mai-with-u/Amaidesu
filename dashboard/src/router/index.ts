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
      // 旧路径兼容：跳转到采集器管理页
      path: '/components',
      redirect: '/collectors',
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
      // 会话调试入口在直播控制台，旧路径重定向避免死链
      path: '/session',
      redirect: '/live',
    },
    {
      path: '/live',
      name: 'live-observer',
      component: () => import('@/views/LiveObserver.vue'),
    },
    {
      // 静态段须先于 /viewers/:userId 声明，避免被动态段吞掉
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
      path: '/viewers/:userId',
      name: 'viewer-detail',
      component: () => import('@/views/ViewerDetail.vue'),
      props: true,
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
      path: '/danmaku',
      name: 'danmaku',
      component: () => import('@/views/Danmaku.vue'),
      meta: { layout: false },
    },
    {
      path: '/subtitle',
      name: 'subtitle',
      component: () => import('@/views/Subtitle.vue'),
      meta: { layout: false },
    },
    {
      path: '/simulator',
      name: 'simulator',
      component: () => import('@/views/SimulatorPanel.vue'),
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
