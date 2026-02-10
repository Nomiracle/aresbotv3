<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import type { RunningStrategy, StrategyStatus } from '@/types'
import { strategyApi } from '@/api/strategy'
import MonitorCard from '@/components/MonitorCard.vue'

const OVERVIEW_ALERT_THRESHOLD = {
  totalOrderValue: 50000,
  avgPendingPerStrategy: 16,
  sellRatio: 0.68,
}

const strategies = ref<RunningStrategy[]>([])
const statusMap = ref<Map<number, StrategyStatus>>(new Map())
const loading = ref(true)
let isFetching = false
let pollTimer: number | null = null

// 只显示正在运行的策略
const runningStrategies = computed(() => {
  return strategies.value.filter(s => statusMap.value.has(s.strategy_id))
})

const monitorOverview = computed(() => {
  const summary = {
    strategyCount: runningStrategies.value.length,
    totalPendingOrders: 0,
    totalPendingBuys: 0,
    totalPendingSells: 0,
    totalOrderValue: 0,
    totalPositionCount: 0,
    avgPendingPerStrategy: 0,
    sellRatio: 0,
    errorStrategyCount: 0,
  }

  runningStrategies.value.forEach((item) => {
    const pendingBuys = item.pending_buys ?? 0
    const pendingSells = item.pending_sells ?? 0

    summary.totalPendingBuys += pendingBuys
    summary.totalPendingSells += pendingSells
    summary.totalPendingOrders += pendingBuys + pendingSells
    summary.totalPositionCount += item.position_count ?? 0

    const buyOrderValue = (item.buy_orders ?? []).reduce((total, order) => {
      return total + order.price * order.quantity
    }, 0)

    const sellOrderValue = (item.sell_orders ?? []).reduce((total, order) => {
      return total + order.price * order.quantity
    }, 0)

    summary.totalOrderValue += buyOrderValue + sellOrderValue

    if (item.last_error) {
      summary.errorStrategyCount += 1
    }
  })

  if (summary.strategyCount > 0) {
    summary.avgPendingPerStrategy = summary.totalPendingOrders / summary.strategyCount
  }

  if (summary.totalPendingOrders > 0) {
    summary.sellRatio = summary.totalPendingSells / summary.totalPendingOrders
  }

  return summary
})

const overviewSignals = computed(() => {
  const highValue = monitorOverview.value.totalOrderValue >= OVERVIEW_ALERT_THRESHOLD.totalOrderValue
  const crowdedOrders = monitorOverview.value.avgPendingPerStrategy >= OVERVIEW_ALERT_THRESHOLD.avgPendingPerStrategy
  const sellPressure = monitorOverview.value.sellRatio >= OVERVIEW_ALERT_THRESHOLD.sellRatio
  const hasErrors = monitorOverview.value.errorStrategyCount > 0

  const hasAlert = highValue || crowdedOrders || sellPressure || hasErrors
  const hasDanger = hasErrors || highValue

  let level: 'normal' | 'warning' | 'danger' = 'normal'
  let message = '状态稳定'

  if (hasDanger) {
    level = 'danger'
    message = '关注风险，建议检查异常策略或挂单规模'
  } else if (hasAlert) {
    level = 'warning'
    message = '负载偏高，建议留意卖压与挂单密度'
  }

  return {
    level,
    message,
    highValue,
    crowdedOrders,
    sellPressure,
    hasErrors,
  }
})

function formatCount(value: number) {
  return value.toLocaleString('zh-CN')
}

function formatFloat(value: number, fractionDigits = 2) {
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })
}

function formatPercent(value: number, fractionDigits = 1) {
  return `${(value * 100).toLocaleString('zh-CN', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })}%`
}

function formatUsd(value: number) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value)
}

async function fetchStrategies(options: { showLoading?: boolean } = {}) {
  const { showLoading = false } = options

  if (isFetching) {
    return
  }

  if (showLoading) {
    loading.value = true
  }

  isFetching = true
  try {
    strategies.value = await strategyApi.getRunning()
    statusMap.value.clear()

    strategies.value.forEach((item) => {
      statusMap.value.set(item.strategy_id, {
        strategy_id: item.strategy_id,
        is_running: item.status === 'running',
        exchange: item.exchange ?? undefined,
        task_id: item.task_id,
        worker_ip: item.worker_ip,
        worker_private_ip: item.worker_private_ip,
        worker_public_ip: item.worker_public_ip,
        worker_ip_location: item.worker_ip_location,
        worker_hostname: item.worker_hostname,
        current_price: item.current_price,
        pending_buys: item.pending_buys,
        pending_sells: item.pending_sells,
        buy_orders: item.buy_orders ?? [],
        sell_orders: item.sell_orders ?? [],
        position_count: item.position_count,
        extra_status: item.extra_status ?? undefined,
        started_at: item.started_at,
        updated_at: item.updated_at,
        last_error: item.last_error ?? undefined,
      })
    })
  } finally {
    isFetching = false
    if (showLoading) {
      loading.value = false
    }
  }
}

function startPolling() {
  if (pollTimer !== null) {
    return
  }

  pollTimer = window.setInterval(() => {
    void fetchStrategies()
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
    await fetchStrategies({ showLoading: true })
  } catch {
    // 错误已在拦截器处理
  }
}

async function handleStop(id: number) {
  try {
    await strategyApi.stop(id)
    ElMessage.success('策略已停止')
    await fetchStrategies({ showLoading: true })
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
    grid_levels: item.grid_levels,
    buy_price_deviation: item.buy_price_deviation,
    sell_price_deviation: item.sell_price_deviation,
    polling_interval: item.polling_interval,
    price_tolerance: item.price_tolerance,
  }))
})

