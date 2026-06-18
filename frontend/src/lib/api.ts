import axios from 'axios'

const api = axios.create({
  baseURL: '/',
  withCredentials: true,
})

// Inject JWT from sessionStorage on every request
api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('token')
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// On 401 → redirect to login (except for status/suggestions checks)
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const url: string = err.config?.url ?? ''
    const skipRedirect = url.includes('/api/assistant/status') || url.includes('/api/assistant/suggestions')
    if (err.response?.status === 401 && !skipRedirect) {
      sessionStorage.clear()
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    return Promise.reject(err)
  }
)

export default api