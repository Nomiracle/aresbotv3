import api from './index'
import type { Trade, TradeStats } from '@/types'

export const tradeApi = {
  getAll(params?: {
    strategy_id?: number
    limit?: number
    offset?: number
  }): Promise<Trade[]> {
    return api.get('/trades', { params })
  },

  getStats(days: number = 7): Promise<TradeStats> {
    return api.get('/trades/stats', { params: { days } })
  },
}
