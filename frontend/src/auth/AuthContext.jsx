import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY
const supabase = supabaseUrl && supabaseAnonKey
  ? createClient(supabaseUrl, supabaseAnonKey, {
    auth: {
      autoRefreshToken: true,
      detectSessionInUrl: true,
      persistSession: true,
    },
  })
  : null

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null)
  const [initializing, setInitializing] = useState(true)
  const [profile, setProfile] = useState(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [profileError, setProfileError] = useState('')

  useEffect(() => {
    if (!supabase) {
      setInitializing(false)
      return undefined
    }
    let active = true
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      if (!active) return
      setSession(nextSession)
      setInitializing(false)
    })
    supabase.auth.getSession().then(({ data, error }) => {
      if (!active) return
      if (error) setProfileError('Could not restore the Supabase session. Sign in again.')
      setSession(data.session)
      setInitializing(false)
    })
    return () => {
      active = false
      subscription.unsubscribe()
    }
  }, [])

  const apiFetch = useCallback(async (input, options = {}) => {
    if (!supabase) throw new Error('Supabase is not configured in the frontend.')
    const { data, error } = await supabase.auth.getSession()
    if (error) throw new Error(error.message)
    let activeSession = data.session
    if (!activeSession?.access_token) throw new Error('Sign in to continue.')

    const send = (token) => {
      const headers = new Headers(options.headers)
      headers.set('Authorization', `Bearer ${token}`)
      return fetch(input, { ...options, headers })
    }
    let response = await send(activeSession.access_token)
    if (response.status === 401) {
      const refreshed = await supabase.auth.refreshSession()
      activeSession = refreshed.data.session
      if (refreshed.error || !activeSession?.access_token) {
        await supabase.auth.signOut()
        return response
      }
      response = await send(activeSession.access_token)
      if (response.status === 401) await supabase.auth.signOut()
    }
    return response
  }, [])

  useEffect(() => {
    let active = true
    if (!session) {
      setProfile(null)
      setProfileError('')
      setProfileLoading(false)
      return () => { active = false }
    }
    setProfile(null)
    setProfileError('')
    setProfileLoading(true)
    apiFetch('/api/auth/me')
      .then(async (response) => {
        const body = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(body.detail || `Account setup failed (${response.status}).`)
        if (active) setProfile(body)
      })
      .catch((error) => {
        if (active) setProfileError(error.message)
      })
      .finally(() => {
        if (active) setProfileLoading(false)
      })
    return () => { active = false }
  }, [session, apiFetch])

  const signIn = useCallback(async (email, password) => {
    if (!supabase) throw new Error('Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY to enable Supabase Auth.')
    const { error } = await supabase.auth.signInWithPassword({ email, password })
    if (error) throw error
  }, [])

  const signOut = useCallback(async () => {
    if (!supabase) return
    const { error } = await supabase.auth.signOut()
    if (error) throw error
  }, [])

  const value = useMemo(() => ({
    configured: Boolean(supabase),
    session,
    user: session?.user || null,
    profile,
    profileError,
    loading: initializing || profileLoading,
    signIn,
    signOut,
    apiFetch,
  }), [session, profile, profileError, initializing, profileLoading, signIn, signOut, apiFetch])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider.')
  return context
}
