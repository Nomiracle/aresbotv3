<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Strategy, StrategyCreate, StrategyStatus, Trade } from '@/types'
import { strategyApi } from '@/api/strategy'
import { tradeApi } from '@/api/trade'
import { getWorkers, type WorkerInfo } from '@/api/worker'
import StrategyForm from '@/components/StrategyForm.vue'
import TradeTable from '@/components/TradeTable.vue'

const strategies = ref<Strategy[]>([])
const statusMap = ref<Map<number, StrategyStatus>>(new Map())
const loading = ref(false)
const drawerVisible = ref(false)
const selectedId = ref<number | null>(null)
const currentStrategy = ref<Strategy | null>(null)
const recentTrades = ref<Trade[]>([])
const tradesLoading = ref(false)

// Worker 选择
const workers = ref<WorkerInfo[]>([])
const selectedWorker = ref<string>('')
const startDialogVisible = ref(false)

const selectedStrategy = computed(() => {
  if (selectedId.value === null) return null
  return strategies.value.find(s => s.id === selectedId.value) || null
})

const selectedStatus = computed(() => {
  if (selectedId.value === null) return null
  return statusMap.value.get(selectedId.value) || null
})

async function fetchStrategies() {
  loading.value = true
  try {
    strategies.value = await strategyApi.getAll()
    if (strategies.value.length > 0 && selectedId.value === null) {
      selectedId.value = strategies.value[0].id
    }
  } finally {
    loading.value = false
  }
}

async function fetchStatus(id: number) {
  try {
    const status = await strategyApi.getStatus(id)
    statusMap.value.set(id, status)
  } catch {
    // 忽略错误
  }
}

async function fetchAllStatus() {
  await Promise.all(strategies.value.map(s => fetchStatus(s.id)))
}

async function fetchRecentTrades(strategyId: number) {
  tradesLoading.value = true
  try {
    recentTrades.value = await tradeApi.getAll({ strategy_id: strategyId, limit: 10 })
  } finally {
    tradesLoading.value = false
  }
}

watch(selectedId, (id) => {
  if (id !== null) {
    fetchStatus(id)
    fetchRecentTrades(id)
  }
})

function handleAdd() {
  currentStrategy.value = null
  drawerVisible.value = true
}

function handleEdit() {
  currentStrategy.value = selectedStrategy.value
  drawerVisible.value = true
}

async function handleDelete() {
  if (!selectedStrategy.value) return
  try {
    await ElMessageBox.confirm(
      `确定要删除策略 "${selectedStrategy.value.name}" 吗？`,
      '删除确认',
      { type: 'warning' }
    )
    await strategyApi.delete(selectedStrategy.value.id)
    ElMessage.success('删除成功')
    selectedId.value = null
    fetchStrategies()
  } catch {
    // 用户取消
  }
}

async function handleSubmit(data: StrategyCreate) {
  try {
    if (currentStrategy.value) {
      await strategyApi.update(currentStrategy.value.id, data)
      ElMessage.success('更新成功')
    } else {
      const newStrategy = await strategyApi.create(data)
      selectedId.value = newStrategy.id
      ElMessage.success('创建成功')
    }
    fetchStrategies()
  } catch {
    // 错误已在拦截器处理
  }
}

async function handleStart() {
  if (!selectedStrategy.value) return
  try {
    workers.value = await getWorkers()
    selectedWorker.value = ''
    startDialogVisible.value = true
  } catch {
    // 错误已在拦截器处理
  }
}

async function confirmStart() {
  if (!selectedStrategy.value) return
  try {
    await strategyApi.start(selectedStrategy.value.id, selectedWorker.value || undefined)
    ElMessage.success('策略已启动')
    startDialogVisible.value = false
    fetchStatus(selectedStrategy.value.id)
  } catch {
    // 错误已在拦截器处理
  }
}

