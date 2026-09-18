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
      // v2 路由拆分后保留旧路径兼容：直接跳转到采集器管理页
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
      // 会话调试页已收编为直播控制台的会话显示模式，旧路径重定向避免死链
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

export default router;
