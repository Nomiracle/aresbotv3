<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getUserInfo, type UserInfo } from '@/api/user'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const userInfo = ref<UserInfo | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    userInfo.value = await getUserInfo()
    if (userInfo.value?.email) {
      userStore.setEmail(userInfo.value.email)
    }
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="profile-page" v-loading="loading">
    <el-card v-if="userInfo">
      <template #header>
        <div class="card-header">
          <el-icon :size="24"><User /></el-icon>
          <span>用户信息</span>
        </div>
      </template>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="邮箱">{{ userInfo.email }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-row :gutter="20" style="margin-top: 20px" v-if="userInfo">
      <el-col :span="6">
        <el-statistic title="账户数" :value="userInfo.stats.accounts">
          <template #prefix><el-icon><Wallet /></el-icon></template>
        </el-statistic>
      </el-col>
      <el-col :span="6">
        <el-statistic title="策略数" :value="userInfo.stats.strategies">
          <template #prefix><el-icon><TrendCharts /></el-icon></template>
        </el-statistic>
      </el-col>
      <el-col :span="6">
        <el-statistic title="交易数" :value="userInfo.stats.trades">
          <template #prefix><el-icon><List /></el-icon></template>
        </el-statistic>
      </el-col>
      <el-col :span="6">
        <el-statistic
          title="总盈亏"
          :value="userInfo.stats.total_pnl"
          :precision="2"
          :value-style="{ color: userInfo.stats.total_pnl >= 0 ? '#67c23a' : '#f56c6c' }"
        >
          <template #prefix><el-icon><Coin /></el-icon></template>
        </el-statistic>
      </el-col>
    </el-row>

    <el-card style="margin-top: 20px">
      <template #header>操作</template>
      <el-button type="danger" @click="userStore.logout">退出登录</el-button>
    </el-card>
  </div>
</template>

<style scoped>
.profile-page {
  padding: 20px;
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 500;
}
</style>
