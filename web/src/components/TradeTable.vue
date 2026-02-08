<script setup lang="ts">
import type { Trade } from '@/types'

defineProps<{
  trades: Trade[]
  loading?: boolean
}>()

function formatTime(dateStr: string) {
  return new Date(dateStr).toLocaleString('zh-CN')
}
</script>

<template>
  <el-table :data="trades" v-loading="loading" stripe size="small">
    <el-table-column prop="created_at" label="时间" width="160">
      <template #default="{ row }">
        {{ formatTime(row.created_at) }}
      </template>
    </el-table-column>
    <el-table-column prop="symbol" label="交易对" width="100" />
    <el-table-column prop="side" label="方向" width="80">
      <template #default="{ row }">
        <el-tag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">
          {{ row.side === 'BUY' ? '买入' : '卖出' }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="price" label="价格" width="120" />
    <el-table-column prop="quantity" label="数量" width="100" />
    <el-table-column prop="amount" label="金额" width="120" />
    <el-table-column prop="pnl" label="盈亏" width="100">
      <template #default="{ row }">
        <span v-if="row.pnl" :class="parseFloat(row.pnl) >= 0 ? 'positive' : 'negative'">
          {{ parseFloat(row.pnl) >= 0 ? '+' : '' }}{{ row.pnl }}
        </span>
        <span v-else>-</span>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.positive {
  color: #67c23a;
}
.negative {
  color: #f56c6c;
}
</style>
