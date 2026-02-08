import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUserStore = defineStore('user', () => {
  const email = ref<string | null>(null)

  function setEmail(e: string) {
    email.value = e
  }

  function logout() {
    window.location.href = '/oauth2/sign_out'
  }

  return { email, setEmail, logout }
})
