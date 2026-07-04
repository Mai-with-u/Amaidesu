import { defineStore } from 'pinia';
import { shallowRef } from 'vue';
import { useWebSocketStore } from './websocket';
import type { WebSocketMessage } from '@/types';

export const useEventsStore = defineStore('events', () => {
  const events = shallowRef<WebSocketMessage[]>([]);
  const maxEvents = 100;

  function handleEvent(message: WebSocketMessage) {
    const newEvents = [...events.value, message];
    if (newEvents.length > maxEvents) {
      newEvents.splice(0, newEvents.length - maxEvents);
    }
    events.value = newEvents;
  }

  useWebSocketStore().subscribe(handleEvent);

  function clearEvents() {
    events.value = [];
  }

  return {
    events,
    clearEvents,
  };
});
