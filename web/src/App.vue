<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { preloadWorkersCache } from '@/api/worker'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const menuItems = [
  { path: '/', title: '概览', icon: 'DataLine' },
  { path: '/monitor', title: '实时监控', icon: 'Monitor' },
  { path: '/accounts', title: '账户管理', icon: 'Wallet' },
  { path: '/strategies', title: '策略管理', icon: 'TrendCharts' },
  { path: '/trades', title: '交易记录', icon: 'List' },
  { path: '/profile', title: '用户信息', icon: 'User' },
]

function handleSelect(path: string) {
  router.push(path)
}

onMounted(() => {
  preloadWorkersCache().catch(() => {
    // Worker 缓存预热失败时保持静默，由业务页面继续读取已有缓存
  })
})
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="logo">AresBot</div>
      <el-menu
        :default-active="route.path"
        background-color="#304156"
        text-color="#bfcbd9"
        active-text-color="#409eff"
        @select="handleSelect"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </aside>
    <div class="main-container">
      <header class="header">
        <el-dropdown>
          <span class="el-dropdown-link">
            {{ userStore.email || '用户' }}
            <el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="userStore.logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </header>
      <main class="main-content">
        <router-view />
      </main>
    </div>
  </div>
</template>
