import api from './index'
import type { Trade, TradeStats, PaginatedResponse } from '@/types'

export const tradeApi = {
  getAll(params?: {
    strategy_id?: number
    limit?: number
    offset?: number
    start_date?: string
    end_date?: string
  }): Promise<PaginatedResponse<Trade>> {
    return api.get('/trades', { params })
  },

  getStats(params?: {
    days?: number
    strategy_id?: number
    start_date?: string
    end_date?: string
  }): Promise<TradeStats> {
    return api.get('/trades/stats', { params })
  },
}
