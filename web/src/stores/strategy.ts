import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Strategy, StrategyStatus } from '@/types'
import { strategyApi } from '@/api/strategy'

export const useStrategyStore = defineStore('strategy', () => {
  const strategies = ref<Strategy[]>([])
  const statusMap = ref<Map<number, StrategyStatus>>(new Map())
  const loading = ref(false)

  async function fetchStrategies() {
    loading.value = true
    try {
      strategies.value = await strategyApi.getAll()
      const newMap = new Map<number, StrategyStatus>()
      for (const s of strategies.value) {
        if (s.runtime_status) {
          newMap.set(s.id, s.runtime_status)
        }
      }
      statusMap.value = newMap
    } finally {
      loading.value = false
    }
  }

  async function fetchStatus(id: number) {
    const status = await strategyApi.getStatus(id)
    statusMap.value.set(id, status)
  }

  return {
    strategies,
    statusMap,
    loading,
    fetchStrategies,
    fetchStatus,
  }
})
