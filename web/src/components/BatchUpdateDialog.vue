<script setup lang="ts">
import { ref, watch } from 'vue'
import type { StrategyUpdate } from '@/types'
import { getWorkersFromCache } from '@/api/worker'

const props = defineProps<{
  visible: boolean
  selectedCount: number
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  submit: [data: StrategyUpdate]
}>()

interface FieldDef {
  key: string
  label: string
  type: 'text' | 'number' | 'select'
  nullable?: boolean
  min?: number
  max?: number
}

const fields: FieldDef[] = [
  { key: 'base_order_size', label: '基础订单量', type: 'text' },
  { key: 'buy_price_deviation', label: '买入偏差 %', type: 'text' },
  { key: 'sell_price_deviation', label: '卖出偏差 %', type: 'text' },
  { key: 'grid_levels', label: '网格层数', type: 'number', min: 1, max: 20 },
  { key: 'polling_interval', label: '轮询间隔(秒)', type: 'text' },
  { key: 'price_tolerance', label: '价格容差 %', type: 'text' },
  { key: 'stop_loss', label: '止损 %', type: 'text', nullable: true },
  { key: 'stop_loss_delay', label: '止损延迟(秒)', type: 'number' },
  { key: 'market_close_buffer', label: '市场切换缓冲(秒)', type: 'number' },
  { key: 'max_open_positions', label: '最大持仓数', type: 'number', min: 1, max: 100 },
  { key: 'max_daily_drawdown', label: '日最大回撤 %', type: 'text', nullable: true },
  { key: 'worker_name', label: '指定 Worker', type: 'select', nullable: true },
]

const checked = ref<Record<string, boolean>>({})
const values = ref<Record<string, any>>({})

const workerOptions = ref<{ label: string; value: string }[]>([])

function reset() {
  checked.value = {}
  values.value = {}
}

watch(() => props.visible, (v) => {
  if (v) {
    reset()
    const workers = getWorkersFromCache()
    workerOptions.value = workers.map((w, idx) => ({
      label: `#${idx + 1} ${w.hostname}`,
      value: w.name,
    }))
  }
})

function handleSubmit() {
  const update: Record<string, any> = {}
  for (const field of fields) {
    if (!checked.value[field.key]) continue
    const val = values.value[field.key]
    if (field.nullable && (val === '' || val === undefined)) {
      update[field.key] = null
    } else {
      update[field.key] = val
    }
  }
  if (Object.keys(update).length === 0) return
  emit('submit', update as StrategyUpdate)
  emit('update:visible', false)
}

function handleClose() {
  emit('update:visible', false)
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="批量修改配置"
    width="480px"
    @close="handleClose"
  >
    <p style="margin-bottom: 16px; color: #909399; font-size: 13px;">
      已选 {{ selectedCount }} 个策略，勾选要修改的字段并填入新值（运行中的策略将被跳过）
    </p>
    <div v-for="field in fields" :key="field.key" style="display: flex; align-items: center; margin-bottom: 12px; gap: 8px;">
      <el-checkbox v-model="checked[field.key]" style="width: 140px;">{{ field.label }}</el-checkbox>
      <template v-if="field.type === 'select'">
        <el-select
          v-model="values[field.key]"
          :disabled="!checked[field.key]"
          :clearable="field.nullable"
          placeholder="自动分配"
          style="flex: 1;"
        >
          <el-option v-for="opt in workerOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
      </template>
      <template v-else-if="field.type === 'number'">
        <el-input-number
          v-model="values[field.key]"
          :disabled="!checked[field.key]"
          :min="field.min"
          :max="field.max"
          controls-position="right"
          style="flex: 1;"
        />
      </template>
      <template v-else>
        <el-input
          v-model="values[field.key]"
          :disabled="!checked[field.key]"
          :placeholder="field.nullable ? '留空清除' : ''"
          style="flex: 1;"
        />
      </template>
    </div>
    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button type="primary" @click="handleSubmit">确定</el-button>
    </template>
  </el-dialog>
</template>