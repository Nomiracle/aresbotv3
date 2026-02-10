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
