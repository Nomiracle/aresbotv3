import api from './index'
import type {
  RunningStrategy,
  Strategy,
  StrategyCreate,
  StrategyStatus,
  StrategyStatusFilter,
} from '@/types'

interface BatchResult {
  success: number[]
  failed: number[]
}

export const strategyApi = {
  getAll(status: StrategyStatusFilter = 'active'): Promise<Strategy[]> {
    return api.get('/strategies', {
      params: { status },
    })
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

  getRunning(): Promise<RunningStrategy[]> {
    return api.get('/strategies/running')
  },

  start(id: number, workerName?: string): Promise<{ message: string }> {
    return api.post(`/strategies/${id}/start`, workerName ? { worker_name: workerName } : {})
  },

  stop(id: number): Promise<{ message: string }> {
    return api.post(`/strategies/${id}/stop`)
  },

  copy(id: number): Promise<Strategy> {
    return api.post(`/strategies/${id}/copy`)
  },

  batchStart(ids: number[]): Promise<BatchResult> {
    return api.post('/strategies/batch/start', { strategy_ids: ids })
  },

  batchStop(ids: number[]): Promise<BatchResult> {
    return api.post('/strategies/batch/stop', { strategy_ids: ids })
  },

  batchDelete(ids: number[]): Promise<BatchResult> {
    return api.post('/strategies/batch/delete', { strategy_ids: ids })
  },
}
