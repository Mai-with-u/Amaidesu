<template>
  <Transition name="overlay-fade">
    <div v-if="visible" class="restart-overlay" @click.stop @keydown.stop>
      <!-- 背景动画 -->
      <div class="bg-animation">
        <div class="ring ring-1" />
        <div class="ring ring-2" />
        <div class="ring ring-3" />
        <div class="particle particle-1" />
        <div class="particle particle-2" />
        <div class="particle particle-3" />
      </div>

      <div class="overlay-content">
        <!-- 图标和状态 -->
        <div class="status-section">
          <div class="icon-wrapper" :class="{ pulse: isRunning }">
            <el-icon v-if="isRunning" class="spin-icon" :size="56">
              <Loading />
            </el-icon>
            <el-icon v-else-if="status === 'success'" class="success-icon" :size="56">
              <CircleCheck />
            </el-icon>
            <el-icon v-else-if="status === 'failed'" class="failed-icon" :size="56">
              <CircleClose />
            </el-icon>
          </div>
          <h2 class="status-title">{{ statusTitle }}</h2>
          <p class="status-desc">{{ statusDesc }}</p>
        </div>

        <!-- 进度条 -->
        <div v-if="showProgress" class="progress-section">
          <el-progress
            :percentage="progress"
            :stroke-width="8"
            :show-text="false"
            :color="progressColor"
          />
          <div class="progress-meta">
            <span>{{ Math.round(progress) }}%</span>
            <span>{{ formatTime(elapsed) }}</span>
          </div>
        </div>

        <!-- 提示 -->
        <div class="tips-box">
          <p>{{ tipText }}</p>
        </div>

        <!-- 失败操作按钮 -->
        <div v-if="status === 'failed'" class="action-buttons">
          <el-button type="primary" @click="emit('refreshPage')">
            <el-icon><Refresh /></el-icon>
            刷新页面
          </el-button>
          <el-button @click="emit('retry')">
            <el-icon><RefreshRight /></el-icon>
            重试检查
          </el-button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { Loading, CircleCheck, CircleClose, Refresh, RefreshRight } from '@element-plus/icons-vue';

// ===== Props =====
const props = withDefaults(
  defineProps<{
    visible: boolean;
    status: 'requesting' | 'restarting' | 'checking' | 'success' | 'failed';
    progress: number;
    elapsed: number;
    checkAttempts?: number;
    maxAttempts?: number;
  }>(),
  {
    checkAttempts: 0,
    maxAttempts: 60,
  },
);

// ===== Emits =====
const emit = defineEmits<{
  retry: [];
  refreshPage: [];
}>();

// ===== Computed =====
const isRunning = computed(() => ['requesting', 'restarting', 'checking'].includes(props.status));

const showProgress = computed(() => props.status !== 'failed');

const progressColor = computed(() => {
  if (props.status === 'success') return 'var(--color-success)';
  if (props.status === 'failed') return 'var(--color-danger)';
  return 'var(--color-primary)';
});

const statusTitle = computed(() => {
  const titles: Record<string, string> = {
    requesting: '正在准备重启...',
    restarting: '正在重启服务...',
    checking: '等待服务启动...',
    success: '重启完成！',
    failed: '重启超时',
  };
  return titles[props.status] || '';
});

const statusDesc = computed(() => {
  const descs: Record<string, string> = {
    requesting: '正在向服务发送重启指令',
    restarting: '服务正在关闭并重新启动',
    checking: `正在检查服务状态 (${props.checkAttempts}/${props.maxAttempts})`,
    success: '服务已成功重新启动，连接即将恢复',
    failed: '健康检查超时，请检查服务是否正常启动',
  };
  return descs[props.status] || '';
});

const tipText = computed(() => {
  const tips: Record<string, string> = {
    requesting: '请耐心等待，不要关闭此页面',
    restarting: '服务重启期间 WebSocket 连接会暂时中断',
    checking: '服务启动需要一些时间，请稍候...',
    success: '页面即将恢复可用',
    failed: '请尝试刷新页面或手动重启服务',
  };
  return tips[props.status] || '';
});

// ===== Helpers =====
function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}
</script>

<style scoped>
/* ===== Overlay ===== */
.restart-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.overlay-fade-enter-active {
  transition: opacity 0.4s ease;
}
.overlay-fade-leave-active {
  transition: opacity 0.3s ease;
}
.overlay-fade-enter-from,
.overlay-fade-leave-to {
  opacity: 0;
}

/* ===== Content ===== */
.overlay-content {
  position: relative;
  z-index: 10;
  width: 100%;
  max-width: 460px;
  margin: 0 1rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2rem;
}

/* ===== Status Section ===== */
.status-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
}

.icon-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.spin-icon {
  color: var(--color-primary);
  animation: spin 1.5s linear infinite;
}

.success-icon {
  color: var(--color-success);
}

.failed-icon {
  color: var(--color-danger);
}

.icon-wrapper.pulse::after {
  content: '';
  position: absolute;
  inset: -12px;
  border-radius: 50%;
  background: var(--color-primary);
  opacity: 0.15;
  animation: pulse-ring 2s ease-in-out infinite;
}

.status-title {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.9);
}

.status-desc {
  margin: 0;
  font-size: 0.9rem;
  color: rgba(255, 255, 255, 0.55);
  text-align: center;
  line-height: 1.5;
}

/* ===== Progress ===== */
.progress-section {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.progress-meta {
  display: flex;
  justify-content: space-between;
  font-size: 0.8rem;
  color: rgba(255, 255, 255, 0.45);
}

/* ===== Tips ===== */
.tips-box {
  width: 100%;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.tips-box p {
  margin: 0;
  font-size: 0.85rem;
  color: rgba(255, 255, 255, 0.45);
  text-align: center;
}

/* ===== Action Buttons ===== */
.action-buttons {
  display: flex;
  gap: 0.75rem;
  width: 100%;
}

.action-buttons .el-button {
  flex: 1;
}

/* ===== Background Animation ===== */
.bg-animation {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.ring {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  border: 1px solid rgba(var(--color-primary-rgb, 64, 158, 255), 0.1);
}

.ring-1 {
  width: 500px;
  height: 500px;
  animation: ping 3s ease-in-out infinite;
}

.ring-2 {
  width: 400px;
  height: 400px;
  animation: ping 3s ease-in-out 0.5s infinite;
}

.ring-3 {
  width: 300px;
  height: 300px;
  animation: ping 3s ease-in-out 1s infinite;
}

.particle {
  position: absolute;
  border-radius: 50%;
  background: rgba(var(--color-primary-rgb, 64, 158, 255), 0.15);
  animation: bounce 2s ease-in-out infinite;
}

.particle-1 {
  width: 6px;
  height: 6px;
  top: 30%;
  left: 32%;
}

.particle-2 {
  width: 4px;
  height: 4px;
  top: 65%;
  right: 28%;
  animation-delay: 0.5s;
}

.particle-3 {
  width: 5px;
  height: 5px;
  top: 45%;
  right: 35%;
  animation-delay: 1s;
}

/* ===== Keyframes ===== */
@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@keyframes pulse-ring {
  0%,
  100% {
    transform: scale(1);
    opacity: 0.15;
  }
  50% {
    transform: scale(1.3);
    opacity: 0.05;
  }
}

@keyframes ping {
  0% {
    transform: translate(-50%, -50%) scale(1);
    opacity: 1;
  }
  100% {
    transform: translate(-50%, -50%) scale(1.5);
    opacity: 0;
  }
}

@keyframes bounce {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-10px);
  }
}
</style>
