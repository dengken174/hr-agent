import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import client from '../api/client'

export interface UserInfo {
  user_id: number
  role: string
  display_name: string
}

export const useUserStore = defineStore('user', () => {
  const user = ref<UserInfo | null>(null)
  const token = ref<string>('')

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'hr_admin')

  function restoreSession() {
    const saved = localStorage.getItem('user')
    const savedToken = localStorage.getItem('token')
    if (saved && savedToken) {
      user.value = JSON.parse(saved)
      token.value = savedToken
    }
  }

  async function login(username: string, password: string) {
    const { data } = await client.post('/auth/login', { username, password })
    token.value = data.access_token
    user.value = {
      user_id: data.user_id,
      role: data.user_role,
      display_name: data.display_name,
    }
    localStorage.setItem('token', data.access_token)
    localStorage.setItem('user', JSON.stringify(user.value))
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  return { user, token, isLoggedIn, isAdmin, restoreSession, login, logout }
})
