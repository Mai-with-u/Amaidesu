<template>
  <div class="subtitle-container" :style="containerStyle">
    <div v-if="subtitle" class="subtitle-area" :style="areaStyle">
      <span class="subtitle-text" :style="textStyle">{{ subtitle }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';

interface SubtitleData {
  type: string;
  text: string;
  font_size?: number;
  font_color?: string;
  background_color?: string;
  border_color?: string;
}

const subtitle = ref<string>('');
const fontSize = ref<number>(32);
const fontColor = ref<string>('#ffffff');
const backgroundColor = ref<string>('rgba(0,0,0,0.45)');
const borderColor = ref<string>('#ff8800');
let ws: WebSocket | null = null;

const containerStyle = computed(() => ({
  paddingBottom: '30px',
}));

const areaStyle = computed(() => ({
  padding: '12px 30px',
  background: backgroundColor.value,
  borderLeft: `3px solid ${borderColor.value}`,
  borderRadius: '6px',
  backdropFilter: 'blur(4px)',
}));

const textStyle = computed(() => ({
  fontSize: `${fontSize.value}px`,
  fontWeight: 'bold',
  color: fontColor.value,
  textShadow: '-2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000, 0 0 10px rgba(0,0,0,0.8)',
  letterSpacing: '2px',
  lineHeight: '1.4',
}));

function connect() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${window.location.host}/ws/subtitle`);

  ws.onmessage = event => {
    try {
      const data: SubtitleData = JSON.parse(event.data);
      if (data.type === 'subtitle') {
        subtitle.value = data.text || '';
        if (data.font_size) fontSize.value = data.font_size;
        if (data.font_color) fontColor.value = data.font_color;
        if (data.background_color) backgroundColor.value = data.background_color;
        if (data.border_color) borderColor.value = data.border_color;
      }
    } catch (e) {
      console.error('解析消息失败:', e);
    }
  };

  ws.onclose = () => {
    setTimeout(connect, 3000);
  };

  ws.onerror = () => {
    ws?.close();
  };
}

onMounted(() => {
  connect();
});

onUnmounted(() => {
  if (ws) {
    ws.close();
  }
});
</script>

<style>
html,
body,
#app {
  background: transparent !important;
}
</style>

<style scoped>
.subtitle-container {
  width: 100%;
  height: 100vh;
  background: transparent !important;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  box-sizing: border-box;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
}

.subtitle-area {
  text-align: center;
  z-index: 100;
}
</style>
