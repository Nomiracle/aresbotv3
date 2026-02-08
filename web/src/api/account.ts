import api from './index'
import type { Account, AccountCreate } from '@/types'

export const accountApi = {
  getAll(): Promise<Account[]> {
    return api.get('/accounts')
  },

  getById(id: number): Promise<Account> {
    return api.get(`/accounts/${id}`)
  },

  create(data: AccountCreate): Promise<Account> {
    return api.post('/accounts', data)
  },

  update(id: number, data: Partial<AccountCreate>): Promise<Account> {
    return api.put(`/accounts/${id}`, data)
  },

  delete(id: number): Promise<void> {
    return api.delete(`/accounts/${id}`)
  },
}
