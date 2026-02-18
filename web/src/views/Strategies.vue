<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type {
  Strategy,
  StrategyCreate,
  StrategyStatus,
  StrategyStatusFilter,
  StrategyUpdate,
} from '@/types'
import { strategyApi } from '@/api/strategy'
import { getWorkersFromCache, refreshWorkersCache, type WorkerInfo } from '@/api/worker'
import { getExchangeOptionsFromCache } from '@/api/account'
import { exchangeColor, exchangeBgColor } from '@/utils/exchangeColor'
import StrategyForm from '@/components/StrategyForm.vue'
import BatchUpdateDialog from '@/components/BatchUpdateDialog.vue'
import EditableCell from '@/components/EditableCell.vue'
import type { SelectOption } from '@/components/EditableCell.vue'

const strategies = ref<Strategy[]>([])
const statusMap = ref<Map<number, StrategyStatus>>(new Map())
const loading = ref(false)
const drawerVisible = ref(false)
const batchUpdateVisible = ref(false)
const selectedIds = ref<number[]>([])
const currentStrategy = ref<Strategy | null>(null)
const searchKeyword = ref('')
const strategyStatusFilter = ref<StrategyStatusFilter>('active')
type StrategyRuntimeFilter = 'all' | 'running' | 'stopped' | 'deleted'
const runtimeStatusFilter = ref<StrategyRuntimeFilter>('all')
const workers = ref<WorkerInfo[]>([])
const refreshingWorkers = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const pageSizeOptions = [10, 20, 50, 100]
const WORKER_FILTER_ALL = '__all__'
const WORKER_FILTER_AUTO = '__auto__'
const workerFilter = ref<string>(WORKER_FILTER_ALL)
const strategyStatusOptions: Array<{ label: string; value: StrategyStatusFilter }> = [
  { label: '活跃策略', value: 'active' },
  { label: '已删除策略', value: 'deleted' },
  { label: '全部策略', value: 'all' },
]
const runtimeStatusOptions: Array<{ label: string; value: StrategyRuntimeFilter }> = [
  { label: '全部状态', value: 'all' },
  { label: '运行中', value: 'running' },
  { label: '已停止', value: 'stopped' },
  { label: '已删除', value: 'deleted' },
]

// 内联编辑状态
const editingCell = ref<{ rowId: number; field: string } | null>(null)

const sortedStrategies = computed(() => (
  [...strategies.value].sort((a, b) => b.id - a.id)
))

const filteredStrategies = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  return sortedStrategies.value.filter((strategy) => {
    if (!matchesRuntimeStatusFilter(strategy)) return false
    if (!matchesWorkerFilter(strategy)) return false
    if (!kw) return true
    return (
      strategy.name.toLowerCase().includes(kw) ||
      strategy.symbol.toLowerCase().includes(kw)
    )
  })
})

const totalStrategies = computed(() => filteredStrategies.value.length)

const paginatedStrategies = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredStrategies.value.slice(start, start + pageSize.value)
})

const serialOffset = computed(() => (currentPage.value - 1) * pageSize.value)

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

const workerFilterOptions = computed<SelectOption[]>(() => {
  const options: SelectOption[] = [
    { label: '全部 Worker', value: WORKER_FILTER_ALL },
    { label: '自动分配', value: WORKER_FILTER_AUTO },
  ]
  const names = new Set<string>()
  const labelMap = new Map<string, string>()

  workerOptions.value.forEach((option) => {
    names.add(option.value)
    labelMap.set(option.value, option.label)
  })

  strategies.value.forEach((strategy) => {
    const workerName = resolveWorkerName(strategy)
    if (workerName) {
      names.add(workerName)
    }
  })

  const sortedNames = [...names].sort((a, b) => a.localeCompare(b))
  sortedNames.forEach((name) => {
    options.push({
      label: labelMap.get(name) ?? name,
      value: name,
    })
  })

  return options
})

function isRunning(id: number): boolean {
  return statusMap.value.get(id)?.is_running ?? false
}

function isDeleted(strategy: Strategy): boolean {
  return strategy.status === 'deleted'
}

function resolveWorkerName(strategy: Strategy): string | null {
  return statusMap.value.get(strategy.id)?.worker_name ?? strategy.worker_name ?? null
}

function matchesRuntimeStatusFilter(strategy: Strategy): boolean {
  if (runtimeStatusFilter.value === 'all') return true
  if (runtimeStatusFilter.value === 'deleted') return isDeleted(strategy)
  if (isDeleted(strategy)) return false
  return runtimeStatusFilter.value === 'running' ? isRunning(strategy.id) : !isRunning(strategy.id)
}

function matchesWorkerFilter(strategy: Strategy): boolean {
  if (workerFilter.value === WORKER_FILTER_ALL) return true
  const workerName = resolveWorkerName(strategy)
  if (workerFilter.value === WORKER_FILTER_AUTO) return !workerName
  return workerName === workerFilter.value
}

