import api from './index'
import type { Strategy, StrategyCreate, StrategyStatus } from '@/types'

export const strategyApi = {
  getAll(): Promise<Strategy[]> {
    return api.get('/strategies')
  },

  getById(id: number): Promise<Strategy> {
    return api.get(`/strategies/${id}`)
  },

  create(data: StrategyCreate): Promise<Strategy> {
    return api.post('/strategies', data)
  },

  update(id: number, data: Partial<StrategyCreate>): Promise<Strategy> {
    return api.put(`/strategies/${id}`, data)
  },

  delete(id: number): Promise<void> {
    return api.delete(`/strategies/${id}`)
  },

  getStatus(id: number): Promise<StrategyStatus> {
    return api.get(`/strategies/${id}/status`)
  },

  start(id: number): Promise<{ message: string }> {
    return api.post(`/strategies/${id}/start`)
  },

  stop(id: number): Promise<{ message: string }> {
    return api.post(`/strategies/${id}/stop`)
  },
}
