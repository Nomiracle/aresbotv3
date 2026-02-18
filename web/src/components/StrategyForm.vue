<script setup lang="ts">
import { computed, ref, reactive, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import type { Strategy, StrategyCreate, StrategyUpdate, Account } from '@/types'
import { accountApi, type TradingFee } from '@/api/account'
import { getWorkersFromCache, refreshWorkersCache, type WorkerInfo } from '@/api/worker'

const symbolsCache = new Map<number, string[]>()
const tradingFeeCache = new Map<string, TradingFee>()
const FUTURES_EXCHANGES = new Set(['binanceusdm', 'binancecoinm'])

const props = defineProps<{
  visible: boolean
  strategy: Strategy | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'submit', data: StrategyCreate | StrategyUpdate): void
}>()

const formRef = ref<FormInstance>()
const accounts = ref<Account[]>([])
const workers = ref<WorkerInfo[]>([])
const refreshingWorkers = ref(false)
const symbols = ref<string[]>([])
const symbolsLoading = ref(false)
const tradingFee = ref<TradingFee | null>(null)
const tradingFeeLoading = ref(false)

const symbolOptions = computed(() => {
  const options = new Set(symbols.value)
  if (form.symbol) {
    options.add(form.symbol)
  }
  return Array.from(options)
})

const selectedAccount = computed(() => {
  return accounts.value.find(item => item.id === form.account_id) || null
})

const isFuturesAccount = computed(() => {
  const exchange = selectedAccount.value?.exchange?.toLowerCase() || ''
  return FUTURES_EXCHANGES.has(exchange)
})

const isPolymarketAccount = computed(() => {
  const exchange = selectedAccount.value?.exchange?.toLowerCase() || ''
  return exchange.startsWith('polymarket')
})

const defaultForm = {
  account_id: undefined as number | undefined,
  name: '',
  symbol: '',
  strategy_type: 'grid',
  base_order_size: '0.01',
  buy_price_deviation: '0.5',
  sell_price_deviation: '1.0',
  grid_levels: 3,
  polling_interval: '1.0',
  price_tolerance: '0.1',
  stop_loss: null as string | null,
  stop_loss_delay: null as number | null,
  market_close_buffer: null as number | null,
  max_open_positions: 10,
  max_daily_drawdown: null as string | null,
  worker_name: null as string | null,
}

const form = reactive({ ...defaultForm })

const rules: FormRules = {
  account_id: [{ required: true, message: '请选择账户', trigger: 'change', type: 'number' }],
  name: [{ required: true, message: '请输入策略名称', trigger: 'blur' }],
  symbol: [{ required: true, message: '请选择交易对', trigger: 'change' }],
  base_order_size: [{ required: true, message: '请输入基础订单量', trigger: 'blur' }],
  buy_price_deviation: [{ required: true, message: '请输入买入偏差', trigger: 'blur' }],
  sell_price_deviation: [{ required: true, message: '请输入卖出偏差', trigger: 'blur' }],
  grid_levels: [{ required: true, message: '请输入网格层数', trigger: 'blur' }],
}

function getTradingFeeCacheKey(accountId: number, symbol: string): string {
  return `${accountId}:${symbol}`
}

async function fetchSymbols(accountId: number) {
  const selectedAccountId = form.account_id
  if (!selectedAccountId || selectedAccountId !== accountId) {
    return
  }

  const cachedSymbols = symbolsCache.get(accountId)
  if (cachedSymbols) {
    symbols.value = cachedSymbols
    return
  }

  symbolsLoading.value = true
  try {
    const data = await accountApi.getSymbols(accountId)
    symbolsCache.set(accountId, data)
    if (form.account_id === accountId) {
      symbols.value = data
    }
  } catch {
    if (form.account_id === accountId) {
      symbols.value = []
    }
  } finally {
    if (form.account_id === accountId) {
      symbolsLoading.value = false
    }
  }
}

