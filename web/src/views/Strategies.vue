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
const selectedIds = ref<number[]>([])
const currentStrategy = ref<Strategy | null>(null)
const recentTrades = ref<Trade[]>([])
const tradesLoading = ref(false)
const searchKeyword = ref('')

// Worker 选择
const workers = ref<WorkerInfo[]>([])
const selectedWorker = ref<string>('')
const startDialogVisible = ref(false)
const startingStrategyId = ref<number | null>(null)

const filteredStrategies = computed(() => {
  if (!searchKeyword.value) return strategies.value
  const kw = searchKeyword.value.toLowerCase()
  return strategies.value.filter(s =>
    s.name.toLowerCase().includes(kw) ||
    s.symbol.toLowerCase().includes(kw) ||
    s.id.toString().includes(kw)
  )
})

const selectedStrategy = computed(() => {
  if (selectedIds.value.length !== 1) return null
  return strategies.value.find(s => s.id === selectedIds.value[0]) || null
})

const selectedStatus = computed(() => {
  if (!selectedStrategy.value) return null
  return statusMap.value.get(selectedStrategy.value.id) || null
})

async function fetchStrategies() {
  loading.value = true
  try {
    strategies.value = await strategyApi.getAll()
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

watch(() => selectedIds.value, (ids) => {
  if (ids.length === 1) {
    fetchStatus(ids[0])
    fetchRecentTrades(ids[0])
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
    await ElMessageBox.confirm(`确定要删除策略 "${selectedStrategy.value.name}" 吗？`, '删除确认', { type: 'warning' })
    await strategyApi.delete(selectedStrategy.value.id)
    ElMessage.success('删除成功')
    selectedIds.value = []
    fetchStrategies()
  } catch {}
}

async function handleSubmit(data: StrategyCreate) {
  try {
    if (currentStrategy.value) {
      await strategyApi.update(currentStrategy.value.id, data)
      ElMessage.success('更新成功')
    } else {
      const newStrategy = await strategyApi.create(data)
      selectedIds.value = [newStrategy.id]
      ElMessage.success('创建成功')
    }
    fetchStrategies()
  } catch {}
}

async function handleStart() {
  if (!selectedStrategy.value) return
  try {
    workers.value = await getWorkers()
    selectedWorker.value = selectedStrategy.value.worker_name || ''
    startingStrategyId.value = selectedStrategy.value.id
    startDialogVisible.value = true
  } catch {}
}

async function confirmStart() {
  if (!startingStrategyId.value) return
  try {
    await strategyApi.start(startingStrategyId.value, selectedWorker.value || undefined)
    ElMessage.success('策略已启动')
    startDialogVisible.value = false
    fetchStatus(startingStrategyId.value)
  } catch {}
}

async function handleStop() {
  if (!selectedStrategy.value) return
  try {
    await strategyApi.stop(selectedStrategy.value.id)
    ElMessage.success('策略已停止')
    fetchStatus(selectedStrategy.value.id)
  } catch {}
}

async function handleCopy() {
  if (!selectedStrategy.value) return
  try {
    const newStrategy = await strategyApi.copy(selectedStrategy.value.id)
    ElMessage.success('复制成功')
    selectedIds.value = [newStrategy.id]
    fetchStrategies()
  } catch {}
}

async function handleBatchStart() {
  if (selectedIds.value.length === 0) return
  try {
    const result = await strategyApi.batchStart(selectedIds.value)
    ElMessage.success(`启动成功: ${result.success.length}, 失败: ${result.failed.length}`)
    fetchAllStatus()
  } catch {}
}

async function handleBatchStop() {
  if (selectedIds.value.length === 0) return
  try {
    const result = await strategyApi.batchStop(selectedIds.value)
    ElMessage.success(`停止成功: ${result.success.length}, 失败: ${result.failed.length}`)
    fetchAllStatus()
  } catch {}
}

async function handleBatchDelete() {
  if (selectedIds.value.length === 0) return
  try {
    await ElMessageBox.confirm(`确定要删除选中的 ${selectedIds.value.length} 个策略吗？`, '批量删除', { type: 'warning' })
    const result = await strategyApi.batchDelete(selectedIds.value)
    ElMessage.success(`删除成功: ${result.success.length}, 失败: ${result.failed.length}`)
    selectedIds.value = []
    fetchStrategies()
  } catch {}
}

function getStatusType(id: number) {
  const status = statusMap.value.get(id)
  return status?.is_running ? 'success' : 'danger'
}

function getStatusText(id: number) {
  const status = statusMap.value.get(id)
  return status?.is_running ? '运行中' : '已停止'
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
        <el-space>
          <el-input v-model="searchKeyword" placeholder="搜索策略" clearable style="width: 200px" />
          <el-button type="primary" @click="handleAdd">新建</el-button>
        </el-space>
      </el-row>
    </div>

    <el-card>
      <div style="margin-bottom: 12px;">
        <el-space>
          <el-button size="small" :disabled="selectedIds.length === 0" @click="handleBatchStart">批量启动</el-button>
          <el-button size="small" :disabled="selectedIds.length === 0" @click="handleBatchStop">批量停止</el-button>
          <el-button size="small" type="danger" :disabled="selectedIds.length === 0" @click="handleBatchDelete">批量删除</el-button>
          <span style="color: #909399; font-size: 12px;">已选: {{ selectedIds.length }}</span>
        </el-space>
      </div>

      <el-row :gutter="16" style="height: 500px;">
        <el-col :span="10">
          <el-table
            :data="filteredStrategies"
            v-loading="loading"
            size="small"
            height="100%"
            highlight-current-row
            @selection-change="(rows: Strategy[]) => selectedIds = rows.map(r => r.id)"
          >
            <el-table-column type="selection" width="40" />
            <el-table-column label="#" width="50">
              <template #default="{ $index }">{{ $index + 1 }}</template>
            </el-table-column>
            <el-table-column prop="name" label="名称" min-width="100" />
            <el-table-column prop="symbol" label="交易对" width="100" />
            <el-table-column label="状态" width="70">
              <template #default="{ row }">
                <el-tag :type="getStatusType(row.id)" size="small">{{ getStatusText(row.id) }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-col>

        <el-col :span="14">
          <div v-if="selectedStrategy" class="detail-panel">
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item label="ID">{{ selectedStrategy.id }}</el-descriptions-item>
              <el-descriptions-item label="交易对">{{ selectedStrategy.symbol }}</el-descriptions-item>
              <el-descriptions-item label="订单量">{{ selectedStrategy.base_order_size }}</el-descriptions-item>
              <el-descriptions-item label="网格">{{ selectedStrategy.grid_levels }}层</el-descriptions-item>
              <el-descriptions-item label="买入偏差">{{ selectedStrategy.buy_price_deviation }}%</el-descriptions-item>
              <el-descriptions-item label="卖出偏差">{{ selectedStrategy.sell_price_deviation }}%</el-descriptions-item>
              <el-descriptions-item label="轮询间隔">{{ selectedStrategy.polling_interval }}s</el-descriptions-item>
              <el-descriptions-item label="最大持仓">{{ selectedStrategy.max_open_positions }}</el-descriptions-item>
              <el-descriptions-item label="Worker">{{ selectedStrategy.worker_name || '自动' }}</el-descriptions-item>
              <el-descriptions-item label="止损">{{ selectedStrategy.stop_loss || '-' }}</el-descriptions-item>
            </el-descriptions>

            <div style="margin-top: 12px;">
              <el-space>
                <el-button size="small" @click="handleEdit">编辑</el-button>
                <el-button size="small" @click="handleCopy">复制</el-button>
                <el-button v-if="!selectedStatus?.is_running" size="small" type="success" @click="handleStart">启动</el-button>
                <el-button v-if="selectedStatus?.is_running" size="small" type="warning" @click="handleStop">停止</el-button>
                <el-button size="small" type="danger" @click="handleDelete">删除</el-button>
              </el-space>
            </div>

            <el-divider content-position="left">最近交易</el-divider>
            <TradeTable :trades="recentTrades" :loading="tradesLoading" />
          </div>
          <el-empty v-else description="请选择一个策略" />
        </el-col>
      </el-row>
    </el-card>

    <StrategyForm v-model:visible="drawerVisible" :strategy="currentStrategy" @submit="handleSubmit" />

    <el-dialog v-model="startDialogVisible" title="启动策略" width="400">
      <el-form>
        <el-form-item label="选择 Worker">
          <el-select v-model="selectedWorker" placeholder="自动分配" clearable style="width: 100%">
            <el-option v-for="(w, idx) in workers" :key="w.name" :label="`${w.hostname} (#${idx + 1})`" :value="w.name" />
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

<style scoped>
.detail-panel {
  height: 100%;
  overflow-y: auto;
}
</style>
