import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { environment } from '../config/environment'
import { GatewayRequestError } from '@/shared/lib/gatewayClient'
import { getCurrentUser, getGoogleClientId, signInWithGoogleCredential, signInWithPassword, signOutCurrentUser, type AuthUser } from './authApi'

export type { AuthUser } from './authApi'

interface AuthContextValue {
  user: AuthUser | null
  googleClientId: string
  googleConfigUnavailable: boolean
  isLoading: boolean
  accessDenied: boolean
  signInWithGoogle: (credential: string) => Promise<void>
  signInWithEmail: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  clearAccessDenied: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [accessDenied, setAccessDenied] = useState(false)
  const [googleClientId, setGoogleClientId] = useState(environment.googleClientId)
  const [googleConfigUnavailable, setGoogleConfigUnavailable] = useState(false)

  useEffect(() => {
    let active = true
    void Promise.all([
      getCurrentUser().catch(() => null),
      getGoogleClientId().then((clientId) => ({ clientId, unavailable: false })).catch(() => ({ clientId: environment.googleClientId, unavailable: true })),
    ])
      .then(([nextUser, googleConfig]) => {
        if (!active) return
        setUser(nextUser)
        setGoogleClientId(googleConfig.clientId || environment.googleClientId)
        setGoogleConfigUnavailable(googleConfig.unavailable)
      })
      .finally(() => { if (active) setIsLoading(false) })
    return () => { active = false }
  }, [])

  const signInWithGoogle = useCallback(async (credential: string) => {
    setIsLoading(true)
    setAccessDenied(false)
    try {
      setUser(await signInWithGoogleCredential(credential))
    } catch (reason) {
      setUser(null)
      if (reason instanceof GatewayRequestError && reason.status === 403) setAccessDenied(true)
    } finally {
      setIsLoading(false)
    }
  }, [])

  const signOut = useCallback(async () => {
    try { await signOutCurrentUser() } finally {
      setUser(null)
      setAccessDenied(false)
    }
  }, [])

  const signInWithEmail = useCallback(async (email: string, password: string) => {
    setIsLoading(true); setAccessDenied(false)
    try { setUser(await signInWithPassword(email, password)) } finally { setIsLoading(false) }
  }, [])

  const value = useMemo<AuthContextValue>(() => ({
    user,
    googleClientId,
    googleConfigUnavailable,
    isLoading,
    accessDenied,
    signInWithGoogle,
    signInWithEmail,
    signOut,
    clearAccessDenied: () => setAccessDenied(false),
  }), [accessDenied, googleClientId, googleConfigUnavailable, isLoading, signInWithEmail, signInWithGoogle, signOut, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
