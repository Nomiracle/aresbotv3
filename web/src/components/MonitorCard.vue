<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { InfoFilled, WarningFilled } from '@element-plus/icons-vue'
import { getExchangeOptionsFromCache } from '@/api/account'
import { exchangeColor, exchangeBgColor } from '@/utils/exchangeColor'
import type { StrategyStatus, OrderDetail } from '@/types'

interface MonitorCardStrategy {
  id: number
  name: string
  symbol: string
  exchange: string
  base_order_size: string
  max_open_positions: number
  grid_levels: number
  buy_price_deviation: string
  sell_price_deviation: string
  polling_interval: string
  price_tolerance: string
  stop_loss?: string | null
  stop_loss_delay?: number | null
  max_daily_drawdown?: string | null
  worker_name?: string | null
  strategy_type?: string
  market_close_buffer?: number | null
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

const detailVisible = ref(false)

const isRunning = computed(() => props.status?.is_running ?? false)

const isPolymarket15m = computed(() => props.status?.exchange === 'polymarket_updown15m')

const wsEnabled = computed(() => props.status?.extra_status?.ws_enabled === true)

const clockSeconds = ref(Math.floor(Date.now() / 1000))

const priceDigits = computed(() => (isPolymarket15m.value ? 2 : 8))

// 运行时间计时器
const runTimeSeconds = ref(0)
let runTimeTimer: number | null = null

function updateRunTime() {
  const startTime = props.status?.start_timestamp || props.status?.started_at
  if (startTime) {
    runTimeSeconds.value = Math.floor(clockSeconds.value - startTime)
  }
}

function tickClock() {
  clockSeconds.value = Math.floor(Date.now() / 1000)
  updateRunTime()
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
  const seconds = Math.floor(clockSeconds.value - props.status.updated_at)
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

  const options = getExchangeOptionsFromCache()
  const match = options.find(o => o.value === exchange)
  return match?.label ?? exchange
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

const POLYMARKET_DEFAULT_BUFFER_BY_EXCHANGE: Record<string, number> = {
  polymarket_updown5m: 60,
  polymarket_updown15m: 180,
  polymarket_updown1h: 300,
  polymarket_updown1d: 1800,
}

function resolvePolymarketDefaultBuffer(exchange: string | null | undefined): number {
  if (!exchange) {
    return 0
  }
  return POLYMARKET_DEFAULT_BUFFER_BY_EXCHANGE[exchange] ?? 0
}

const polymarketStatus = computed(() => {
  if (!props.status?.exchange?.startsWith('polymarket_updown')) {
    return null
  }

  const extra = props.status.extra_status ?? {}
  return {
    marketSlug: typeof extra.market_slug === 'string' ? extra.market_slug : '-',
    tokenId: typeof extra.token_id === 'string' ? extra.token_id : '',
    secondsUntilClose: toFiniteNumber(extra.seconds_until_close),
    secondsUntilSwitch: toFiniteNumber(extra.seconds_until_switch),
    marketCloseBuffer: toFiniteNumber(extra.market_close_buffer),
    marketEndTime: toFiniteNumber(extra.market_end_time),
    isClosing: Boolean(extra.is_closing),
  }
})

const polymarketSlugShort = computed(() => {
  const slug = polymarketStatus.value?.marketSlug
  if (!slug || slug === '-') {
    return '-'
  }
  if (slug.length <= 26) {
    return slug
  }
  return `${slug.slice(0, 18)}...${slug.slice(-6)}`
})

const polymarketRemainingSeconds = computed(() => {
  const info = polymarketStatus.value
  if (!info) {
    return null
  }

  if (info.marketEndTime !== null) {
    return Math.max(0, Math.floor(info.marketEndTime - clockSeconds.value))
  }

  return info.secondsUntilClose
})

const polymarketMarketUrl = computed(() => {
  const info = polymarketStatus.value
  if (!info || !info.marketSlug || info.marketSlug === '-') {
    return ''
  }
  return `https://polymarket.com/event/${info.marketSlug}`
})

const polymarketTooltipText = computed(() => {
  const info = polymarketStatus.value
  if (!info) {
    return ''
  }

  return `数据来源：worker 上报的 extra_status（Polymarket 适配器）。\n` +
    `market：当前15分钟市场 slug。\n` +
    `剩余：距离该市场结束的倒计时。\n` +
    `切换：距离策略触发市场切换的倒计时（= 剩余 - 缓冲秒数）。\n` +
    `token：当前交易 outcome 对应的 token_id。`
})

const polymarketSwitchSeconds = computed(() => {
  const info = polymarketStatus.value
  if (!info) {
    return null
  }

  if (info.secondsUntilSwitch !== null) {
    return Math.max(0, Math.floor(info.secondsUntilSwitch))
  }

  const remaining = polymarketRemainingSeconds.value
  if (remaining === null) return null
  const strategyBuffer = toFiniteNumber(props.strategy.market_close_buffer)
  const buffer = strategyBuffer
    ?? info.marketCloseBuffer
    ?? resolvePolymarketDefaultBuffer(props.status?.exchange)
  return Math.max(0, remaining - Math.floor(buffer))
})

const isSwitchCritical = computed(() => {
  const s = polymarketSwitchSeconds.value
  return s !== null && s <= 30
})

const isPolymarketCritical = computed(() => {
  const info = polymarketStatus.value
  if (!info) {
    return false
  }
  const remaining = polymarketRemainingSeconds.value
  return info.isClosing || (remaining !== null && remaining <= 60)
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
  return price.toLocaleString('en-US', { maximumFractionDigits: priceDigits.value })
}

function formatOrderValue(value: number) {
  if (value >= 1000) {
    return `$${(value / 1000).toFixed(2)}K`
  }
  return `$${value.toFixed(2)}`
}

function formatQuantity(quantity: number): string {
  const absQuantity = Math.abs(quantity)
  let maxFractionDigits = 8

  if (absQuantity >= 1000) {
    maxFractionDigits = 2
  } else if (absQuantity >= 1) {
    maxFractionDigits = 4
  }

  return quantity.toLocaleString('en-US', { maximumFractionDigits: maxFractionDigits })
}

function calcPriceOffsetPercent(price: number, anchorPrice: number | null | undefined): number | null {
  if (!Number.isFinite(price) || !Number.isFinite(anchorPrice) || !anchorPrice || anchorPrice <= 0) {
    return null
  }

  return ((price - anchorPrice) / anchorPrice) * 100
}

function formatSignedPercent(value: number, fractionDigits = 3): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(fractionDigits)}%`
}

function formatOrderEntry(order: OrderDetail, currentPrice: number | null | undefined): string {
  const offset = calcPriceOffsetPercent(order.price, currentPrice)
  const offsetText = offset === null ? '-' : formatSignedPercent(offset)
  return `$${formatPrice(order.price)}@${formatQuantity(order.quantity)}（${offsetText}）`
}

function buildOrderDisplay(
  orders: OrderDetail[],
  currentPrice: number | null | undefined
): string[] {
  if (!orders.length) {
    return []
  }

  const formattedOrders = orders.map(order => formatOrderEntry(order, currentPrice))

  if (formattedOrders.length <= 3) {
    return formattedOrders
  }

  return [formattedOrders[0], formattedOrders[1], '...', formattedOrders[formattedOrders.length - 1]]
}

const buyOrderDisplay = computed(() =>
  buildOrderDisplay(sortedBuyOrders.value, props.status?.current_price)
)

const sellOrderDisplay = computed(() =>
  buildOrderDisplay(sortedSellOrders.value, props.status?.current_price)
)

onMounted(() => {
  tickClock()
  runTimeTimer = window.setInterval(tickClock, 1000)
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
        <el-tag v-if="strategy.strategy_type === 'bilateral_grid'" size="small" type="warning" class="symbol-tag">双边</el-tag>
        <el-tag v-if="strategy.strategy_type === 'short_grid'" size="small" type="danger" class="symbol-tag">做空</el-tag>
        <span
          v-if="exchangeLabel && status?.exchange"
          class="exchange-badge"
          :style="{ color: exchangeColor(status.exchange), backgroundColor: exchangeBgColor(status.exchange) }"
        >{{ exchangeLabel }}</span>
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
        <el-button size="small" class="action-btn" @click="detailVisible = true">详情</el-button>
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
      <span class="separator">|</span>
      <span :class="['ws-tag', wsEnabled ? 'ws-on' : 'ws-off']">
        {{ wsEnabled ? 'WS' : 'REST' }}
      </span>
    </div>

    <div v-if="isRunning && polymarketStatus" class="exchange-row">
      <span class="exchange-label">市场:</span>
      <a
        v-if="polymarketMarketUrl"
        class="exchange-value market-link"
        :href="polymarketMarketUrl"
        target="_blank"
        rel="noopener noreferrer"
      >
        {{ polymarketSlugShort }}
      </a>
      <span v-else class="exchange-value">{{ polymarketSlugShort }}</span>
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
        {{ formatCountdown(polymarketRemainingSeconds) }}
      </span>
      <template v-if="polymarketSwitchSeconds !== null">
        <span class="separator">|</span>
        <span class="exchange-label">切换:</span>
        <span :class="['exchange-value', { danger: isSwitchCritical }]">
          {{ formatCountdown(polymarketSwitchSeconds) }}
        </span>
      </template>
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
        <span v-if="!buyOrderDisplay.length" class="order-empty">-</span>
        <span v-else class="order-list">
          <span
            v-for="(item, itemIndex) in buyOrderDisplay"
            :key="`buy-${itemIndex}`"
            class="order-item"
            :class="{ ellipsis: item === '...' }"
          >
            {{ item }}
          </span>
        </span>
      </div>
      <div class="order-line sell">
        <span class="order-label">卖单({{ status?.pending_sells ?? 0 }}):</span>
        <span v-if="!sellOrderDisplay.length" class="order-empty">-</span>
        <span v-else class="order-list">
          <span
            v-for="(item, itemIndex) in sellOrderDisplay"
            :key="`sell-${itemIndex}`"
            class="order-item"
            :class="{ ellipsis: item === '...' }"
          >
            {{ item }}
          </span>
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

    <!-- 策略详情弹窗 -->
    <el-dialog v-model="detailVisible" :title="`策略详情 - ${strategy.name}`" width="420px">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item label="策略ID">{{ strategy.id }}</el-descriptions-item>
        <el-descriptions-item label="策略名称">{{ strategy.name }}</el-descriptions-item>
        <el-descriptions-item label="交易所">{{ exchangeLabel || strategy.exchange }}</el-descriptions-item>
        <el-descriptions-item label="交易对">{{ strategy.symbol }}</el-descriptions-item>
        <el-descriptions-item label="基础订单量">{{ strategy.base_order_size }}</el-descriptions-item>
        <el-descriptions-item label="网格层数">{{ strategy.grid_levels }}</el-descriptions-item>
        <el-descriptions-item label="买入偏差">{{ strategy.buy_price_deviation }}%</el-descriptions-item>
        <el-descriptions-item label="卖出偏差">{{ strategy.sell_price_deviation }}%</el-descriptions-item>
        <el-descriptions-item label="轮询间隔">{{ strategy.polling_interval }}s</el-descriptions-item>
        <el-descriptions-item label="价格容差">{{ strategy.price_tolerance }}%</el-descriptions-item>
        <el-descriptions-item label="最大持仓">{{ strategy.max_open_positions }}</el-descriptions-item>
        <el-descriptions-item label="止损">{{ strategy.stop_loss ? strategy.stop_loss + '%' : '-' }}</el-descriptions-item>
        <el-descriptions-item label="止损延迟">{{ strategy.stop_loss_delay != null ? strategy.stop_loss_delay + 's' : '-' }}</el-descriptions-item>
        <el-descriptions-item label="日最大回撤">{{ strategy.max_daily_drawdown ? strategy.max_daily_drawdown + '%' : '-' }}</el-descriptions-item>
        <el-descriptions-item label="Worker">{{ strategy.worker_name || '自动分配' }}</el-descriptions-item>
        <el-descriptions-item label="数据源">
          <span :class="['ws-tag', wsEnabled ? 'ws-on' : 'ws-off']">{{ wsEnabled ? 'WebSocket' : 'REST' }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>
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

.exchange-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.4;
  white-space: nowrap;
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

.market-link {
  text-decoration: underline;
  color: #409eff;
}

.market-link:hover {
  color: #1e80ff;
}

.exchange-value.danger {
  color: #e6a23c;
}

.worker-info {
  color: #606266;
  font-weight: 500;
}

.ws-tag {
  display: inline-block;
  padding: 0 5px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
}

.ws-tag.ws-on {
  color: #67c23a;
  background: #f0f9eb;
}

.ws-tag.ws-off {
  color: #909399;
  background: #f4f4f5;
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
  gap: 0;
  color: #606266;
  font-weight: 500;
}

.order-item {
  white-space: nowrap;
}

.order-item:not(:last-child)::after {
  content: '|';
  margin: 0 6px;
  color: #dcdfe6;
}

.order-item.ellipsis {
  color: #909399;
  font-weight: 400;
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
