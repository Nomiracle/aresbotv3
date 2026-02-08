import api from '.'

export interface WorkerInfo {
  name: string
  hostname: string
  concurrency: number
  active_tasks: number
}

export const getWorkers = () => api.get<any, WorkerInfo[]>('/workers')
