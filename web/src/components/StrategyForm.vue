<script setup lang="ts">
import { ref, reactive, watch, onMounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import type { Strategy, StrategyCreate, Account } from '@/types'
import { accountApi } from '@/api/account'
import { getWorkers, type WorkerInfo } from '@/api/worker'

const props = defineProps<{
  visible: boolean
  strategy: Strategy | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'submit', data: StrategyCreate): void
}>()

const formRef = ref<FormInstance>()
const accounts = ref<Account[]>([])
const workers = ref<WorkerInfo[]>([])

const defaultForm: StrategyCreate = {
  account_id: 0,
  name: '',
  symbol: '',
  base_order_size: '0.01',
  buy_price_deviation: '0.5',
  sell_price_deviation: '1.0',
  grid_levels: 3,
  polling_interval: '1.0',
  price_tolerance: '0.1',
  stop_loss: null,
  stop_loss_delay: null,
  max_open_positions: 10,
  max_daily_drawdown: null,
  worker_name: null,
}

const form = reactive<StrategyCreate>({ ...defaultForm })

const rules: FormRules = {
  account_id: [{ required: true, message: '请选择账户', trigger: 'change' }],
  name: [{ required: true, message: '请输入策略名称', trigger: 'blur' }],
  symbol: [{ required: true, message: '请输入交易对', trigger: 'blur' }],
  base_order_size: [{ required: true, message: '请输入基础订单量', trigger: 'blur' }],
  buy_price_deviation: [{ required: true, message: '请输入买入偏差', trigger: 'blur' }],
  sell_price_deviation: [{ required: true, message: '请输入卖出偏差', trigger: 'blur' }],
  grid_levels: [{ required: true, message: '请输入网格层数', trigger: 'blur' }],
}

async function fetchAccounts() {
  accounts.value = await accountApi.getAll()
}

async function fetchWorkers() {
  try {
    workers.value = await getWorkers()
  } catch {
    workers.value = []
  }
}

watch(() => props.visible, (val) => {
  if (val) {
    fetchAccounts()
    fetchWorkers()
    if (props.strategy) {
      Object.assign(form, {
        account_id: props.strategy.account_id,
        name: props.strategy.name,
        symbol: props.strategy.symbol,
        base_order_size: props.strategy.base_order_size,
        buy_price_deviation: props.strategy.buy_price_deviation,
        sell_price_deviation: props.strategy.sell_price_deviation,
        grid_levels: props.strategy.grid_levels,
        polling_interval: props.strategy.polling_interval,
        price_tolerance: props.strategy.price_tolerance,
        stop_loss: props.strategy.stop_loss,
        stop_loss_delay: props.strategy.stop_loss_delay,
        max_open_positions: props.strategy.max_open_positions,
        max_daily_drawdown: props.strategy.max_daily_drawdown,
        worker_name: props.strategy.worker_name,
      })
    } else {
      Object.assign(form, defaultForm)
    }
  }
})

function handleClose() {
  emit('update:visible', false)
  formRef.value?.resetFields()
}

async function handleSubmit() {
  if (!formRef.value) return
  await formRef.value.validate()
  emit('submit', { ...form })
  handleClose()
}

onMounted(() => {
  fetchAccounts()
  fetchWorkers()
})
</script>

<template>
  <el-drawer
    :model-value="visible"
    :title="strategy ? '编辑策略' : '新建策略'"
    size="500px"
    @close="handleClose"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="120px">
      <el-form-item label="账户" prop="account_id">
        <el-select v-model="form.account_id" style="width: 100%">
          <el-option
            v-for="acc in accounts"
            :key="acc.id"
            :label="`${acc.label} (${acc.exchange})`"
            :value="acc.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="策略名称" prop="name">
        <el-input v-model="form.name" placeholder="例如：BTC 网格策略" />
      </el-form-item>
      <el-form-item label="交易对" prop="symbol">
        <el-input v-model="form.symbol" placeholder="例如：BTC/USDT" />
      </el-form-item>
      <el-divider content-position="left">交易参数</el-divider>
      <el-form-item label="基础订单量" prop="base_order_size">
        <el-input v-model="form.base_order_size" placeholder="0.01" />
      </el-form-item>
      <el-form-item label="买入偏差 %" prop="buy_price_deviation">
        <el-input v-model="form.buy_price_deviation" placeholder="0.5" />
      </el-form-item>
      <el-form-item label="卖出偏差 %" prop="sell_price_deviation">
        <el-input v-model="form.sell_price_deviation" placeholder="1.0" />
      </el-form-item>
      <el-form-item label="网格层数" prop="grid_levels">
        <el-input-number v-model="form.grid_levels" :min="1" :max="20" />
      </el-form-item>
      <el-divider content-position="left">高级设置</el-divider>
      <el-form-item label="轮询间隔(秒)">
        <el-input v-model="form.polling_interval" placeholder="1.0" />
      </el-form-item>
      <el-form-item label="价格容差 %">
        <el-input v-model="form.price_tolerance" placeholder="0.1" />
      </el-form-item>
      <el-form-item label="止损 %">
        <el-input v-model="form.stop_loss" placeholder="留空表示不设置" />
      </el-form-item>
      <el-form-item label="止损延迟(秒)">
        <el-input-number v-model="form.stop_loss_delay" :min="0" />
      </el-form-item>
      <el-form-item label="最大持仓数">
        <el-input-number v-model="form.max_open_positions" :min="1" :max="100" />
      </el-form-item>
      <el-form-item label="日最大回撤 %">
        <el-input v-model="form.max_daily_drawdown" placeholder="留空表示不设置" />
      </el-form-item>
      <el-form-item label="指定 Worker">
        <el-select v-model="form.worker_name" placeholder="自动分配" clearable style="width: 100%">
          <el-option v-for="(w, idx) in workers" :key="w.name" :label="`${w.hostname} (#${idx + 1})`" :value="w.name" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button type="primary" @click="handleSubmit">确定</el-button>
    </template>
  </el-drawer>
</template>
