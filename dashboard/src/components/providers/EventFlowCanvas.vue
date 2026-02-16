<template>
  <div class="flow-container" :class="{ disabled: isDisabled }">
    <div
      v-for="particle in particles"
      :key="particle.id"
      class="flow-particle"
      :class="[`flow-${particle.flowDirection}`]"
      :style="{
        '--delay': `${particle.delay}ms`,
        '--duration': `${particle.duration}ms`,
        '--start-y': `${particle.startY}%`,
        '--color': particle.color,
        '--glow-color': particle.glowColor,
      }"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue';

interface Props {
  flowing: boolean;
  fromDomain: 'input' | 'decision';
  toDomain: 'decision' | 'output';
  eventType: string;
}

const props = defineProps<Props>();

interface Particle {
  id: string;
  startY: number;
  color: string;
  glowColor: string;
  delay: number;
  duration: number;
  flowDirection: 'left-to-right';
}

const particles = ref<Particle[]>([]);

// Event type to color mapping
const EVENT_COLORS: Record<string, { color: string; glow: string }> = {
  'message.received': {
    color: 'var(--color-input)',
    glow: 'var(--color-input)',
  },
  'decision.intent': {
    color: 'var(--color-decision)',
    glow: 'var(--color-decision)',
  },
  'output.render': {
    color: 'var(--color-output)',
    glow: 'var(--color-output)',
  },
};

// Check if we're on a small screen (animations disabled)
const isDisabled = ref(false);

function checkScreenSize() {
  isDisabled.value = window.innerWidth < 768;
}

// Generate particles when flow is triggered
function emitParticles() {
  if (isDisabled.value || !props.flowing) return;

  const colorConfig = EVENT_COLORS[props.eventType] || {
    color: 'var(--color-primary)',
    glow: 'var(--color-primary)',
  };

  // Emit 3-5 particles with staggered timing
  const particleCount = 3 + Math.floor(Math.random() * 3);

  for (let i = 0; i < particleCount; i++) {
    const particle: Particle = {
      id: `${Date.now()}-${Math.random()}-${i}`,
      startY: 30 + Math.random() * 40, // Random Y between 30-70%
      color: colorConfig.color,
      glowColor: colorConfig.glow,
      delay: i * 80 + Math.random() * 40, // Staggered start
      duration: 600 + Math.random() * 400, // 600-1000ms
      flowDirection: 'left-to-right',
    };

    particles.value.push(particle);
  }

  // Clean up particles after animation completes
  const maxDuration = 600 + 400 + (particleCount - 1) * 80 + 40 + 100;
  setTimeout(() => {
    particles.value = [];
  }, maxDuration);
}

// Watch for flow trigger
watch(
  () => props.flowing,
  (newVal) => {
    if (newVal) {
      emitParticles();
    }
  },
  { immediate: true },
);

onMounted(() => {
  checkScreenSize();
  window.addEventListener('resize', checkScreenSize);
});

onUnmounted(() => {
  window.removeEventListener('resize', checkScreenSize);
  particles.value = [];
});
</script>

<style scoped>
.flow-container {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
  z-index: 10;
  overflow: hidden;
}

.flow-container.disabled {
  display: none;
}

/* Particle base styles */
.flow-particle {
  position: absolute;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--color);
  box-shadow:
    0 0 6px var(--glow-color),
    0 0 12px var(--glow-color),
    0 0 20px rgba(255, 255, 255, 0.3);
  animation: flow-left-to-right var(--duration) var(--delay) cubic-bezier(0.4, 0, 0.2, 1) forwards;
  opacity: 0;
  will-change: transform, opacity;
}

/* Left to Right flow (Input -> Decision, Decision -> Output) */
@keyframes flow-left-to-right {
  0% {
    left: 0;
    top: var(--start-y);
    transform: translateX(-50%) scale(0.5);
    opacity: 0;
  }
  15% {
    opacity: 1;
    transform: translateX(0) scale(1);
  }
  85% {
    opacity: 1;
    transform: translateX(calc(100cqw - 10px)) scale(1);
  }
  100% {
    left: 100%;
    top: var(--start-y);
    transform: translateX(-50%) scale(0.5);
    opacity: 0;
  }
}

.flow-container {
  container-type: inline-size;
}

/* Particle trail effect - pseudo element creates a fading trail */
.flow-particle::before {
  content: '';
  position: absolute;
  width: 30px;
  height: 4px;
  background: linear-gradient(90deg, transparent, var(--glow-color));
  border-radius: 2px;
  top: 50%;
  right: 100%;
  transform: translateY(-50%);
  opacity: 0.6;
}

/* Glow pulse effect */
.flow-particle::after {
  content: '';
  position: absolute;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--glow-color) 0%, transparent 70%);
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  opacity: 0.4;
  animation: pulse 0.6s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    transform: translate(-50%, -50%) scale(1);
    opacity: 0.4;
  }
  50% {
    transform: translate(-50%, -50%) scale(1.3);
    opacity: 0.2;
  }
}

/* Reduced motion support */
@media (prefers-reduced-motion: reduce) {
  .flow-particle {
    animation-duration: 0.01ms !important;
    animation-delay: 0ms !important;
  }

  .flow-particle::before,
  .flow-particle::after {
    display: none;
  }
}

/* Mobile - hide entirely */
@media (max-width: 768px) {
  .flow-container {
    display: none;
  }
}
</style>
