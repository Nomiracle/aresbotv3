import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Dashboard',
      component: () => import('@/views/Dashboard.vue'),
    },
    {
      path: '/monitor',
      name: 'Monitor',
      component: () => import('@/views/Monitor.vue'),
    },
    {
      path: '/accounts',
      name: 'Accounts',
      component: () => import('@/views/Accounts.vue'),
    },
    {
      path: '/strategies',
      name: 'Strategies',
      component: () => import('@/views/Strategies.vue'),
    },
    {
      path: '/trades',
      name: 'Trades',
      component: () => import('@/views/Trades.vue'),
    },
  ],
})

export default router
