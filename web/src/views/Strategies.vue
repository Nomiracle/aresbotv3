<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Strategy, StrategyCreate, StrategyStatus } from '@/types'
import { strategyApi } from '@/api/strategy'
import { getWorkersFromCache, refreshWorkersCache, type WorkerInfo } from '@/api/worker'
import StrategyForm from '@/components/StrategyForm.vue'
import EditableCell from '@/components/EditableCell.vue'
import type { SelectOption } from '@/components/EditableCell.vue'

const strategies = ref<Strategy[]>([])
const statusMap = ref<Map<number, StrategyStatus>>(new Map())
const loading = ref(false)
const drawerVisible = ref(false)
const selectedIds = ref<number[]>([])
const currentStrategy = ref<Strategy | null>(null)
const searchKeyword = ref('')
const workers = ref<WorkerInfo[]>([])
const refreshingWorkers = ref(false)

// 内联编辑状态
const editingCell = ref<{ rowId: number; field: string } | null>(null)

const filteredStrategies = computed(() => {
  if (!searchKeyword.value) return strategies.value
  const kw = searchKeyword.value.toLowerCase()
  return strategies.value.filter(s =>
    s.name.toLowerCase().includes(kw) ||
    s.symbol.toLowerCase().includes(kw) ||
    s.id.toString().includes(kw)
  )
})

const workerOptions = computed<SelectOption[]>(() =>
  workers.value.map((w, idx) => {
    const publicIp = w.public_ip || w.ip || ''
    const privateIp = w.private_ip || ''
    const location = w.ip_location || ''
    const concurrency = Number(w.concurrency || 0)
    const activeTasks = Number(w.active_tasks || 0)
    const availableConcurrency = Math.max(concurrency - activeTasks, 0)

    const details: string[] = []
    if (concurrency > 0) {
      details.push(`可用并发:${availableConcurrency}/${concurrency}`)
    }
    if (publicIp) {
      details.push(`出口IP:${publicIp}`)
    }
    if (location) {
      details.push(location)
    }
    if (privateIp && privateIp !== publicIp) {
      details.push(`内网:${privateIp}`)
    }

    return {
      label: `#${idx + 1} ${w.hostname}${details.length ? ` (${details.join(' | ')})` : ''}`,
      value: w.name,
    }
  })
)

function isRunning(id: number): boolean {
  return statusMap.value.get(id)?.is_running ?? false
}

function isEditing(rowId: number, field: string): boolean {
  return editingCell.value?.rowId === rowId && editingCell.value?.field === field
}

function startEdit(rowId: number, field: string) {
  if (isRunning(rowId)) {
    ElMessage.warning('运行中的策略不能内联编辑，请先停止')
    return
  }
  editingCell.value = { rowId, field }
}

async function saveEdit(rowId: number, field: string, newValue: string | number | null) {
  const strategy = strategies.value.find(s => s.id === rowId)
  if (!strategy) return

  const oldValue = (strategy as any)[field]
  // 没有变化则直接关闭
  if (String(newValue ?? '') === String(oldValue ?? '')) {
    cancelEdit()
    return
  }

  // 乐观更新
  ;(strategy as any)[field] = newValue
  cancelEdit()

  try {
    await strategyApi.update(rowId, { [field]: newValue })
    ElMessage.success('已保存')
  } catch {
    // 回滚
    ;(strategy as any)[field] = oldValue
    ElMessage.error('保存失败')
  }
}

function cancelEdit() {
  editingCell.value = null
}

function getRowClassName({ row }: { row: Strategy }): string {
  return isRunning(row.id) ? 'row-running' : ''
}

// 数据获取
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
    // 忽略
  }
}

async function fetchAllStatus() {
  await Promise.all(strategies.value.map(s => fetchStatus(s.id)))
}

function loadWorkersFromCache() {
  workers.value = getWorkersFromCache()
}

