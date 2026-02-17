<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Account, AccountCreate } from '@/types'
import { accountApi, preloadExchangeOptionsCache, getExchangeOptionsFromCache } from '@/api/account'
import type { ExchangeOption } from '@/api/account'
import { exchangeColor, exchangeBgColor } from '@/utils/exchangeColor'
import AccountForm from '@/components/AccountForm.vue'

const accounts = ref<Account[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const currentAccount = ref<Account | null>(null)

const copyDialogVisible = ref(false)
const copyTargetExchange = ref('')
const copySourceAccount = ref<Account | null>(null)
const copyLoading = ref(false)
const exchangeOptions = ref<ExchangeOption[]>([])

async function fetchAccounts() {
  loading.value = true
  try {
    accounts.value = await accountApi.getAll()
  } finally {
    loading.value = false
  }
}

function handleAdd() {
  currentAccount.value = null
  preloadExchangeOptionsCache().catch(() => {
    // 使用缓存/兜底值继续打开弹窗
  })
  dialogVisible.value = true
}

function getExchangeLabel(exchangeId: string): string {
  const options = getExchangeOptionsFromCache()
  const match = options.find(o => o.value === exchangeId)
  return match?.label ?? exchangeId
}

function handleEdit(account: Account) {
  currentAccount.value = account
  preloadExchangeOptionsCache().catch(() => {
    // 使用缓存/兜底值继续打开弹窗
  })
  dialogVisible.value = true
}

async function handleDelete(account: Account) {
  try {
    await ElMessageBox.confirm(
      `确定要删除账户 "${account.label}" 吗？`,
      '删除确认',
      { type: 'warning' }
    )
    await accountApi.delete(account.id)
    ElMessage.success('删除成功')
    fetchAccounts()
  } catch {
    // 用户取消
  }
}

async function handleSubmit(data: AccountCreate) {
  try {
    if (currentAccount.value) {
      await accountApi.update(currentAccount.value.id, data)
      ElMessage.success('更新成功')
    } else {
      await accountApi.create(data)
      ElMessage.success('创建成功')
    }
    fetchAccounts()
  } catch {
    // 错误已在拦截器处理
  }
}

async function handleCopy(account: Account) {
  copySourceAccount.value = account
  copyTargetExchange.value = ''
  try {
    exchangeOptions.value = await preloadExchangeOptionsCache()
  } catch {
    exchangeOptions.value = getExchangeOptionsFromCache()
  }
  copyDialogVisible.value = true
}

async function submitCopy() {
  if (!copySourceAccount.value || !copyTargetExchange.value) return
  copyLoading.value = true
  try {
    await accountApi.copy(copySourceAccount.value.id, copyTargetExchange.value)
    ElMessage.success('复制成功')
    copyDialogVisible.value = false
    fetchAccounts()
  } catch {
    // 错误已在拦截器处理
  } finally {
    copyLoading.value = false
  }
}

onMounted(() => {
  fetchAccounts()
})
</script>

<template>
  <div>
    <div class="page-header">
      <el-row justify="space-between" align="middle">
        <h2>账户管理</h2>
        <el-button type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon>
          新增账户
        </el-button>
      </el-row>
    </div>

    <el-card>
      <el-table :data="accounts" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="exchange" label="交易所" width="120">
          <template #default="{ row }">
            <span
              class="exchange-badge"
              :style="{ color: exchangeColor(row.exchange), backgroundColor: exchangeBgColor(row.exchange) }"
            >{{ getExchangeLabel(row.exchange) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="label" label="账户标签" />
        <el-table-column prop="api_key" label="API Key">
          <template #default="{ row }">
            <span>{{ row.api_key.slice(0, 8) }}...{{ row.api_key.slice(-4) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="testnet" label="测试网" width="100">
          <template #default="{ row }">
            <el-tag :type="row.testnet ? 'warning' : 'success'">
              {{ row.testnet ? '测试' : '正式' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'">
              {{ row.is_active ? '活跃' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="handleCopy(row)">复制</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <AccountForm
      v-model:visible="dialogVisible"
      :account="currentAccount"
      @submit="handleSubmit"
    />

    <el-dialog v-model="copyDialogVisible" title="复制账户" width="420px">
      <p style="margin-bottom: 12px">
        将账户 <strong>{{ copySourceAccount?.label }}</strong> 的凭证复制到新交易所：
      </p>
      <el-select v-model="copyTargetExchange" placeholder="选择目标交易所" filterable style="width: 100%">
        <el-option
          v-for="opt in exchangeOptions"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <template #footer>
        <el-button @click="copyDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="copyLoading" :disabled="!copyTargetExchange" @click="submitCopy">
          确认复制
        </el-button>
      </template>
    </el-dialog>
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
</style>
