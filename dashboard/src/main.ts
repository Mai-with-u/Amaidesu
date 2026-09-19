import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import zhCN from 'element-plus/es/locale/lang/zh-cn';
import 'element-plus/dist/index.css';

import App from './App.vue';
import router from './router';
import './styles/main.css';
import { useWebSocketStore, useEventsStore, useLogsStore } from './stores';

const app = createApp(App);

app.use(createPinia());
app.use(ElementPlus, { locale: zhCN });
app.use(router);

app.mount('#app');

useWebSocketStore().init();
// 显式实例化懒加载 store，让消息处理器在启动时注册（否则进入对应页面才开始接收数据）
useEventsStore();
useLogsStore();
// 断线/刷新续传游标回填：显式触发而非 store 工厂体内隐式执行
void useEventsStore().backfill();
useWebSocketStore().connect();
