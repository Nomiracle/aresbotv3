<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { InfoFilled, WarningFilled } from '@element-plus/icons-vue'
import type { StrategyStatus, OrderDetail } from '@/types'

interface MonitorCardStrategy {
  id: number
  name: string
  symbol: string
  max_open_positions: number
  grid_levels: number
  buy_price_deviation: string
  sell_price_deviation: string
  polling_interval: string
  price_tolerance: string
}

const props = defineProps<{
  strategy: MonitorCardStrategy
  status: StrategyStatus | null
  index: number
}>()

const emit = defineEmits<{
  (e: 'stop', id: number): void
  (e: 'start', id: number): void
}>()

const isRunning = computed(() => props.status?.is_running ?? false)

// 运行时间计时器
const runTimeSeconds = ref(0)
let runTimeTimer: number | null = null

function updateRunTime() {
  const startTime = props.status?.start_timestamp || props.status?.started_at
  if (startTime) {
    runTimeSeconds.value = Math.floor(Date.now() / 1000 - startTime)
  }
}

const runTimeDisplay = computed(() => {
  const seconds = runTimeSeconds.value
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  return `${hours}h ${minutes}m ${secs}s`
})

// 更新时间（多少秒前）
const updateTimeAgo = computed(() => {
  if (!props.status?.updated_at) return '-'
  const seconds = Math.floor(Date.now() / 1000 - props.status.updated_at)
  if (seconds < 60) return `${seconds}s前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m前`
  return `${Math.floor(seconds / 3600)}h前`
})

// Worker 信息显示
const workerDisplay = computed(() => {
  const status = props.status
  if (!status) {
    return ''
  }

  const publicIp = status.worker_public_ip || status.worker_ip || ''
  const privateIp = status.worker_private_ip || ''
  const location = status.worker_ip_location || ''

  const details: string[] = []
  if (location) {
    details.push(location)
  }
  if (privateIp && privateIp !== publicIp) {
    details.push(`内网:${privateIp}`)
  }

  if (!publicIp) {
    return details.join(' | ')
  }

  if (details.length === 0) {
    return `出口IP: ${publicIp}`
  }

  return `出口IP: ${publicIp} | ${details.join(' | ')}`
})

const exchangeLabel = computed(() => {
  const exchange = props.status?.exchange
  if (!exchange) {
    return ''
  }

  const labels: Record<string, string> = {
    binance: 'Binance Spot',
    binance_spot: 'Binance Spot',
    polymarket_updown15m: 'Polymarket 15m',
  }

  return labels[exchange] ?? exchange
})

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null
  }
  if (typeof value === 'string') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

const polymarketStatus = computed(() => {
  if (props.status?.exchange !== 'polymarket_updown15m') {
    return null
  }

  const extra = props.status.extra_status ?? {}
  return {
    marketSlug: typeof extra.market_slug === 'string' ? extra.market_slug : '-',
    tokenId: typeof extra.token_id === 'string' ? extra.token_id : '',
    secondsUntilClose: toFiniteNumber(extra.seconds_until_close),
    isClosing: Boolean(extra.is_closing),
  }
})

const polymarketTooltipText = computed(() => {
  const info = polymarketStatus.value
  if (!info) {
    return ''
  }

  return `数据来源：worker 上报的 extra_status（Polymarket 适配器）。\n` +
    `market：当前15分钟市场 slug。\n` +
    `剩余：距离该市场结束的倒计时。\n` +
    `token：当前交易 outcome 对应的 token_id。`
})

const isPolymarketCritical = computed(() => {
  const info = polymarketStatus.value
  if (!info) {
    return false
  }
  return info.isClosing || (info.secondsUntilClose !== null && info.secondsUntilClose <= 60)
})

function formatCountdown(seconds: number | null): string {
  if (seconds === null) {
    return '-'
  }
  const safeSeconds = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(safeSeconds / 60)
  const remainSeconds = safeSeconds % 60
  return `${minutes}m ${remainSeconds.toString().padStart(2, '0')}s`
}

function compactToken(tokenId: string): string {
  if (!tokenId) {
    return '-'
  }
  if (tokenId.length <= 14) {
    return tokenId
  }
  return `${tokenId.slice(0, 6)}...${tokenId.slice(-4)}`
}

function toNumber(value: unknown): number {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : 0
  }
  if (typeof value === 'string') {
    const normalized = value.replace(/,/g, '')
    const parsed = Number(normalized)
    return Number.isFinite(parsed) ? parsed : 0
  }
  return 0
}

function normalizeOrders(orders: OrderDetail[]): OrderDetail[] {
  return orders.map((order) => ({
    price: toNumber((order as unknown as { price?: unknown }).price),
    quantity: toNumber((order as unknown as { quantity?: unknown }).quantity),
  }))
}

