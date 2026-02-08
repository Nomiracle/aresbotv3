<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import type { RunningStrategy, StrategyStatus } from '@/types'
import { strategyApi } from '@/api/strategy'
import MonitorCard from '@/components/MonitorCard.vue'

const strategies = ref<RunningStrategy[]>([])
const statusMap = ref<Map<number, StrategyStatus>>(new Map())
const loading = ref(false)
let pollTimer: number | null = null

// 只显示正在运行的策略
const runningStrategies = computed(() => {
  return strategies.value.filter(s => statusMap.value.has(s.strategy_id))
})

async function fetchStrategies() {
  loading.value = true
  try {
    strategies.value = await strategyApi.getRunning()
    statusMap.value.clear()

    strategies.value.forEach((item) => {
      statusMap.value.set(item.strategy_id, {
        strategy_id: item.strategy_id,
        is_running: item.status === 'running',
        task_id: item.task_id,
        worker_ip: item.worker_ip,
        worker_hostname: item.worker_hostname,
        current_price: item.current_price,
        pending_buys: item.pending_buys,
        pending_sells: item.pending_sells,
        position_count: item.position_count,
        started_at: item.started_at,
        updated_at: item.updated_at,
      })
    })
  } finally {
    loading.value = false
  }
}

function startPolling() {
  pollTimer = window.setInterval(() => {
    fetchStrategies()
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
    fetchStrategies()
  } catch {
    // 错误已在拦截器处理
  }
}

async function handleStop(id: number) {
  try {
    await strategyApi.stop(id)
    ElMessage.success('策略已停止')
    fetchStrategies()
  } catch {
    // 错误已在拦截器处理
  }
}

function getStatus(id: number): StrategyStatus | null {
  return statusMap.value.get(id) || null
}

const monitorCardStrategy = computed(() => {
  return runningStrategies.value.map(item => ({
    id: item.strategy_id,
    name: item.strategy_name,
    symbol: item.symbol,
    max_open_positions: item.max_open_positions,
  }))
})

onMounted(async () => {
  await fetchStrategies()
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
        v-for="strategy in monitorCardStrategy"
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
