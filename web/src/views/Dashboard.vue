<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import * as echarts from 'echarts'
import type { TradeStats, Strategy, StrategyStatus } from '@/types'
import { tradeApi } from '@/api/trade'
import { strategyApi } from '@/api/strategy'

const stats = ref<TradeStats | null>(null)
const strategies = ref<Strategy[]>([])
const statusMap = ref<Map<number, StrategyStatus>>(new Map())
const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

const runningCount = computed(() => {
  let count = 0
  statusMap.value.forEach(s => {
    if (s.is_running) count++
  })
  return count
})

async function fetchStats() {
  stats.value = await tradeApi.getStats(7)
}

async function fetchStrategies() {
  strategies.value = await strategyApi.getAll()
  const newMap = new Map<number, StrategyStatus>()
  for (const s of strategies.value) {
    if (s.runtime_status) {
      newMap.set(s.id, s.runtime_status)
    }
  }
  statusMap.value = newMap
}

function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value)
  
  // 模拟数据，实际应该从后端获取每日盈亏数据
  const dates = []
  const pnlData = []
  for (let i = 6; i >= 0; i--) {
    const date = new Date()
    date.setDate(date.getDate() - i)
    dates.push(`${date.getMonth() + 1}/${date.getDate()}`)
    pnlData.push((Math.random() - 0.3) * 100)
  }

  const option = {
    tooltip: {
      trigger: 'axis',
    },
    xAxis: {
      type: 'category',
      data: dates,
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        formatter: '${value}',
      },
    },
    series: [
      {
        name: '盈亏',
        type: 'line',
        data: pnlData,
        smooth: true,
        areaStyle: {
          opacity: 0.3,
        },
        itemStyle: {
          color: '#409eff',
        },
      },
    ],
  }
  chart.setOption(option)
}

function getStatusType(id: number) {
  const status = statusMap.value.get(id)
  if (!status) return 'info'
  return status.is_running ? 'success' : 'danger'
}

function getStatusText(id: number) {
  const status = statusMap.value.get(id)
  if (!status) return '未知'
  return status.is_running ? '运行中' : '已停止'
}

onMounted(async () => {
  await Promise.all([fetchStats(), fetchStrategies()])
  initChart()
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>概览</h2>
    </div>

    <el-row :gutter="20">
      <el-col :span="6">
        <div class="stat-card">
          <div class="label">7日总盈亏</div>
          <div 
            class="value" 
            :class="stats && parseFloat(stats.total_pnl) >= 0 ? 'positive' : 'negative'"
          >
            {{ stats ? `${parseFloat(stats.total_pnl) >= 0 ? '+' : ''}$${stats.total_pnl}` : '-' }}
          </div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="label">总交易次数</div>
          <div class="value">{{ stats?.total_trades ?? '-' }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="label">胜率</div>
          <div class="value">{{ stats ? `${(stats.win_rate * 100).toFixed(1)}%` : '-' }}</div>
        </div>
      </el-col>
      <el-col :span="6">
        <div class="stat-card">
          <div class="label">运行策略数</div>
          <div class="value">{{ runningCount }}/{{ strategies.length }}</div>
        </div>
      </el-col>
    </el-row>

    <el-card style="margin-top: 20px;">
      <template #header>
        <span>盈亏趋势（近7日）</span>
      </template>
      <div ref="chartRef" style="height: 300px;"></div>
    </el-card>

    <el-card style="margin-top: 20px;">
      <template #header>
        <span>策略状态</span>
      </template>
      <el-table :data="strategies" stripe>
        <el-table-column prop="name" label="策略名称" />
        <el-table-column prop="symbol" label="交易对" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.id)" size="small">
              {{ getStatusText(row.id) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="持仓数" width="100">
          <template #default="{ row }">
            {{ statusMap.get(row.id)?.position_count ?? 0 }}/{{ row.max_open_positions }}
          </template>
        </el-table-column>
        <el-table-column label="挂单" width="120">
          <template #default="{ row }">
            <span style="color: #67c23a;">买{{ statusMap.get(row.id)?.pending_buys ?? 0 }}</span>
            /
            <span style="color: #f56c6c;">卖{{ statusMap.get(row.id)?.pending_sells ?? 0 }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>
