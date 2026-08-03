import axios from 'axios'

const api = axios.create({ baseURL: '', timeout: 60000 })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) { localStorage.clear(); window.location.href = '/' }
    return Promise.reject(err)
  }
)

// Auth
export const login = (username: string, password: string) => api.post('/api/auth/login', { username, password })
export const getMe = () => api.get('/api/auth/me')
export const getStats = () => api.get('/api/stats')

// Chat
export const chatSync = (data: { message: string; session_id?: string; user_id?: number; user_role?: string }) =>
  api.post('/api/chat', data)

// Skills
export const getSkills = () => api.get('/api/skills')
export const createSkill = (data: any) => api.post('/api/skills', data)
export const updateSkill = (name: string, data: any) => api.put(`/api/skills/${name}`, data)
export const deleteSkill = (name: string) => api.delete(`/api/skills/${name}`)
export const toggleSkill = (name: string) => api.patch(`/api/skills/${name}/toggle`)
export const getAvailableTools = () => api.get('/api/skills/tools')

// Approvals
export const getApprovals = (status?: string) => api.get('/api/approvals', { params: { status } })
export const getPendingApprovals = () => api.get('/api/approvals/pending')
export const actionApproval = (id: string, data: any) => api.post(`/api/approvals/${id}/action`, data)
export const getApprovalStats = () => api.get('/api/approvals/stats')

// Knowledge
export const getKnowledgeDocs = (category?: string) => api.get('/api/knowledge', { params: { category } })
export const searchKnowledge = (query: string, topK = 5) => api.post('/api/knowledge/search', { query, top_k: topK })
export const createKnowledgeDoc = (data: any) => api.post('/api/knowledge', data)
export const updateKnowledgeDoc = (id: string, data: any) => api.put(`/api/knowledge/${id}`, data)
export const deleteKnowledgeDoc = (id: string) => api.delete(`/api/knowledge/${id}`)
export const getKnowledgeCategories = () => api.get('/api/knowledge/categories')

// Eval
export const getEvalPresets = () => api.get('/api/eval/presets')
export const runEval = (testSet: any[]) => api.post('/api/eval/run', { test_set: testSet })
