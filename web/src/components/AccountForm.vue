<script setup lang="ts">
import { ref, reactive, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import type { Account, AccountCreate } from '@/types'
import {
  getExchangeOptionsFromCache,
  preloadExchangeOptionsCache,
  refreshExchangeOptionsCache,
  type ExchangeOption,
} from '@/api/account'

const props = defineProps<{
  visible: boolean
  account: Account | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'submit', data: AccountCreate): void
}>()

const formRef = ref<FormInstance>()
const exchanges = ref<ExchangeOption[]>([])
const refreshingExchanges = ref(false)
const form = reactive<AccountCreate>({
  exchange: 'binance',
  label: '',
  api_key: '',
  api_secret: '',
  testnet: false,
})

const rules: FormRules = {
  exchange: [{ required: true, message: '请选择交易所', trigger: 'change' }],
  label: [{ required: true, message: '请输入账户标签', trigger: 'blur' }],
  api_key: [{ required: true, message: '请输入 API Key', trigger: 'blur' }],
  api_secret: [{ required: true, message: '请输入 API Secret', trigger: 'blur' }],
}

function loadExchangesFromCache() {
  const cached = getExchangeOptionsFromCache()
  if (cached.length > 0) {
    exchanges.value = cached
    return
  }

  exchanges.value = [{ value: 'binance', label: 'Binance' }]
}

async function ensureExchangesLoaded() {
  try {
    const loaded = await preloadExchangeOptionsCache()
    if (loaded.length > 0) {
      exchanges.value = loaded
    }
  } catch {
    // 失败时保持缓存或兜底默认值
  }
}

async function handleRefreshExchanges() {
  refreshingExchanges.value = true
  try {
    exchanges.value = await refreshExchangeOptionsCache()
    ElMessage.success('交易所列表已刷新')
  } catch {
    loadExchangesFromCache()
  } finally {
    refreshingExchanges.value = false
  }
}

function defaultExchangeValue(): string {
  return exchanges.value[0]?.value || 'binance'
}

watch(() => props.visible, (val) => {
  if (val && props.account) {
    form.exchange = props.account.exchange
    form.label = props.account.label
    form.api_key = props.account.api_key
    form.api_secret = ''
    form.testnet = props.account.testnet
  } else if (val) {
    form.exchange = defaultExchangeValue()
    form.label = ''
    form.api_key = ''
    form.api_secret = ''
    form.testnet = false
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
  loadExchangesFromCache()
  ensureExchangesLoaded()
})
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="account ? '编辑账户' : '新增账户'"
    width="500px"
    @close="handleClose"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
      <el-form-item label="交易所" prop="exchange">
        <div style="display: flex; width: 100%; gap: 8px; align-items: center;">
          <el-select v-model="form.exchange" style="flex: 1;">
            <el-option
              v-for="ex in exchanges"
              :key="ex.value"
              :label="ex.label"
              :value="ex.value"
            />
          </el-select>
          <el-button :loading="refreshingExchanges" @click="handleRefreshExchanges">刷新</el-button>
        </div>
      </el-form-item>
      <el-form-item label="账户标签" prop="label">
        <el-input v-model="form.label" placeholder="例如：主账户" />
      </el-form-item>
      <el-form-item label="API Key" prop="api_key">
        <el-input v-model="form.api_key" placeholder="输入 API Key" />
      </el-form-item>
      <el-form-item label="API Secret" prop="api_secret">
        <el-input
          v-model="form.api_secret"
          type="password"
          show-password
          :placeholder="account ? '留空表示不修改' : '输入 API Secret'"
        />
      </el-form-item>
      <el-form-item label="测试网络">
        <el-switch v-model="form.testnet" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button type="primary" @click="handleSubmit">确定</el-button>
    </template>
  </el-dialog>
</template>
