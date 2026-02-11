import api from './index'
import type { Trade, TradeStats, PaginatedResponse } from '@/types'

export const tradeApi = {
  getAll(params?: {
    strategy_id?: number
    limit?: number
    offset?: number
  }): Promise<PaginatedResponse<Trade>> {
    return api.get('/trades', { params })
  },

  getStats(days: number = 7): Promise<TradeStats> {
    return api.get('/trades/stats', { params: { days } })
  },
}
