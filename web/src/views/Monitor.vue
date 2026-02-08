<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import type { Strategy, StrategyStatus } from '@/types'
import { strategyApi } from '@/api/strategy'
import MonitorCard from '@/components/MonitorCard.vue'

const strategies = ref<Strategy[]>([])
const statusMap = ref<Map<number, StrategyStatus>>(new Map())
const loading = ref(false)
let pollTimer: number | null = null

// 只显示正在运行的策略
const runningStrategies = computed(() => {
  return strategies.value.filter(s => statusMap.value.has(s.id))
})

async function fetchStrategies() {
  loading.value = true
  try {
    strategies.value = await strategyApi.getAll()
  } finally {
    loading.value = false
  }
}

async function fetchStatus(id: number) {
  try {
    const status = await strategyApi.getStatus(id)
    if (status.is_running) {
      statusMap.value.set(id, status)
    } else {
      statusMap.value.delete(id)
    }
  } catch {
    statusMap.value.delete(id)
  }
}

async function fetchAllStatus() {
  await Promise.all(strategies.value.map(s => fetchStatus(s.id)))
}

function startPolling() {
  pollTimer = window.setInterval(() => {
    fetchAllStatus()
  }, 1000)
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function handleStart(id: number) {
  try {
    await strategyApi.start(id)
    ElMessage.success('策略已启动')
    fetchStatus(id)
  } catch {
    // 错误已在拦截器处理
  }
}

async function handleStop(id: number) {
  try {
    await strategyApi.stop(id)
    ElMessage.success('策略已停止')
    // 立即从 map 中删除
    statusMap.value.delete(id)
  } catch {
    // 错误已在拦截器处理
  }
}

function getStatus(id: number): StrategyStatus | null {
  return statusMap.value.get(id) || null
}

onMounted(async () => {
  await fetchStrategies()
  await fetchAllStatus()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>实时监控</h2>
    </div>

    <div v-loading="loading">
      <MonitorCard
        v-for="strategy in runningStrategies"
        :key="strategy.id"
        :strategy="strategy"
        :status="getStatus(strategy.id)"
        @start="handleStart"
        @stop="handleStop"
      />
      <el-empty v-if="runningStrategies.length === 0" description="暂无运行中的策略" />
    </div>
  </div>
</template>
