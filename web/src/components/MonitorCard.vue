<script setup lang="ts">
import { computed } from 'vue'
import { Monitor } from '@element-plus/icons-vue'
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
}>()

const emit = defineEmits<{
  (e: 'stop', id: number): void
  (e: 'start', id: number): void
}>()

const isRunning = computed(() => props.status?.is_running ?? false)

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
        <span>{{ strategy.name }}</span>
        <el-tag size="small" style="margin-left: 8px;">{{ strategy.symbol }}</el-tag>
      </div>
      <div class="status">
        <span :class="['status-dot', isRunning ? 'running' : 'stopped']"></span>
        <span>{{ isRunning ? '运行中' : '已停止' }}</span>
        <span v-if="isRunning" style="margin-left: 16px; color: #909399;">
          运行时长: {{ runTime }}
        </span>
        <el-button
          v-if="isRunning"
          type="warning"
          size="small"
          style="margin-left: 16px;"
          @click="emit('stop', strategy.id)"
        >
          停止
        </el-button>
        <el-button
          v-else
          type="success"
          size="small"
          style="margin-left: 16px;"
          @click="emit('start', strategy.id)"
        >
          启动
        </el-button>
      </div>
    </div>

    <!-- 显示运行节点信息 -->
    <div v-if="isRunning && status?.worker_ip" class="worker-info">
      <el-icon><Monitor /></el-icon>
      <span>运行节点: {{ status.worker_ip }}</span>
      <el-tag v-if="status?.worker_hostname" size="small" type="info">{{ status.worker_hostname }}</el-tag>
    </div>

    <div class="card-body">
      <div class="price-info">
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

      <div class="orders-section">
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

    <div class="card-footer">
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
  border-radius: 8px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}

.card-header {
  padding: 16px 20px;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.title {
  font-size: 16px;
  font-weight: 600;
}

.status {
  display: flex;
  align-items: center;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 8px;
}

.status-dot.running {
  background-color: #67c23a;
}

.status-dot.stopped {
  background-color: #f56c6c;
}

.worker-info {
  padding: 8px 20px;
  background: #f0f9eb;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #67c23a;
  border-bottom: 1px solid #ebeef5;
}

.card-body {
  padding: 20px;
}

.price-info {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.info-item .label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.info-item .value {
  font-size: 20px;
  font-weight: 600;
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
  gap: 20px;
}

.order-box {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 16px;
}

.order-box.buy {
  border-left: 4px solid #67c23a;
}

.order-box.sell {
  border-left: 4px solid #f56c6c;
}

.box-title {
  font-weight: 600;
  margin-bottom: 12px;
}

.stat-row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.stat-row .label {
  color: #909399;
}

.card-footer {
  padding: 16px 20px;
  background: #fafafa;
  display: flex;
  justify-content: space-between;
  border-radius: 0 0 8px 8px;
}

.footer-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.footer-item.error {
  color: #f56c6c;
}
</style>
