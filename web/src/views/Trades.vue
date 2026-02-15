<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import type { Trade, Strategy } from '@/types'
import { tradeApi } from '@/api/trade'
import { strategyApi } from '@/api/strategy'
import { getExchangeOptionsFromCache } from '@/api/account'
import { exchangeColor, exchangeBgColor } from '@/utils/exchangeColor'

const trades = ref<Trade[]>([])
const loading = ref(false)
const total = ref(0)
const pageSize = ref(20)
const currentPage = ref(1)
const expandedRows = ref<number[]>([])
const detailVisible = ref(false)
const detailLoading = ref(false)
const currentTrade = ref<Trade | null>(null)
const currentStrategy = ref<Strategy | null>(null)

async function fetchTrades() {
  loading.value = true
  try {
    const params: { limit: number; offset: number } = {
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value,
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

function formatTime(dateStr: string) {
  return new Date(dateStr).toLocaleString('zh-CN')
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

async function openDetail(row: Trade) {
  detailVisible.value = true
  detailLoading.value = true
  currentTrade.value = row
  currentStrategy.value = null
  try {
    currentStrategy.value = await strategyApi.getById(row.strategy_id)
  } catch {
    currentStrategy.value = null
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detailVisible.value = false
  currentTrade.value = null
  currentStrategy.value = null
}

function formatNullable(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  return String(value)
}

onMounted(() => {
  fetchTrades()
})
</script>

<template>
  <div>
    <div class="page-header">
      <el-row justify="space-between" align="middle">
        <h2>交易记录</h2>
        <div />
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
        <el-table-column prop="grid_index" label="网格索引" min-width="90">
          <template #default="{ row }">
            {{ formatNullable(row.grid_index) }}
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
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="openDetail(row)">详情</el-button>
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

    <el-dialog
      v-model="detailVisible"
      title="交易详情"
      width="900px"
      destroy-on-close
      @closed="closeDetail"
    >
      <el-skeleton v-if="detailLoading" :rows="8" animated />
      <template v-else>
        <el-descriptions v-if="currentTrade" title="交易记录字段" :column="2" border size="small">
          <el-descriptions-item label="交易ID">{{ currentTrade.id }}</el-descriptions-item>
          <el-descriptions-item label="策略ID">{{ currentTrade.strategy_id }}</el-descriptions-item>
          <el-descriptions-item label="订单ID">{{ currentTrade.order_id }}</el-descriptions-item>
          <el-descriptions-item label="关联订单ID">{{ formatNullable(currentTrade.related_order_id) }}</el-descriptions-item>
          <el-descriptions-item label="交易所">{{ formatNullable(currentTrade.exchange) }}</el-descriptions-item>
          <el-descriptions-item label="交易对">{{ currentTrade.symbol }}</el-descriptions-item>
          <el-descriptions-item label="方向">{{ currentTrade.side }}</el-descriptions-item>
          <el-descriptions-item label="网格索引">{{ formatNullable(currentTrade.grid_index) }}</el-descriptions-item>
          <el-descriptions-item label="价格">{{ currentTrade.price }}</el-descriptions-item>
          <el-descriptions-item label="数量">{{ currentTrade.quantity }}</el-descriptions-item>
          <el-descriptions-item label="金额">{{ currentTrade.amount }}</el-descriptions-item>
          <el-descriptions-item label="手续费">{{ currentTrade.fee }}</el-descriptions-item>
          <el-descriptions-item label="盈亏">{{ formatNullable(currentTrade.pnl) }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatTime(currentTrade.created_at) }}</el-descriptions-item>
        </el-descriptions>

        <div class="detail-raw-header">
          <div class="detail-subtitle">原始订单信息 (raw_order_info)</div>
          <el-button
            size="small"
            text
            type="primary"
            :disabled="!currentTrade?.raw_order_info"
            @click="copyRawOrderInfo(currentTrade?.raw_order_info ?? null)"
          >
            复制 JSON
          </el-button>
        </div>
        <pre class="raw-order-json">{{ formatRawOrderInfo(currentTrade?.raw_order_info ?? null) }}</pre>

        <el-descriptions
          title="策略详情"
          :column="2"
          border
          size="small"
          style="margin-top: 16px;"
        >
          <template v-if="currentStrategy">
            <el-descriptions-item label="策略ID">{{ currentStrategy.id }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ currentStrategy.status }}</el-descriptions-item>
            <el-descriptions-item label="策略名称">{{ currentStrategy.name }}</el-descriptions-item>
            <el-descriptions-item label="账户ID">{{ currentStrategy.account_id }}</el-descriptions-item>
            <el-descriptions-item label="交易所">{{ currentStrategy.exchange }}</el-descriptions-item>
            <el-descriptions-item label="交易对">{{ currentStrategy.symbol }}</el-descriptions-item>
            <el-descriptions-item label="基础订单量">{{ currentStrategy.base_order_size }}</el-descriptions-item>
            <el-descriptions-item label="网格层数">{{ currentStrategy.grid_levels }}</el-descriptions-item>
            <el-descriptions-item label="买入偏差">{{ currentStrategy.buy_price_deviation }}</el-descriptions-item>
            <el-descriptions-item label="卖出偏差">{{ currentStrategy.sell_price_deviation }}</el-descriptions-item>
            <el-descriptions-item label="轮询间隔">{{ currentStrategy.polling_interval }}</el-descriptions-item>
            <el-descriptions-item label="价格容差">{{ currentStrategy.price_tolerance }}</el-descriptions-item>
            <el-descriptions-item label="止损">{{ formatNullable(currentStrategy.stop_loss) }}</el-descriptions-item>
            <el-descriptions-item label="止损延迟">{{ formatNullable(currentStrategy.stop_loss_delay) }}</el-descriptions-item>
            <el-descriptions-item label="最大持仓">{{ currentStrategy.max_open_positions }}</el-descriptions-item>
            <el-descriptions-item label="最大日回撤">{{ formatNullable(currentStrategy.max_daily_drawdown) }}</el-descriptions-item>
            <el-descriptions-item label="Worker">{{ formatNullable(currentStrategy.worker_name) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatTime(currentStrategy.created_at) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatTime(currentStrategy.updated_at) }}</el-descriptions-item>
          </template>
          <template v-else>
            <el-descriptions-item label="提示" :span="2">
              策略详情不存在或已无权限访问
            </el-descriptions-item>
          </template>
        </el-descriptions>
      </template>
    </el-dialog>
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

.detail-subtitle {
  font-size: 13px;
  color: #606266;
  font-weight: 500;
}

.detail-raw-header {
  margin-top: 16px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
