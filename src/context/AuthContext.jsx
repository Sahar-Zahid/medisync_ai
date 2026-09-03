import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import {
  fetchCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
} from '../services/authService.js'

const AuthContext = createContext(null)

/**
 * Small centralized place for "is anyone logged in, and who" — nothing
 * more elaborate than that is needed yet.
 *
 * Deliberately holds no token: the JWT lives only in the HttpOnly cookie
 * the backend sets, which this code (like all browser JS) cannot read.
 * Instead this tracks the authenticated user's profile, refreshed via
 * GET /auth/me, which only succeeds if the browser's cookie is valid.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  const refresh = useCallback(async () => {
    const current = await fetchCurrentUser()
    setUser(current)
    return current
  }, [])

  // On first load, check whether an existing session cookie is still
  // valid so a page refresh doesn't look like a fresh logged-out state.
  useEffect(() => {
    refresh().finally(() => setIsLoading(false))
  }, [refresh])

  async function login(credentials) {
    await loginRequest(credentials)
    // The login response body intentionally isn't used as the source of
    // truth for "who is logged in" — re-fetching via /auth/me confirms
    // the cookie actually took effect and returns the user's real role.
    return refresh()
  }

  async function logout() {
    await logoutRequest()
    setUser(null)
  }

  // Lets a page that just performed a profile update (e.g. PATCH
  // /patient/profile) reflect the change immediately — in the header,
  // sidebar, anywhere else `user` is read — without a full page reload
  // or an extra round trip to /auth/me. Callers pass the fresh user
  // object returned directly by that update's own response body.
  function updateUser(updatedUser) {
    setUser(updatedUser)
  }

  const value = {
    user,
    isAuthenticated: Boolean(user),
    isLoading,
    login,
    logout,
    refresh,
    updateUser,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