// 排序后的买单（降序）和卖单（升序）
const sortedBuyOrders = computed(() =>
  normalizeOrders(props.status?.buy_orders ?? []).sort((a, b) => b.price - a.price)
)
const sortedSellOrders = computed(() =>
  normalizeOrders(props.status?.sell_orders ?? []).sort((a, b) => a.price - b.price)
)

// 挂单总价值
const totalOrderValue = computed(() => {
  const buyOrders = props.status?.buy_orders ?? []
  const sellOrders = props.status?.sell_orders ?? []
  let total = 0
  buyOrders.forEach((o: OrderDetail) => {
    total += o.price * o.quantity
  })
  sellOrders.forEach((o: OrderDetail) => {
    total += o.price * o.quantity
  })
  return total
})

function formatPrice(price: number | null | undefined) {
  if (price === null || price === undefined) return '-'
  return price.toLocaleString('en-US', { maximumFractionDigits: 8 })
}

function formatOrderValue(value: number) {
  if (value >= 1000) {
    return `$${(value / 1000).toFixed(2)}K`
  }
  return `$${value.toFixed(2)}`
}

// 计算挂单偏移百分比
function calcOffset(orderPrice: number, currentPrice: number | null | undefined): string {
  if (!currentPrice || currentPrice === 0) return ''
  const offset = ((orderPrice - currentPrice) / currentPrice) * 100
  const sign = offset >= 0 ? '+' : ''
  return `(${sign}${offset.toFixed(2)}%)`
}

// 格式化挂单显示
function formatOrderDisplay(order: OrderDetail, currentPrice: number | null | undefined): string {
  const priceStr = `$${formatPrice(order.price)}`
  const qtyStr = order.quantity.toString()
  const offset = calcOffset(order.price, currentPrice)
  return `${priceStr}@${qtyStr}${offset}`
}

onMounted(() => {
  updateRunTime()
  runTimeTimer = window.setInterval(updateRunTime, 1000)
})

onUnmounted(() => {
  if (runTimeTimer !== null) {
    clearInterval(runTimeTimer)
    runTimeTimer = null
  }
})
</script>

<template>
  <div class="monitor-card">
    <!-- 头部：编号+名称+交易对 + 状态+停止按钮 -->
    <div class="card-header">
      <div class="title">
        <span class="index">#{{ index }}</span>
        <span class="title-text">{{ strategy.name }}</span>
        <el-tag size="small" class="symbol-tag">{{ strategy.symbol }}</el-tag>
        <el-tag v-if="exchangeLabel" size="small" type="info" effect="plain" class="exchange-tag">
          {{ exchangeLabel }}
        </el-tag>
      </div>
      <div class="status">
        <span :class="['status-dot', isRunning ? 'running' : 'stopped']"></span>
        <span class="status-text">{{ isRunning ? '运行中' : '已停止' }}</span>
        <el-button
          v-if="isRunning"
          type="warning"
          size="small"
          class="action-btn"
          @click="emit('stop', strategy.id)"
        >
          停止
        </el-button>
        <el-button
          v-else
          type="success"
          size="small"
          class="action-btn"
          @click="emit('start', strategy.id)"
        >
          启动
        </el-button>
      </div>
    </div>

    <!-- 时间行：运行时间 + 更新时间 + Worker -->
    <div v-if="isRunning" class="time-row">
      <span>运行: {{ runTimeDisplay }}</span>
      <span class="separator">|</span>
      <span>更新: {{ updateTimeAgo }}</span>
      <template v-if="workerDisplay">
        <span class="separator">|</span>
        <span class="worker-info">{{ workerDisplay }}</span>
      </template>
    </div>

    <div v-if="isRunning && polymarketStatus" class="exchange-row">
      <span class="exchange-label">市场:</span>
      <span class="exchange-value">{{ polymarketStatus.marketSlug }}</span>
      <el-tooltip
        effect="dark"
        placement="top"
        :content="polymarketTooltipText"
        :show-after="200"
        :max-width="360"
      >
        <el-icon class="exchange-help"><InfoFilled /></el-icon>
      </el-tooltip>
      <span class="separator">|</span>
      <span class="exchange-label">剩余:</span>
      <span :class="['exchange-value', { danger: isPolymarketCritical }]">
        {{ formatCountdown(polymarketStatus.secondsUntilClose) }}
      </span>
      <template v-if="polymarketStatus.tokenId">
        <span class="separator">|</span>
        <span class="exchange-label">Token:</span>
        <span class="exchange-value token">{{ compactToken(polymarketStatus.tokenId) }}</span>
      </template>
    </div>

    <!-- 价格行 -->
    <div class="price-row">
      <div class="price-item">
        <span class="label">现价:</span>
        <span class="value price">${{ formatPrice(status?.current_price) }}</span>
      </div>
      <div class="price-item">
        <span class="label">挂单总值:</span>
        <span class="value">{{ formatOrderValue(totalOrderValue) }}</span>
      </div>
    </div>

    <!-- 挂单详情 -->
    <div class="orders-section">
      <div class="order-line buy">
        <span class="order-label">买单({{ status?.pending_buys ?? 0 }}):</span>
        <span v-if="!sortedBuyOrders.length" class="order-empty">-</span>
        <span v-else class="order-list">
          <span
            v-for="(order, idx) in sortedBuyOrders.slice(0, 3)"
            :key="idx"
            class="order-item"
          >
            {{ formatOrderDisplay(order, status?.current_price) }}
          </span>
          <span v-if="sortedBuyOrders.length > 3">...</span>
        </span>
      </div>
      <div class="order-line sell">
        <span class="order-label">卖单({{ status?.pending_sells ?? 0 }}):</span>
        <span v-if="!sortedSellOrders.length" class="order-empty">-</span>
        <span v-else class="order-list">
          <span
            v-for="(order, idx) in sortedSellOrders.slice(0, 3)"
            :key="idx"
            class="order-item"
          >
            {{ formatOrderDisplay(order, status?.current_price) }}
          </span>
          <span v-if="sortedSellOrders.length > 3">...</span>
        </span>
      </div>
    </div>

    <!-- 错误信息 -->
    <div v-if="status?.last_error" class="error-row">
      <el-icon><WarningFilled /></el-icon>
      <span class="error-text">{{ status.last_error }}</span>
    </div>

    <!-- 底部参数 -->
    <div class="params-row">
      <span>持仓:{{ status?.position_count ?? 0 }}/{{ strategy.max_open_positions }}</span>
      <span class="separator">|</span>
      <span>网格:{{ strategy.grid_levels }}</span>
      <span class="separator">|</span>
      <span>买偏:{{ strategy.buy_price_deviation }}%</span>
      <span class="separator">|</span>
      <span>卖偏:{{ strategy.sell_price_deviation }}%</span>
      <span class="separator">|</span>
      <span>间隔:{{ strategy.polling_interval }}s</span>
      <span class="separator">|</span>
      <span>容差:{{ strategy.price_tolerance }}%</span>
    </div>
  </div>