async function handleStop() {
  if (!selectedStrategy.value) return
  try {
    await strategyApi.stop(selectedStrategy.value.id)
    ElMessage.success('策略已停止')
    fetchStatus(selectedStrategy.value.id)
  } catch {
    // 错误已在拦截器处理
  }
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

onMounted(() => {
  fetchStrategies().then(() => fetchAllStatus())
})
</script>

<template>
  <div>
    <div class="page-header">
      <el-row justify="space-between" align="middle">
        <h2>策略管理</h2>
        <el-button type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon>
          新建策略
        </el-button>
      </el-row>
    </div>

    <el-card>
      <el-row :gutter="20" style="height: 600px;">
        <el-col :span="8">
          <div class="strategy-list">
            <div
              v-for="strategy in strategies"
              :key="strategy.id"
              :class="['item', { active: selectedId === strategy.id }]"
              @click="selectedId = strategy.id"
            >
              <div style="font-weight: 600; margin-bottom: 4px;">{{ strategy.name }}</div>
              <div style="font-size: 12px; color: #909399;">
                {{ strategy.symbol }}
              </div>
              <el-tag
                :type="getStatusType(strategy.id)"
                size="small"
                style="margin-top: 8px;"
              >
                {{ getStatusText(strategy.id) }}
              </el-tag>
            </div>
            <el-empty v-if="strategies.length === 0" description="暂无策略" />
          </div>
        </el-col>
        <el-col :span="16">
          <div v-if="selectedStrategy" class="strategy-detail">
            <el-descriptions :column="2" border>
              <el-descriptions-item label="策略名称">{{ selectedStrategy.name }}</el-descriptions-item>
              <el-descriptions-item label="交易对">{{ selectedStrategy.symbol }}</el-descriptions-item>
              <el-descriptions-item label="基础订单量">{{ selectedStrategy.base_order_size }}</el-descriptions-item>
              <el-descriptions-item label="网格层数">{{ selectedStrategy.grid_levels }}</el-descriptions-item>
              <el-descriptions-item label="买入偏差">{{ selectedStrategy.buy_price_deviation }}%</el-descriptions-item>
              <el-descriptions-item label="卖出偏差">{{ selectedStrategy.sell_price_deviation }}%</el-descriptions-item>
              <el-descriptions-item label="轮询间隔">{{ selectedStrategy.polling_interval }}s</el-descriptions-item>
              <el-descriptions-item label="价格容差">{{ selectedStrategy.price_tolerance }}%</el-descriptions-item>
              <el-descriptions-item label="最大持仓">{{ selectedStrategy.max_open_positions }}</el-descriptions-item>
              <el-descriptions-item label="止损">{{ selectedStrategy.stop_loss || '未设置' }}</el-descriptions-item>
            </el-descriptions>

            <div style="margin-top: 16px;">
              <el-button @click="handleEdit">编辑</el-button>
              <el-button
                v-if="selectedStatus && !selectedStatus.is_running"
                type="success"
                @click="handleStart"
              >
                启动
              </el-button>
              <el-button
                v-if="selectedStatus && selectedStatus.is_running"
                type="warning"
                @click="handleStop"
              >
                停止
              </el-button>
              <el-button type="danger" @click="handleDelete">删除</el-button>
            </div>

            <el-divider>最近交易</el-divider>
            <TradeTable :trades="recentTrades" :loading="tradesLoading" />
          </div>
          <el-empty v-else description="请选择一个策略" />
        </el-col>
      </el-row>
    </el-card>

    <StrategyForm
      v-model:visible="drawerVisible"
      :strategy="currentStrategy"
      @submit="handleSubmit"
    />

    <el-dialog v-model="startDialogVisible" title="启动策略" width="400">
      <el-form>
        <el-form-item label="选择 Worker">
          <el-select v-model="selectedWorker" placeholder="自动分配" clearable style="width: 100%">
            <el-option
              v-for="(w, idx) in workers"
              :key="w.name"
              :label="`${w.hostname} (#${idx + 1})`"
              :value="w.name"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="startDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmStart">启动</el-button>
      </template>
    </el-dialog>
  </div>
</template>
