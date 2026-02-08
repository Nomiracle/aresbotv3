<script setup lang="ts">
import { computed } from 'vue'
import { Monitor, WarningFilled } from '@element-plus/icons-vue'
import type { StrategyStatus } from '@/types'

interface MonitorCardStrategy {
  id: number
  name: string
  symbol: string
  max_open_positions: number
}

const props = defineProps<{
  strategy: MonitorCardStrategy
  status: StrategyStatus | null
  ultraCompact?: boolean
}>()

const emit = defineEmits<{
  (e: 'stop', id: number): void
  (e: 'start', id: number): void
}>()

const isRunning = computed(() => props.status?.is_running ?? false)
const compactMode = computed(() => props.ultraCompact ?? false)

const runTime = computed(() => {
  const startTime = props.status?.start_timestamp || props.status?.started_at
  if (!startTime) return '-'
  const seconds = Math.floor(Date.now() / 1000 - startTime)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${minutes}m`
})

const priceDeviation = computed(() => {
  if (!props.status?.current_price || !props.status?.target_price) return null
  const dev = ((props.status.current_price - props.status.target_price) / props.status.target_price) * 100
  return dev.toFixed(2)
})

function formatPrice(price: number | null | undefined) {
  if (price === null || price === undefined) return '-'
  return price.toLocaleString('en-US', { maximumFractionDigits: 8 })
}

function formatPriceList(prices: number[] | undefined) {
  if (!prices || prices.length === 0) return '-'
  const min = Math.min(...prices)
  const max = Math.max(...prices)
  return `$${formatPrice(min)} ~ $${formatPrice(max)}`
}
</script>

<template>
  <div class="monitor-card">
    <div class="card-header">
      <div class="title">
        <span class="title-text">{{ strategy.name }}</span>
        <el-tag size="small" style="margin-left: 8px;">{{ strategy.symbol }}</el-tag>
      </div>
      <div class="status">
        <span :class="['status-dot', isRunning ? 'running' : 'stopped']"></span>
        <span>{{ isRunning ? '运行中' : '已停止' }}</span>
        <span v-if="isRunning" class="run-time">
          运行时长: {{ runTime }}
        </span>
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

    <!-- 显示运行节点信息 -->
    <div v-if="isRunning && status?.worker_ip && !compactMode" class="worker-info">
      <el-icon><Monitor /></el-icon>
      <span>运行节点: {{ status.worker_ip }}</span>
      <el-tag v-if="status?.worker_hostname" size="small" type="info">{{ status.worker_hostname }}</el-tag>
    </div>

    <div class="card-body">
      <div class="price-info" :class="{ compact: compactMode }">
        <div class="info-item">
          <div class="label">当前价格</div>
          <div class="value">${{ formatPrice(status?.current_price) }}</div>
        </div>
        <div class="info-item">
          <div class="label">目标价格</div>
          <div class="value">${{ formatPrice(status?.target_price) }}</div>
        </div>
        <div class="info-item">
          <div class="label">价格偏差</div>
          <div class="value" :class="priceDeviation && parseFloat(priceDeviation) >= 0 ? 'positive' : 'negative'">
            {{ priceDeviation ? `${parseFloat(priceDeviation) >= 0 ? '+' : ''}${priceDeviation}%` : '-' }}
          </div>
        </div>
      </div>

      <div v-if="compactMode" class="compact-summary">
        <span class="summary-item">买单: {{ status?.pending_buys ?? 0 }}</span>
        <span class="summary-item">卖单: {{ status?.pending_sells ?? 0 }}</span>
        <span class="summary-item">持仓: {{ status?.position_count ?? 0 }}/{{ strategy.max_open_positions }}</span>
        <span v-if="status?.worker_hostname" class="summary-item muted">{{ status.worker_hostname }}</span>
      </div>

      <div v-else class="orders-section">
        <div class="order-box buy">
          <div class="box-title">买单挂单</div>
          <div class="box-content">
            <div class="stat-row">
              <span class="label">数量:</span>
              <span class="value">{{ status?.pending_buys ?? 0 }}</span>
            </div>
            <div class="stat-row">
              <span class="label">价格区间:</span>
              <span class="value">{{ formatPriceList(status?.buy_prices) }}</span>
            </div>
            <div class="stat-row">
              <span class="label">平均偏差:</span>
              <span class="value">
                {{ status?.buy_avg_diff_percent !== undefined ? `${status.buy_avg_diff_percent.toFixed(2)}%` : '-' }}
              </span>
            </div>
          </div>
        </div>
        <div class="order-box sell">
          <div class="box-title">卖单挂单</div>
          <div class="box-content">
            <div class="stat-row">
              <span class="label">数量:</span>
              <span class="value">{{ status?.pending_sells ?? 0 }}</span>
            </div>
            <div class="stat-row">
              <span class="label">价格区间:</span>
              <span class="value">{{ formatPriceList(status?.sell_prices) }}</span>
            </div>
            <div class="stat-row">
              <span class="label">平均偏差:</span>
              <span class="value">
                {{ status?.sell_avg_diff_percent !== undefined ? `+${status.sell_avg_diff_percent.toFixed(2)}%` : '-' }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="!compactMode || status?.last_error" class="card-footer" :class="{ compact: compactMode }">
      <div class="footer-item">
        <span class="label">持仓数:</span>
        <span class="value">{{ status?.position_count ?? 0 }}/{{ strategy.max_open_positions }}</span>
      </div>
      <div v-if="status?.last_error" class="footer-item error">
        <el-icon><WarningFilled /></el-icon>
        <span>{{ status.last_error }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.monitor-card {
  background: #fff;
  border-radius: 6px;
  margin-bottom: 12px;
  box-shadow: 0 1px 8px rgba(0, 0, 0, 0.08);
}

.card-header {
  padding: 12px 16px;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.title {
  display: flex;
  align-items: center;
  font-size: 15px;
  font-weight: 600;
}

.title-text {
  line-height: 1.2;
}

.status {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
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

.run-time {
  color: #909399;
}

.action-btn {
  margin-left: 4px;
}

.worker-info {
  padding: 6px 16px;
  background: #f0f9eb;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #67c23a;
  border-bottom: 1px solid #ebeef5;
}

.card-body {
  padding: 14px 16px;
}

.price-info {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 12px;
}

.price-info.compact {
  margin-bottom: 8px;
}

.info-item .label {
  font-size: 11px;
  color: #909399;
  margin-bottom: 2px;
}

.info-item .value {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.2;
}

.info-item .value.positive {
  color: #67c23a;
}

.info-item .value.negative {
  color: #f56c6c;
}

.orders-section {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.compact-summary {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 12px;
  color: #303133;
  flex-wrap: wrap;
}

.summary-item {
  font-weight: 500;
}

.summary-item.muted {
  color: #909399;
  font-weight: 400;
}

.order-box {
  background: #f5f7fa;
  border-radius: 5px;
  padding: 10px 12px;
}

.order-box.buy {
  border-left: 3px solid #67c23a;
}

.order-box.sell {
  border-left: 3px solid #f56c6c;
}

.box-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 8px;
}

.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
}

.stat-row .label {
  color: #909399;
}

.stat-row:last-child {
  margin-bottom: 0;
}

.card-footer {
  padding: 10px 16px;
  background: #fafafa;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  border-radius: 0 0 6px 6px;
}

.card-footer.compact {
  padding: 8px 16px;
}

.footer-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.footer-item .value {
  font-weight: 600;
}

.footer-item.error {
  color: #f56c6c;
}
</style>
