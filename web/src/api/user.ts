import api from '.'

export interface UserInfo {
  email: string
  stats: {
    accounts: number
    strategies: number
    trades: number
    total_pnl: number
  }
}

export const getUserInfo = () => api.get<any, UserInfo>('/user')
