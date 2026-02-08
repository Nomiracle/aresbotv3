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
    } finally {
      loading.value = false
    }
  }

  async function fetchStatus(id: number) {
    const status = await strategyApi.getStatus(id)
    statusMap.value.set(id, status)
  }

  async function fetchAllStatus() {
    const promises = strategies.value.map((s) => fetchStatus(s.id))
    await Promise.all(promises)
  }

  return {
    strategies,
    statusMap,
    loading,
    fetchStrategies,
    fetchStatus,
    fetchAllStatus,
  }
})
