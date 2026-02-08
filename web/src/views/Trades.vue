<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import type { Trade, Strategy } from '@/types'
import { tradeApi } from '@/api/trade'
import { strategyApi } from '@/api/strategy'

const trades = ref<Trade[]>([])
const strategies = ref<Strategy[]>([])
const loading = ref(false)
const total = ref(0)
const pageSize = ref(20)
const currentPage = ref(1)
const selectedStrategy = ref<number | ''>('')

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
    trades.value = await tradeApi.getAll(params)
    // 假设后端返回的是全部数据，这里简单处理
    total.value = trades.value.length < pageSize.value ? 
      (currentPage.value - 1) * pageSize.value + trades.value.length : 
      currentPage.value * pageSize.value + 1
  } finally {
    loading.value = false
  }
}

function handlePageChange(page: number) {
  currentPage.value = page
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
      <el-table :data="trades" v-loading="loading" stripe>
        <el-table-column prop="created_at" label="时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column prop="strategy_id" label="策略" width="150">
          <template #default="{ row }">
            {{ getStrategyName(row.strategy_id) }}
          </template>
        </el-table-column>
        <el-table-column prop="symbol" label="交易对" width="120" />
        <el-table-column prop="side" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">
              {{ row.side === 'BUY' ? '买入' : '卖出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="price" label="价格" width="120" />
        <el-table-column prop="quantity" label="数量" width="120" />
        <el-table-column prop="amount" label="金额" width="120" />
        <el-table-column prop="fee" label="手续费" width="100" />
        <el-table-column prop="pnl" label="盈亏" width="100">
          <template #default="{ row }">
            <span v-if="row.pnl" :style="{ color: parseFloat(row.pnl) >= 0 ? '#67c23a' : '#f56c6c' }">
              {{ parseFloat(row.pnl) >= 0 ? '+' : '' }}{{ row.pnl }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="order_id" label="订单ID" min-width="150">
          <template #default="{ row }">
            <span style="font-size: 12px; color: #909399;">{{ row.order_id }}</span>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="currentPage"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        style="margin-top: 20px; justify-content: center;"
        @current-change="handlePageChange"
      />
    </el-card>
  </div>
</template>
