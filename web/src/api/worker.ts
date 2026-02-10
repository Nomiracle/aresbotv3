import api from '.'

export interface WorkerInfo {
  name: string
  hostname: string
  ip: string
  private_ip: string
  public_ip: string
  ip_location: string
  concurrency: number
  active_tasks: number
}

export const getWorkers = () => api.get<any, WorkerInfo[]>('/workers')

interface WorkerCachePayload {
  updated_at: number
  workers: WorkerInfo[]
}

const WORKERS_CACHE_KEY = 'aresbot:workers:v1'

let workersMemoryCache: WorkerInfo[] | null = null
let workersPreloadPromise: Promise<WorkerInfo[]> | null = null

function updateWorkersCache(workers: WorkerInfo[]): WorkerInfo[] {
  workersMemoryCache = workers
  writeWorkersCacheToStorage(workers)
  return [...workers]
}

function getLocalStorage(): Storage | null {
  if (typeof window === 'undefined') {
    return null
  }
  return window.localStorage
}

function readWorkersCacheFromStorage(): WorkerInfo[] {
  const localStorage = getLocalStorage()
  if (!localStorage) {
    return []
  }

  try {
    const raw = localStorage.getItem(WORKERS_CACHE_KEY)
    if (!raw) {
      return []
    }

    const parsed = JSON.parse(raw) as Partial<WorkerCachePayload>
    if (!parsed || !Array.isArray(parsed.workers)) {
      return []
    }

    return parsed.workers
  } catch {
    return []
  }
}

function writeWorkersCacheToStorage(workers: WorkerInfo[]): void {
  const localStorage = getLocalStorage()
  if (!localStorage) {
    return
  }

  const payload: WorkerCachePayload = {
    updated_at: Date.now(),
    workers,
  }
  localStorage.setItem(WORKERS_CACHE_KEY, JSON.stringify(payload))
}

function ensureMemoryCache(): WorkerInfo[] {
  if (workersMemoryCache !== null) {
    return workersMemoryCache
  }

  workersMemoryCache = readWorkersCacheFromStorage()
  return workersMemoryCache
}

export function getWorkersFromCache(): WorkerInfo[] {
  return [...ensureMemoryCache()]
}

export async function preloadWorkersCache(): Promise<WorkerInfo[]> {
  if (workersPreloadPromise) {
    return workersPreloadPromise
  }

  workersPreloadPromise = getWorkers()
    .then((workers) => updateWorkersCache(workers))
    .catch((error) => {
      const cached = ensureMemoryCache()
      if (cached.length > 0) {
        return [...cached]
      }
      throw error
    })
    .finally(() => {
      workersPreloadPromise = null
    })

  return workersPreloadPromise
}

export async function refreshWorkersCache(): Promise<WorkerInfo[]> {
  const workers = await getWorkers()
  return updateWorkersCache(workers)
}
