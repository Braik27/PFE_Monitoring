import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import { useLoginMutation, useLogoutMutation } from '../hooks/useApi'
import api from '../lib/api'
import { normalizeApiUser, type AppUser } from '../lib/user'

interface AuthContextType {
  user: AppUser | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
  isLoading: boolean
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AppUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const { mutateAsync: loginMutate } = useLoginMutation()
  const { mutateAsync: logoutMutate } = useLogoutMutation()

  const fetchUser = async () => {
    const { data } = await api.get('/api/me')
    const u = normalizeApiUser(data as Record<string, unknown>)
    if (u) {
      setUser(u)
      sessionStorage.setItem('user', JSON.stringify(u))
    }
    return u
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const { data } = await api.get('/api/me')
        const u = normalizeApiUser(data as Record<string, unknown>)
        if (!cancelled && u) {
          setUser(u)
          sessionStorage.setItem('user', JSON.stringify(u))
        }
      } catch {
        if (!cancelled) {
          setUser(null)
          sessionStorage.removeItem('user')
        }
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const refreshUser = async () => {
    try { await fetchUser() } catch { /* ignore */ }
  }

  const login = async (username: string, password: string) => {
    await loginMutate({ username, password })
    const stored = sessionStorage.getItem('user')
    if (stored) {
      try {
        setUser(JSON.parse(stored) as AppUser)
      } catch {
        setUser(null)
      }
    }
  }

  const logout = async () => {
    try {
      await logoutMutate()
    } catch {
      // ignore
    }
    sessionStorage.clear()
    setUser(null)
    window.location.href = '/login'
  }

  return (
    <AuthContext.Provider value={{ user, login, logout, refreshUser, isLoading }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}