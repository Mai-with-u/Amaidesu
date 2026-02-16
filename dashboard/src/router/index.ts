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
      path: '/providers',
      name: 'providers',
      component: () => import('@/views/Providers.vue'),
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
      path: '/devtools',
      name: 'devtools',
      component: () => import('@/views/DevTools.vue'),
    },
    {
      path: '/session',
      name: 'session',
      component: () => import('@/views/SessionHistory.vue'),
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
  ],
});

export default router;