</template>

<style scoped>
.monitor-card {
  width: 480px;
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 1px 8px rgba(0, 0, 0, 0.08);
  padding: 12px 16px;
  min-height: 180px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 15px;
  font-weight: 600;
}

.index {
  color: #909399;
  font-weight: 500;
}

.title-text {
  line-height: 1.2;
}

.symbol-tag {
  margin-left: 4px;
}

.exchange-tag {
  margin-left: 2px;
}

.status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #606266;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot.running {
  background-color: #67c23a;
}

.status-dot.stopped {
  background-color: #f56c6c;
}

.status-text {
  font-size: 13px;
}

.action-btn {
  margin-left: 4px;
}

.time-row {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.exchange-row {
  margin-top: 4px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  font-size: 12px;
}

.exchange-help {
  margin-left: 4px;
  color: #909399;
  cursor: help;
}

.exchange-label {
  color: #909399;
}

.exchange-value {
  color: #606266;
  font-weight: 500;
}

.exchange-value.token {
  font-family: Menlo, Monaco, Consolas, 'Courier New', monospace;
}

.exchange-value.danger {
  color: #e6a23c;
}

.worker-info {
  color: #606266;
  font-weight: 500;
}

.separator {
  margin: 0 6px;
  color: #dcdfe6;
}

.price-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
  padding: 8px 0;
  border-top: 1px solid #ebeef5;
  border-bottom: 1px solid #ebeef5;
}

.price-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.price-item .label {
  font-size: 13px;
  color: #909399;
}

.price-item .value {
  font-size: 14px;
  font-weight: 500;
}

.price-item .value.price {
  font-size: 16px;
  font-weight: 600;
}

.orders-section {
  margin-top: 10px;
}

.order-line {
  font-size: 12px;
  line-height: 1.6;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

.order-line.buy {
  color: #67c23a;
}

.order-line.sell {
  color: #f56c6c;
}

.order-label {
  font-weight: 500;
  flex-shrink: 0;
}

.order-empty {
  color: #c0c4cc;
}

.order-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.order-item {
  white-space: nowrap;
}

.error-row {
  margin-top: 10px;
  padding: 6px 8px;
  background: #fef0f0;
  border-radius: 4px;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  color: #f56c6c;
}

.error-text {
  word-break: break-all;
  line-height: 1.4;
}

.params-row {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid #ebeef5;
  font-size: 11px;
  color: #909399;
  line-height: 1.6;
}

.params-row .separator {
  margin: 0 4px;
}
</style>
