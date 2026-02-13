<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Trade, Strategy } from '@/types'
import { tradeApi } from '@/api/trade'
import { strategyApi } from '@/api/strategy'
import { getExchangeOptionsFromCache } from '@/api/account'
import { exchangeColor, exchangeBgColor } from '@/utils/exchangeColor'

const trades = ref<Trade[]>([])
const strategies = ref<Strategy[]>([])
const loading = ref(false)
const total = ref(0)
const pageSize = ref(20)
const currentPage = ref(1)
const selectedStrategy = ref<number | ''>('')
const expandedRows = ref<number[]>([])

async function fetchStrategies() {
  strategies.value = await strategyApi.getAll()
}

async function fetchTrades() {
  loading.value = true
  try {
    const params: { strategy_id?: number; limit: number; offset: number } = {
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value,
    }
    if (selectedStrategy.value !== '') {
      params.strategy_id = selectedStrategy.value
    }
    const result = await tradeApi.getAll(params)
    trades.value = result.items
    total.value = result.total
  } catch {
    trades.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function handlePageChange(page: number) {
  currentPage.value = page
  fetchTrades()
}

function handleSizeChange(size: number) {
  pageSize.value = size
  currentPage.value = 1
  fetchTrades()
}

function handleStrategyChange() {
  currentPage.value = 1
  fetchTrades()
}

function formatTime(dateStr: string) {
  return new Date(dateStr).toLocaleString('zh-CN')
}

function getStrategyName(id: number) {
  const strategy = strategies.value.find(s => s.id === id)
  return strategy?.name || '-'
}

function getExchangeLabel(exchangeId: string): string {
  const options = getExchangeOptionsFromCache()
  const match = options.find(o => o.value === exchangeId)
  return match?.label ?? exchangeId
}

function getRelatedBuyTrade(sellTrade: Trade): Trade | null {
  if (!sellTrade.related_order_id) return null
  return trades.value.find(t =>
    t.order_id === sellTrade.related_order_id && t.side === 'BUY'
  ) || null
}

function stringifyRawOrderInfo(rawOrderInfo: Trade['raw_order_info']): string | null {
  if (!rawOrderInfo) return null
  try {
    return JSON.stringify(rawOrderInfo, null, 2)
  } catch {
    return null
  }
}

function formatRawOrderInfo(rawOrderInfo: Trade['raw_order_info']): string {
  const text = stringifyRawOrderInfo(rawOrderInfo)
  if (text) {
    return text
  }
  return rawOrderInfo ? '原始订单信息格式化失败' : '暂无原始订单信息'
}

async function copyRawOrderInfo(rawOrderInfo: Trade['raw_order_info']) {
  const text = stringifyRawOrderInfo(rawOrderInfo)
  if (!text) {
    ElMessage.warning('没有可复制的 JSON')
    return
  }

  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('JSON 已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败，请检查浏览器剪贴板权限')
  }
}

function handleExpandChange(_row: Trade, expandedRowsList: Trade[]) {
  expandedRows.value = expandedRowsList.map(r => r.id)
}

watch(selectedStrategy, () => {
  handleStrategyChange()
})

onMounted(() => {
  fetchStrategies()
  fetchTrades()
})
</script>

<template>
  <div>
    <div class="page-header">
      <el-row justify="space-between" align="middle">
        <h2>交易记录</h2>
        <el-select
          v-model="selectedStrategy"
          placeholder="全部策略"
          clearable
          style="width: 200px;"
        >
          <el-option
            v-for="s in strategies"
            :key="s.id"
            :label="s.name"
            :value="s.id"
          />
        </el-select>
      </el-row>
    </div>

    <el-card>
      <el-table
        :data="trades"
        v-loading="loading"
        stripe
        row-key="id"
        :expand-row-keys="expandedRows.map(String)"
        @expand-change="handleExpandChange"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="expand-content">
              <div v-if="row.side === 'SELL' || row.side === 'sell'">
                <template v-if="getRelatedBuyTrade(row)">
                  <p class="expand-title">关联买入订单</p>
                  <el-descriptions :column="4" border size="small">
                    <el-descriptions-item label="时间">{{ formatTime(getRelatedBuyTrade(row)!.created_at) }}</el-descriptions-item>
                    <el-descriptions-item label="价格">{{ getRelatedBuyTrade(row)!.price }}</el-descriptions-item>
                    <el-descriptions-item label="数量">{{ getRelatedBuyTrade(row)!.quantity }}</el-descriptions-item>
                    <el-descriptions-item label="金额">{{ getRelatedBuyTrade(row)!.amount }}</el-descriptions-item>
                    <el-descriptions-item label="订单ID">
                      <span class="order-id-text">{{ getRelatedBuyTrade(row)!.order_id }}</span>
                    </el-descriptions-item>
                    <el-descriptions-item label="手续费">{{ getRelatedBuyTrade(row)!.fee }}</el-descriptions-item>
                  </el-descriptions>
                </template>
                <template v-else-if="row.related_order_id">
                  <p class="expand-title">关联买入订单: <span class="order-id-text">{{ row.related_order_id }}</span>（不在当前页）</p>
                </template>
                <template v-else>
                  <p class="expand-empty">无关联买入订单</p>
                </template>
              </div>

              <div class="raw-order-header">
                <p class="expand-title raw-order-title">原始订单信息 (JSON)</p>
                <el-button
                  size="small"
                  text
                  type="primary"
                  :disabled="!row.raw_order_info"
                  @click="copyRawOrderInfo(row.raw_order_info)"
                >
                  复制 JSON
                </el-button>
              </div>
              <pre class="raw-order-json">{{ formatRawOrderInfo(row.raw_order_info) }}</pre>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="created_at" label="时间" min-width="160">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column prop="strategy_id" label="策略" min-width="120">
          <template #default="{ row }">
            {{ getStrategyName(row.strategy_id) }}
          </template>
        </el-table-column>
        <el-table-column prop="order_id" label="订单ID" min-width="140">
          <template #default="{ row }">
            <span class="order-id-text">{{ row.order_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="exchange" label="交易所" min-width="100">
          <template #default="{ row }">
            <span
              v-if="row.exchange"
              class="exchange-badge"
              :style="{ color: exchangeColor(row.exchange), backgroundColor: exchangeBgColor(row.exchange) }"
            >{{ getExchangeLabel(row.exchange) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="symbol" label="交易对" min-width="100" />
        <el-table-column prop="side" label="方向" min-width="70">
          <template #default="{ row }">
            <el-tag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">
              {{ row.side === 'BUY' ? '买入' : '卖出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="price" label="价格" min-width="100" />
        <el-table-column prop="quantity" label="数量" min-width="100" />
        <el-table-column prop="amount" label="金额" min-width="100" />
        <el-table-column prop="fee" label="手续费" min-width="90" />
        <el-table-column prop="pnl" label="盈亏" min-width="90">
          <template #default="{ row }">
            <span v-if="row.pnl" :style="{ color: parseFloat(row.pnl) >= 0 ? '#67c23a' : '#f56c6c' }">
              {{ parseFloat(row.pnl) >= 0 ? '+' : '' }}{{ row.pnl }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        style="margin-top: 20px; justify-content: center;"
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />
    </el-card>
  </div>
</template>

<style scoped>
.exchange-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.4;
  white-space: nowrap;
}

.order-id-text {
  font-size: 12px;
  color: #909399;
  font-family: Menlo, Monaco, Consolas, 'Courier New', monospace;
}

.expand-content {
  padding: 8px 16px;
}

.expand-title {
  font-size: 13px;
  color: #606266;
  margin-bottom: 8px;
  font-weight: 500;
}

.expand-empty {
  font-size: 13px;
  color: #909399;
}

.raw-order-title {
  margin-top: 12px;
  margin-bottom: 0;
}

.raw-order-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.raw-order-json {
  margin: 0;
  padding: 10px 12px;
  max-height: 260px;
  overflow: auto;
  border-radius: 6px;
  background: #f5f7fa;
  color: #303133;
  font-size: 12px;
  line-height: 1.5;
  font-family: Menlo, Monaco, Consolas, 'Courier New', monospace;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