async function handleRefreshWorkers() {
  refreshingWorkers.value = true
  try {
    workers.value = await refreshWorkersCache()
    ElMessage.success('Worker 列表已刷新')
  } catch {
    loadWorkersFromCache()
  } finally {
    refreshingWorkers.value = false
  }
}

function getStatusType(id: number) {
  return isRunning(id) ? 'success' : 'danger'
}

function getStatusText(id: number) {
  return isRunning(id) ? '运行中' : '已停止'
}

// 操作
function handleAdd() {
  currentStrategy.value = null
  drawerVisible.value = true
}

function handleEdit(row: Strategy) {
  currentStrategy.value = row
  drawerVisible.value = true
}

async function handleDelete(row: Strategy) {
  try {
    await ElMessageBox.confirm(`确定要删除策略 "${row.name}" 吗？`, '删除确认', { type: 'warning' })
    await strategyApi.delete(row.id)
    ElMessage.success('删除成功')
    selectedIds.value = selectedIds.value.filter(id => id !== row.id)
    fetchStrategies()
  } catch {}
}

async function handleSubmit(data: StrategyCreate) {
  try {
    if (currentStrategy.value) {
      await strategyApi.update(currentStrategy.value.id, data)
      ElMessage.success('更新成功')
    } else {
      await strategyApi.create(data)
      ElMessage.success('创建成功')
    }
    fetchStrategies()
  } catch {}
}

async function handleStart(row: Strategy) {
  try {
    await strategyApi.start(row.id)
    ElMessage.success('策略已启动')
    fetchStatus(row.id)
  } catch {}
}

async function handleStop(row: Strategy) {
  try {
    await strategyApi.stop(row.id)
    ElMessage.success('策略已停止')
    fetchStatus(row.id)
  } catch {}
}

