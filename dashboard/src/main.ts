import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import zhCN from 'element-plus/es/locale/lang/zh-cn';
import 'element-plus/dist/index.css';

import App from './App.vue';
import router from './router';
import './styles/main.css';

const app = createApp(App);

app.use(createPinia());
app.use(ElementPlus, { locale: zhCN });
app.use(router);

app.mount('#app');
