import api from './index'
import type { Account, AccountCreate } from '@/types'

export interface TradingFee {
  symbol: string
  maker: number
  taker: number
}

export interface ExchangeOption {
  value: string
  label: string
}

interface ExchangeCachePayload {
  updated_at: number
  exchanges: ExchangeOption[]
}

const EXCHANGES_CACHE_KEY = 'aresbot:exchanges:v1'

let exchangesMemoryCache: ExchangeOption[] | null = null

function getLocalStorage(): Storage | null {
  if (typeof window === 'undefined') {
    return null
  }
  return window.localStorage
}

function readExchangesCacheFromStorage(): ExchangeOption[] {
  const localStorage = getLocalStorage()
  if (!localStorage) {
    return []
  }

  try {
    const raw = localStorage.getItem(EXCHANGES_CACHE_KEY)
    if (!raw) {
      return []
    }

    const parsed = JSON.parse(raw) as Partial<ExchangeCachePayload>
    if (!parsed || !Array.isArray(parsed.exchanges)) {
      return []
    }

    return parsed.exchanges
  } catch {
    return []
  }
}

function writeExchangesCacheToStorage(exchanges: ExchangeOption[]): void {
  const localStorage = getLocalStorage()
  if (!localStorage) {
    return
  }

  const payload: ExchangeCachePayload = {
    updated_at: Date.now(),
    exchanges,
  }
  localStorage.setItem(EXCHANGES_CACHE_KEY, JSON.stringify(payload))
}

function updateExchangesCache(exchanges: ExchangeOption[]): ExchangeOption[] {
  exchangesMemoryCache = exchanges
  writeExchangesCacheToStorage(exchanges)
  return [...exchanges]
}

function ensureExchangesMemoryCache(): ExchangeOption[] {
  if (exchangesMemoryCache !== null) {
    return exchangesMemoryCache
  }

  exchangesMemoryCache = readExchangesCacheFromStorage()
  return exchangesMemoryCache
}

export function getExchangeOptionsFromCache(): ExchangeOption[] {
  return [...ensureExchangesMemoryCache()]
}

export async function refreshExchangeOptionsCache(): Promise<ExchangeOption[]> {
  const exchanges = await api.get<any, ExchangeOption[]>('/accounts/exchanges', { params: { refresh: true } })
  return updateExchangesCache(exchanges)
}

export async function preloadExchangeOptionsCache(): Promise<ExchangeOption[]> {
  try {
    const exchanges = await api.get<any, ExchangeOption[]>('/accounts/exchanges')
    return updateExchangesCache(exchanges)
  } catch (error) {
    const cached = ensureExchangesMemoryCache()
    if (cached.length > 0) {
      return [...cached]
    }
    throw error
  }
}

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

  getSymbols(accountId: number): Promise<string[]> {
    return api.get(`/accounts/${accountId}/symbols`)
  },

  fetchTradingFee(accountId: number, symbol: string): Promise<TradingFee> {
    return api.get(`/accounts/${accountId}/trading-fee`, { params: { symbol } })
  },
}