onMounted(async () => {
  await fetchStrategies({ showLoading: true })
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<template>
  <div>
    <div class="page-header monitor-header">
      <h2>实时监控</h2>
    </div>

    <el-card class="overview-card" shadow="never">
      <div class="overview-head">
        <div>
          <p class="overview-kicker">运行策略总览</p>
          <h3>全局挂单概览</h3>
        </div>
        <span class="overview-note" :class="`is-${overviewSignals.level}`">
          {{ overviewSignals.message }}
        </span>
      </div>

      <div class="overview-grid">
        <div class="overview-item primary" :class="{ 'is-alert': overviewSignals.highValue }">
          <span class="label">挂单总值</span>
          <span class="value">{{ formatUsd(monitorOverview.totalOrderValue) }}</span>
        </div>
        <div class="overview-item">
          <span class="label">运行策略数</span>
          <span class="value">{{ formatCount(monitorOverview.strategyCount) }}</span>
        </div>
        <div class="overview-item" :class="{ 'is-warning': overviewSignals.crowdedOrders }">
          <span class="label">挂单总个数</span>
          <span class="value">{{ formatCount(monitorOverview.totalPendingOrders) }}</span>
        </div>
        <div class="overview-item" :class="{ 'is-warning': overviewSignals.sellPressure }">
          <span class="label">买单 / 卖单</span>
          <span class="value">{{ formatCount(monitorOverview.totalPendingBuys) }} / {{ formatCount(monitorOverview.totalPendingSells) }}</span>
          <span class="value-sub">卖单占比 {{ formatPercent(monitorOverview.sellRatio) }}</span>
        </div>
        <div class="overview-item">
          <span class="label">持仓总数</span>
          <span class="value">{{ formatCount(monitorOverview.totalPositionCount) }}</span>
        </div>
        <div class="overview-item" :class="{ 'is-alert': overviewSignals.hasErrors }">
          <span class="label">平均挂单 / 异常策略</span>
          <span class="value">{{ formatFloat(monitorOverview.avgPendingPerStrategy) }} / {{ formatCount(monitorOverview.errorStrategyCount) }}</span>
        </div>
      </div>
    </el-card>

    <div v-loading="loading" class="monitor-container">
      <MonitorCard
        v-for="(strategy, idx) in monitorCardStrategy"
        :key="strategy.id"
        :strategy="strategy"
        :status="getStatus(strategy.id)"
        :index="idx + 1"
        @start="handleStart"
        @stop="handleStop"
      />
      <el-empty v-if="runningStrategies.length === 0" description="暂无运行中的策略" style="width: 100%" />
    </div>
  </div>
</template>

<style scoped>
.monitor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.overview-card {
  margin-bottom: 14px;
  border-radius: 10px;
  border: 1px solid #d9ecff;
  background: linear-gradient(132deg, #f2f8ff 0%, #ffffff 78%);
}

.overview-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}

.overview-kicker {
  font-size: 12px;
  color: #409eff;
  font-weight: 600;
  margin-bottom: 4px;
}

.overview-head h3 {
  font-size: 18px;
  line-height: 1.2;
  color: #303133;
}

.overview-note {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid #dcdfe6;
  background: #ffffff;
}

.overview-note.is-normal {
  color: #67c23a;
  border-color: #b7eb8f;
  background: #f6ffed;
}

.overview-note.is-warning {
  color: #e6a23c;
  border-color: #f5dab1;
  background: #fff8ef;
}

.overview-note.is-danger {
  color: #f56c6c;
  border-color: #f3c8c8;
  background: #fff3f3;
}

.overview-grid {
  display: grid;
  grid-template-columns: 1.2fr repeat(5, minmax(130px, 1fr));
  gap: 10px;
}

.overview-item {
  background: #ffffff;
  border: 1px solid #e8edf4;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.overview-item.primary {
  border-color: #bfdbfe;
  background: linear-gradient(125deg, #eef6ff 0%, #ffffff 78%);
}

.overview-item.is-warning {
  border-color: #f3c987;
  background: linear-gradient(125deg, #fff9ec 0%, #ffffff 70%);
  box-shadow: 0 0 0 1px rgba(230, 162, 60, 0.08);
}

.overview-item.is-alert {
  border-color: #f3b3b3;
  background: linear-gradient(125deg, #fff4f4 0%, #ffffff 70%);
  box-shadow: 0 0 0 1px rgba(245, 108, 108, 0.1);
  animation: overviewAlertPulse 1.8s ease-in-out infinite;
}

.overview-item .label {
  font-size: 12px;
  color: #909399;
}

.overview-item .value {
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
  line-height: 1.2;
}

.overview-item .value-sub {
  font-size: 12px;
  color: #909399;
}

.monitor-container {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

@keyframes overviewAlertPulse {
  0% {
    box-shadow: 0 0 0 1px rgba(245, 108, 108, 0.1);
  }

  50% {
    box-shadow: 0 0 0 2px rgba(245, 108, 108, 0.18);
  }

  100% {
    box-shadow: 0 0 0 1px rgba(245, 108, 108, 0.1);
  }
}

@media (max-width: 1200px) {
  .overview-grid {
    grid-template-columns: repeat(3, minmax(140px, 1fr));
  }

  .overview-item.primary {
    grid-column: 1 / -1;
  }
}

@media (max-width: 768px) {
  .overview-head {
    flex-direction: column;
    margin-bottom: 10px;
  }

  .overview-note {
    white-space: normal;
  }

  .overview-grid {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .overview-item.is-alert {
    animation: none;
  }
}
</style>
