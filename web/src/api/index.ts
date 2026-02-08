import axios from 'axios'
import { ElMessage } from 'element-plus'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

api.interceptors.response.use(
  (res) => res.data,
  (err) => {
    ElMessage.error(err.response?.data?.detail || '请求失败')
    return Promise.reject(err)
  }
)

export default api