function getExchangeLabel(exchangeId: string): string {
  const options = getExchangeOptionsFromCache()
  const match = options.find(o => o.value === exchangeId)
  return match?.label ?? exchangeId
}

function isEditing(rowId: number, field: string): boolean {
  return editingCell.value?.rowId === rowId && editingCell.value?.field === field
}

function startEdit(rowId: number, field: string) {
  const strategy = strategies.value.find(s => s.id === rowId)
  if (strategy && isDeleted(strategy)) {
    ElMessage.warning('已删除策略不支持编辑')
    return
  }
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
  if (isDeleted(row)) {
    return 'row-deleted'
  }
  return isRunning(row.id) ? 'row-running' : ''
}

function handlePageChange(page: number) {
  currentPage.value = page
  selectedIds.value = []
}

function handlePageSizeChange(size: number) {
  pageSize.value = size
  currentPage.value = 1
  selectedIds.value = []
}

// 数据获取
async function fetchStrategies() {
  loading.value = true
  try {
    strategies.value = await strategyApi.getAll(strategyStatusFilter.value)
    selectedIds.value = []
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
  statusMap.value = new Map()
  const activeStrategies = strategies.value.filter(s => !isDeleted(s))
  await Promise.all(activeStrategies.map(s => fetchStatus(s.id)))
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

function getStatusType(strategy: Strategy) {
  if (isDeleted(strategy)) return 'info'
  return isRunning(strategy.id) ? 'success' : 'danger'
}

function getStatusText(strategy: Strategy) {
  if (isDeleted(strategy)) return '已删除'
  return isRunning(strategy.id) ? '运行中' : '已停止'
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
    ElMessage.success('已归档到已删除策略')
    selectedIds.value = selectedIds.value.filter(id => id !== row.id)
    await fetchStrategies()
    await fetchAllStatus()
  } catch {}
}

async function handleSubmit(data: StrategyCreate | StrategyUpdate) {
  try {
    if (currentStrategy.value) {
      await strategyApi.update(currentStrategy.value.id, data as StrategyUpdate)
      ElMessage.success('更新成功')
    } else {
      await strategyApi.create(data as StrategyCreate)
      ElMessage.success('创建成功')
    }
    await fetchStrategies()
    await fetchAllStatus()
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
    await fetchStrategies()
    await fetchAllStatus()
  } catch {}
}

async function handleBatchStart() {
  if (strategyStatusFilter.value !== 'active') return
  if (selectedIds.value.length === 0) return
  try {
    const result = await strategyApi.batchStart(selectedIds.value)
    ElMessage.success(`启动成功: ${result.success.length}, 失败: ${result.failed.length}`)
    fetchAllStatus()
  } catch {}
}

async function handleBatchStop() {
  if (strategyStatusFilter.value !== 'active') return
  if (selectedIds.value.length === 0) return
  try {
    const result = await strategyApi.batchStop(selectedIds.value)
    ElMessage.success(`停止成功: ${result.success.length}, 失败: ${result.failed.length}`)
    fetchAllStatus()
  } catch {}
}

async function handleBatchDelete() {
  if (strategyStatusFilter.value !== 'active') return
  if (selectedIds.value.length === 0) return
  try {
    await ElMessageBox.confirm(`确定要删除选中的 ${selectedIds.value.length} 个策略吗？`, '批量删除', { type: 'warning' })
    const result = await strategyApi.batchDelete(selectedIds.value)
    ElMessage.success(`删除成功: ${result.success.length}, 失败: ${result.failed.length}`)
    selectedIds.value = []
    await fetchStrategies()
    await fetchAllStatus()
  } catch {}
}

async function handleBatchUpdate(update: StrategyUpdate) {
  if (selectedIds.value.length === 0) return
  try {
    const result = await strategyApi.batchUpdate(selectedIds.value, update)
    ElMessage.success(`修改成功: ${result.success.length}, 失败: ${result.failed.length}`)
    await fetchStrategies()
    await fetchAllStatus()
  } catch {}
}

watch([searchKeyword, runtimeStatusFilter, workerFilter], () => {
  currentPage.value = 1
  selectedIds.value = []
})

watch(strategyStatusFilter, () => {
  currentPage.value = 1
  selectedIds.value = []
  fetchStrategies()
    .then(() => fetchAllStatus())
    .catch(() => {
      // 错误已由拦截器处理
    })
})

watch([totalStrategies, pageSize], ([total, size]) => {
  const maxPage = Math.max(1, Math.ceil(total / size))
  if (currentPage.value > maxPage) {
    currentPage.value = maxPage
    selectedIds.value = []
  }
})

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
          <el-select v-model="strategyStatusFilter" style="width: 140px">
            <el-option
              v-for="option in strategyStatusOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-select v-model="runtimeStatusFilter" style="width: 120px">
            <el-option
              v-for="option in runtimeStatusOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-select v-model="workerFilter" filterable style="width: 220px">
            <el-option
              v-for="option in workerFilterOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-input v-model="searchKeyword" placeholder="搜索策略" clearable style="width: 200px" />
          <el-button :loading="refreshingWorkers" @click="handleRefreshWorkers">刷新 Worker</el-button>
          <el-button type="primary" @click="handleAdd">新建</el-button>
        </el-space>
      </el-row>
    </div>

	    <el-card>
	      <div style="margin-bottom: 12px;">
	        <el-space>
	          <el-button size="small" :disabled="selectedIds.length === 0 || strategyStatusFilter !== 'active'" @click="handleBatchStart">批量启动</el-button>
	          <el-button size="small" :disabled="selectedIds.length === 0 || strategyStatusFilter !== 'active'" @click="handleBatchStop">批量停止</el-button>
	          <el-button size="small" type="danger" :disabled="selectedIds.length === 0 || strategyStatusFilter !== 'active'" @click="handleBatchDelete">批量删除</el-button>
	          <el-button size="small" type="warning" :disabled="selectedIds.length === 0 || strategyStatusFilter !== 'active'" @click="batchUpdateVisible = true">批量修改</el-button>
	          <span style="color: #909399; font-size: 12px;">已选: {{ selectedIds.length }}</span>
	        </el-space>
	      </div>

	      <div style="overflow-x: auto;">
	        <el-table
	        :data="paginatedStrategies"
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
        <el-table-column label="编号" width="70">
          <template #default="{ $index }">
            {{ serialOffset + $index + 1 }}
          </template>
        </el-table-column>

        <el-table-column label="名称" min-width="120">
          <template #default="{ row }">
            <EditableCell
              :value="row.name"
              :editing="isEditing(row.id, 'name')"
              :disabled="isRunning(row.id) || isDeleted(row)"
              @start="startEdit(row.id, 'name')"
              @save="(v: any) => saveEdit(row.id, 'name', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="交易所" min-width="100">
          <template #default="{ row }">
            <span
              v-if="row.exchange"
              class="exchange-badge"
              :style="{ color: exchangeColor(row.exchange), backgroundColor: exchangeBgColor(row.exchange) }"
            >{{ getExchangeLabel(row.exchange) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="交易对" min-width="100">
          <template #default="{ row }">
            <EditableCell
              :value="row.symbol"
              :editing="isEditing(row.id, 'symbol')"
              :disabled="isRunning(row.id) || isDeleted(row)"
              @start="startEdit(row.id, 'symbol')"
              @save="(v: any) => saveEdit(row.id, 'symbol', v)"
              @cancel="cancelEdit"
            />
          </template>
        </el-table-column>

        <el-table-column label="状态" min-width="70">
          <template #default="{ row }">
            <el-tag :type="getStatusType(row)" size="small">{{ getStatusText(row) }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="订单量" min-width="80">
          <template #default="{ row }">
            <EditableCell
              :value="row.base_order_size"
              :editing="isEditing(row.id, 'base_order_size')"
              :disabled="isRunning(row.id) || isDeleted(row)"
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
              :disabled="isRunning(row.id) || isDeleted(row)"
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
              :disabled="isRunning(row.id) || isDeleted(row)"
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
              :disabled="isRunning(row.id) || isDeleted(row)"
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
              :disabled="isRunning(row.id) || isDeleted(row)"
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
              :disabled="isRunning(row.id) || isDeleted(row)"
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
              :disabled="isRunning(row.id) || isDeleted(row)"
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
              :disabled="isRunning(row.id) || isDeleted(row)"
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
              :disabled="isRunning(row.id) || isDeleted(row)"
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
            <template v-if="!isDeleted(row)">
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
            <span v-else style="color: #909399;">-</span>
          </template>
        </el-table-column>
	        </el-table>
	      </div>

        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :total="totalStrategies"
            :page-sizes="pageSizeOptions"
            layout="total, sizes, prev, pager, next, jumper"
            background
            @current-change="handlePageChange"
            @size-change="handlePageSizeChange"
          />
        </div>
	    </el-card>

    <StrategyForm v-model:visible="drawerVisible" :strategy="currentStrategy" @submit="handleSubmit" />
    <BatchUpdateDialog v-model:visible="batchUpdateVisible" :selected-count="selectedIds.length" @submit="handleBatchUpdate" />
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

:deep(.row-running) {
  background-color: #f0f9eb !important;
}
:deep(.row-running:hover > td) {
  background-color: #e8f5e1 !important;
}

:deep(.row-deleted) {
  background-color: #f5f7fa !important;
  color: #909399;
}

.pagination-wrap {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