async function handleCopy(row: Strategy) {
  try {
    await strategyApi.copy(row.id)
    ElMessage.success('复制成功')
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

onMounted(() => {
  fetchStrategies()
    .then(() => fetchAllStatus())
    .catch(() => {
      // 错误已由拦截器处理
    })

  loadWorkersFromCache()
})
</script>

<template>
  <div>
    <div class="page-header">
      <el-row justify="space-between" align="middle">
        <h2>策略管理</h2>
        <el-space>
          <el-input v-model="searchKeyword" placeholder="搜索策略" clearable style="width: 200px" />
          <el-button :loading="refreshingWorkers" @click="handleRefreshWorkers">刷新 Worker</el-button>
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

	      <div style="overflow-x: auto;">
	        <el-table
	        :data="filteredStrategies"
	        v-loading="loading"
	        size="small"
	        :height="'calc(100vh - 220px)'"
	        style="width: 100%"
	        stripe
	        border
	        :row-class-name="getRowClassName"
	        @selection-change="(rows: Strategy[]) => selectedIds = rows.map(r => r.id)"
	        >
        <el-table-column type="selection" width="40" />
        <el-table-column prop="id" label="ID" width="50" />

        <el-table-column label="名称" min-width="120">
          <template #default="{ row }">
            <EditableCell
              :value="row.name"
              :editing="isEditing(row.id, 'name')"
              :disabled="isRunning(row.id)"
              @start="startEdit(row.id, 'name')"
              @save="(v: any) => saveEdit(row.id, 'name', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="交易对" min-width="100">
          <template #default="{ row }">
            <EditableCell
              :value="row.symbol"
              :editing="isEditing(row.id, 'symbol')"
              :disabled="isRunning(row.id)"
              @start="startEdit(row.id, 'symbol')"
              @save="(v: any) => saveEdit(row.id, 'symbol', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="状态" min-width="70">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.id)" size="small">{{ getStatusText(row.id) }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="订单量" min-width="80">
          <template #default="{ row }">
            <EditableCell
              :value="row.base_order_size"
              :editing="isEditing(row.id, 'base_order_size')"
              :disabled="isRunning(row.id)"
              @start="startEdit(row.id, 'base_order_size')"
              @save="(v: any) => saveEdit(row.id, 'base_order_size', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="买偏差%" min-width="75">
          <template #default="{ row }">
            <EditableCell
              :value="row.buy_price_deviation"
              :editing="isEditing(row.id, 'buy_price_deviation')"
              :disabled="isRunning(row.id)"
              suffix="%"
              @start="startEdit(row.id, 'buy_price_deviation')"
              @save="(v: any) => saveEdit(row.id, 'buy_price_deviation', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="卖偏差%" min-width="75">
          <template #default="{ row }">
            <EditableCell
              :value="row.sell_price_deviation"
              :editing="isEditing(row.id, 'sell_price_deviation')"
              :disabled="isRunning(row.id)"
              suffix="%"
              @start="startEdit(row.id, 'sell_price_deviation')"
              @save="(v: any) => saveEdit(row.id, 'sell_price_deviation', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="网格层" min-width="65">
          <template #default="{ row }">
            <EditableCell
              :value="row.grid_levels"
              :editing="isEditing(row.id, 'grid_levels')"
              :disabled="isRunning(row.id)"
              type="number"
              :min="1"
              :max="20"
              @start="startEdit(row.id, 'grid_levels')"
              @save="(v: any) => saveEdit(row.id, 'grid_levels', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="轮询s" min-width="65">
          <template #default="{ row }">
            <EditableCell
              :value="row.polling_interval"
              :editing="isEditing(row.id, 'polling_interval')"
              :disabled="isRunning(row.id)"
              suffix="s"
              @start="startEdit(row.id, 'polling_interval')"
              @save="(v: any) => saveEdit(row.id, 'polling_interval', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="容差%" min-width="65">
          <template #default="{ row }">
            <EditableCell
              :value="row.price_tolerance"
              :editing="isEditing(row.id, 'price_tolerance')"
              :disabled="isRunning(row.id)"
              suffix="%"
              @start="startEdit(row.id, 'price_tolerance')"
              @save="(v: any) => saveEdit(row.id, 'price_tolerance', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="最大持仓" min-width="75">
          <template #default="{ row }">
            <EditableCell
              :value="row.max_open_positions"
              :editing="isEditing(row.id, 'max_open_positions')"
              :disabled="isRunning(row.id)"
              type="number"
              :min="1"
              :max="100"
              @start="startEdit(row.id, 'max_open_positions')"
              @save="(v: any) => saveEdit(row.id, 'max_open_positions', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="止损%" min-width="65">
          <template #default="{ row }">
            <EditableCell
              :value="row.stop_loss"
              :editing="isEditing(row.id, 'stop_loss')"
              :disabled="isRunning(row.id)"
              suffix="%"
              nullable
              @start="startEdit(row.id, 'stop_loss')"
              @save="(v: any) => saveEdit(row.id, 'stop_loss', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="Worker" min-width="90">
          <template #default="{ row }">
            <EditableCell
              :value="row.worker_name"
              :editing="isEditing(row.id, 'worker_name')"
              :disabled="isRunning(row.id)"
              type="select"
              :options="workerOptions"
              nullable
              @start="startEdit(row.id, 'worker_name')"
              @save="(v: any) => saveEdit(row.id, 'worker_name', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!isRunning(row.id)"
              type="success"
              link
              size="small"
              @click="handleStart(row)"
            >启动</el-button>
            <el-button
              v-else
              type="warning"
              link
              size="small"
              @click="handleStop(row)"
            >停止</el-button>
            <el-button link size="small" @click="handleEdit(row)">编辑</el-button>
            <el-button link size="small" @click="handleCopy(row)">复制</el-button>
            <el-button type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
	        </el-table>
	      </div>
	    </el-card>

    <StrategyForm v-model:visible="drawerVisible" :strategy="currentStrategy" @submit="handleSubmit" />
  </div>
</template>

<style scoped>
:deep(.row-running) {
  background-color: #f0f9eb !important;
}
:deep(.row-running:hover > td) {
  background-color: #e8f5e1 !important;
}
</style>