async function fetchTradingFee(accountId: number, symbol: string) {
  const cacheKey = getTradingFeeCacheKey(accountId, symbol)
  const cachedFee = tradingFeeCache.get(cacheKey)
  if (cachedFee) {
    tradingFee.value = cachedFee
    return
  }

  tradingFeeLoading.value = true
  try {
    const fee = await accountApi.fetchTradingFee(accountId, symbol)
    tradingFeeCache.set(cacheKey, fee)
    if (form.account_id === accountId && form.symbol === symbol) {
      tradingFee.value = fee
    }
  } catch {
    if (form.account_id === accountId && form.symbol === symbol) {
      tradingFee.value = null
    }
  } finally {
    if (form.account_id === accountId && form.symbol === symbol) {
      tradingFeeLoading.value = false
    }
  }
}

function formatFeeRate(value: number): string {
  return `${(value * 100).toFixed(4)}%`
}

async function fetchAccounts() {
  accounts.value = await accountApi.getAll()
  // 新建时默认选择第一个账户
  if (!props.strategy && accounts.value.length > 0 && !form.account_id) {
    form.account_id = accounts.value[0].id
  }
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

function formatWorkerLabel(worker: WorkerInfo, index: number): string {
  const publicIp = worker.public_ip || worker.ip || ''
  const location = worker.ip_location || ''
  const privateIp = worker.private_ip || ''
  const concurrency = Number(worker.concurrency || 0)
  const activeTasks = Number(worker.active_tasks || 0)
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

  const suffix = details.length ? ` (${details.join(' | ')})` : ''
  return `#${index + 1} ${worker.hostname}${suffix}`
}

watch(() => props.visible, (val) => {
  if (val) {
    fetchAccounts()
    loadWorkersFromCache()
    if (props.strategy) {
      Object.assign(form, {
        account_id: props.strategy.account_id,
        name: props.strategy.name,
        symbol: props.strategy.symbol,
        strategy_type: props.strategy.strategy_type || 'grid',
        base_order_size: props.strategy.base_order_size,
        buy_price_deviation: props.strategy.buy_price_deviation,
        sell_price_deviation: props.strategy.sell_price_deviation,
        grid_levels: props.strategy.grid_levels,
        polling_interval: props.strategy.polling_interval,
        price_tolerance: props.strategy.price_tolerance,
        stop_loss: props.strategy.stop_loss,
        stop_loss_delay: props.strategy.stop_loss_delay,
        market_close_buffer: props.strategy.market_close_buffer,
        max_open_positions: props.strategy.max_open_positions,
        max_daily_drawdown: props.strategy.max_daily_drawdown,
        worker_name: props.strategy.worker_name || null,
      })
    } else {
      Object.assign(form, { ...defaultForm, account_id: undefined, worker_name: null, strategy_type: 'grid' })
    }

    tradingFee.value = null
    tradingFeeLoading.value = false

    if (form.account_id) {
      fetchSymbols(form.account_id)
    } else {
      symbols.value = []
    }
  }
})

watch(
  () => form.account_id,
  async (newAccountId, oldAccountId) => {
    tradingFee.value = null
    tradingFeeLoading.value = false

    if (!newAccountId) {
      symbols.value = []
      form.symbol = ''
      return
    }

    // 切换到非合约账户时重置策略类型
    if (oldAccountId !== undefined && oldAccountId !== newAccountId) {
      symbols.value = []
      const acc = accounts.value.find(a => a.id === newAccountId)
      if (acc && !FUTURES_EXCHANGES.has(acc.exchange?.toLowerCase() || '')) {
        form.strategy_type = 'grid'
      }
    }

    await fetchSymbols(newAccountId)

    if (oldAccountId !== undefined && oldAccountId !== newAccountId && form.symbol) {
      const currentOptions = symbolsCache.get(newAccountId) || symbols.value
      if (!currentOptions.includes(form.symbol)) {
        form.symbol = ''
      }
    }
  }
)

watch(
  () => [form.account_id, form.symbol, props.visible] as const,
  async ([accountId, symbol, visible]) => {
    tradingFee.value = null
    tradingFeeLoading.value = false

    if (!visible || !accountId || !symbol) {
      return
    }

    await fetchTradingFee(accountId, symbol)
  }
)

function handleClose() {
  emit('update:visible', false)
  formRef.value?.resetFields()
}

async function handleSubmit() {
  if (!formRef.value) return
  await formRef.value.validate()
  const payload = { ...form } as StrategyCreate
  if (props.strategy) {
    const { account_id: _accountId, ...updatePayload } = payload
    emit('submit', updatePayload as StrategyUpdate)
  } else {
    emit('submit', payload)
  }
  handleClose()
}

onMounted(() => {
  fetchAccounts()
  loadWorkersFromCache()
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
        <el-select v-model="form.account_id" :disabled="Boolean(strategy)" style="width: 100%">
          <el-option
            v-for="acc in accounts"
            :key="acc.id"
            :label="`${acc.label} (${acc.exchange})`"
            :value="acc.id"
          />
        </el-select>
        <div v-if="strategy" style="margin-top: 8px; font-size: 12px; color: #909399;">
          编辑策略时不支持更换账户，如需更换请新建或复制策略后再调整。
        </div>
      </el-form-item>
      <el-form-item label="策略名称" prop="name">
        <el-input v-model="form.name" placeholder="例如：BTC 网格策略" />
      </el-form-item>
      <el-form-item v-if="isFuturesAccount" label="策略类型">
        <el-select v-model="form.strategy_type" :disabled="Boolean(strategy)" style="width: 100%">
          <el-option label="单边网格" value="grid" />
          <el-option label="双边网格" value="bilateral_grid" />
          <el-option label="做空网格" value="short_grid" />
        </el-select>
        <div
          v-if="['bilateral_grid', 'short_grid'].includes(form.strategy_type)"
          style="margin-top: 8px; font-size: 12px; color: #e6a23c;"
        >
          双边/做空网格需要在交易所开启对冲模式（Hedge Mode），请前往 Binance 合约设置中手动开启。
        </div>
      </el-form-item>
      <el-form-item label="交易对" prop="symbol">
        <el-select
          v-model="form.symbol"
          filterable
          :placeholder="isFuturesAccount ? '搜索或选择合约交易对（如 BTC/USDT:USDT）' : '搜索或选择交易对'"
          :loading="symbolsLoading"
          style="width: 100%"
        >
          <el-option
            v-for="s in symbolOptions"
            :key="s"
            :label="s"
            :value="s"
          />
        </el-select>
        <div
          v-if="isFuturesAccount && !['bilateral_grid', 'short_grid'].includes(form.strategy_type)"
          style="margin-top: 8px; font-size: 12px; color: #e6a23c;"
        >
          合约账户建议使用单向持仓模式（One-way），卖单将按 reduceOnly 提交以避免误开反向仓位。
        </div>
        <div v-if="form.account_id && form.symbol" style="margin-top: 8px; font-size: 12px; color: #606266;">
          <span v-if="tradingFeeLoading">手续费加载中...</span>
          <span v-else-if="tradingFee">
            Maker: {{ formatFeeRate(tradingFee.maker) }}，Taker: {{ formatFeeRate(tradingFee.taker) }}
          </span>
        </div>
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
      <el-form-item :label="['bilateral_grid', 'short_grid'].includes(form.strategy_type) ? '网格层数（每边）' : '网格层数'" prop="grid_levels">
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
      <el-form-item v-if="isPolymarketAccount" label="市场切换缓冲(秒)">
        <el-input-number v-model="form.market_close_buffer" :min="0" />
      </el-form-item>
      <el-form-item label="最大持仓数">
        <el-input-number v-model="form.max_open_positions" :min="1" :max="100" />
      </el-form-item>
      <el-form-item label="日最大回撤 %">
        <el-input v-model="form.max_daily_drawdown" placeholder="留空表示不设置" />
      </el-form-item>
      <el-form-item label="指定 Worker">
        <div style="display: flex; gap: 8px; width: 100%; align-items: center;">
          <el-select v-model="form.worker_name" placeholder="自动分配" clearable style="flex: 1;">
            <el-option
              v-for="(w, idx) in workers"
              :key="w.name"
              :label="formatWorkerLabel(w, idx)"
              :value="w.name"
            />
          </el-select>
          <el-button :loading="refreshingWorkers" @click="handleRefreshWorkers">刷新</el-button>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button type="primary" @click="handleSubmit">确定</el-button>
    </template>
  </el-drawer>
</template>
