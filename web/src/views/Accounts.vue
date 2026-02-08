<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Account, AccountCreate } from '@/types'
import { accountApi } from '@/api/account'
import AccountForm from '@/components/AccountForm.vue'

const accounts = ref<Account[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const currentAccount = ref<Account | null>(null)

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
  dialogVisible.value = true
}

function handleEdit(account: Account) {
  currentAccount.value = account
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
            <el-tag>{{ row.exchange.toUpperCase() }}</el-tag>
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
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleEdit(row)">编辑</el-button>
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
  </div>
</template>
