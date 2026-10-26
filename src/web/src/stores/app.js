import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useAppStore = defineStore('app', () => {
  const visitCount = ref(0)
  const doubled = computed(() => visitCount.value * 2)

  function visit() {
    visitCount.value += 1
  }

  return { visitCount, doubled, visit }
})
