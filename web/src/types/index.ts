export interface Account {
  id: number
  exchange: string
  label: string
  api_key: string
  testnet: boolean
  is_active: boolean
  created_at: string
}

export interface AccountCreate {
  exchange: string
  label: string
  api_key: string
  api_secret: string
  testnet: boolean
}

export interface Strategy {
  id: number
  account_id: number
  name: string
  symbol: string
  base_order_size: string
  buy_price_deviation: string
  sell_price_deviation: string
  grid_levels: number
  polling_interval: string
  price_tolerance: string
  stop_loss: string | null
  stop_loss_delay: number | null
  max_open_positions: number
  max_daily_drawdown: string | null
  worker_name: string | null
  created_at: string
  updated_at: string
}

export interface StrategyCreate {
  account_id: number
  name: string
  symbol: string
  base_order_size: string
  buy_price_deviation: string
  sell_price_deviation: string
  grid_levels: number
  polling_interval?: string
  price_tolerance?: string
  stop_loss?: string | null
  stop_loss_delay?: number | null
  max_open_positions?: number
  max_daily_drawdown?: string | null
  worker_name?: string | null
}

export interface OrderDetail {
  price: number
  quantity: number
}

export interface StrategyStatus {
  strategy_id: number
  is_running: boolean
  task_id?: string
  exchange?: string | null
  worker_name?: string
  worker_ip?: string
  worker_private_ip?: string
  worker_public_ip?: string
  worker_ip_location?: string
  worker_hostname?: string
  current_price: number | null
  pending_buys: number
  pending_sells: number
  buy_orders?: OrderDetail[]
  sell_orders?: OrderDetail[]
  position_count: number
  target_price?: number
  buy_prices?: number[]
  sell_prices?: number[]
  buy_avg_diff_percent?: number
  sell_avg_diff_percent?: number
  start_timestamp?: number
  started_at?: number
  updated_at?: number
  last_error?: string
  error_count?: number
  extra_status?: Record<string, unknown>
}

export interface RunningStrategy {
  strategy_id: number
  task_id: string
  exchange?: string | null
  strategy_name: string
  symbol: string
  base_order_size: string
  buy_price_deviation: string
  sell_price_deviation: string
  grid_levels: number
  polling_interval: string
  price_tolerance: string
  stop_loss?: string | null
  stop_loss_delay?: number | null
  max_open_positions: number
  max_daily_drawdown?: string | null
  worker_name?: string | null
  worker_ip: string
  worker_private_ip: string
  worker_public_ip: string
  worker_ip_location: string
  worker_hostname: string
  status: string
  current_price: number
  pending_buys: number
  pending_sells: number
  buy_orders: OrderDetail[]
  sell_orders: OrderDetail[]
  position_count: number
  started_at: number
  updated_at: number
  last_error?: string | null
  extra_status?: Record<string, unknown>
}

export interface Trade {
  id: number
  strategy_id: number
  order_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  price: string
  quantity: string
  amount: string
  fee: string
  pnl: string | null
  grid_index: number | null
  related_order_id: string | null
  created_at: string
}

export interface TradeStats {
  period_days: number
  total_trades: number
  total_pnl: string
  total_volume: string
  total_fees: string
  win_count: number
  loss_count: number
  win_rate: number
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}
