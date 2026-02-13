import api from './index'
import type { NotificationChannel, NotificationChannelCreate, NotifyEventInfo } from '@/types'

export const notificationApi = {
  getAll(): Promise<NotificationChannel[]> {
    return api.get('/notifications')
  },

  create(data: NotificationChannelCreate): Promise<NotificationChannel> {
    return api.post('/notifications', data)
  },

  update(id: number, data: Partial<NotificationChannelCreate> & { is_active?: boolean }): Promise<NotificationChannel> {
    return api.put(`/notifications/${id}`, data)
  },

  delete(id: number): Promise<void> {
    return api.delete(`/notifications/${id}`)
  },

  test(id: number): Promise<{ status: string; message: string }> {
    return api.post(`/notifications/${id}/test`)
  },

  getEvents(): Promise<NotifyEventInfo[]> {
    return api.get('/notifications/events')
  },
}
