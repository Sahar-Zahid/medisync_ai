import { API_BASE_URL } from './apiConfig.js'

// Thrown for any signup failure. `message` is always safe to show directly
// to the user — never raw backend/database text.
export class SignupError extends Error {
  constructor(message, { status } = {}) {
    super(message)
    this.name = 'SignupError'
    this.status = status
  }
}

/**
 * Register a new account via POST /auth/signup.
 *
 * @param {{ fullName: string, email: string, password: string, role: 'patient' | 'doctor' }} input
 * @returns {Promise<{ id: string, full_name: string, email: string, role: string, created_at: string, updated_at: string }>}
 *   The backend's safe UserResponse — never contains a password field.
 * @throws {SignupError} with a user-friendly message on any failure.
 *
 * Note: this function never logs `input` or the request body — they
 * contain the plaintext password — and never persists anything to
 * localStorage/sessionStorage. The caller decides what (if anything) to
 * do with the response, and should not store it in browser storage either.
 */
export async function signup({ fullName, email, password, role }) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/auth/signup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        full_name: fullName,
        email,
        password,
        role,
      }),
    })
  } catch {
    // fetch() itself only throws on network-level failure (server
    // unreachable, DNS failure, CORS rejection, etc.) — not on HTTP error
    // status codes, which are handled below.
    throw new SignupError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 409) {
    throw new SignupError('An account with this email already exists.', {
      status: response.status,
    })
  }

  if (response.status === 422 || response.status === 400) {
    throw new SignupError(
      'Please check the information you entered and try again.',
      { status: response.status },
    )
  }

  throw new SignupError('Something went wrong creating your account. Please try again.', {
    status: response.status,
  })
}

// Thrown for any login failure. `message` is always safe to show directly
// to the user — the backend uses one generic message for every failure
// mode (unknown email, wrong password, wrong role) so this never reveals
// which one occurred.
export class LoginError extends Error {
  constructor(message, { status } = {}) {
    super(message)
    this.name = 'LoginError'
    this.status = status
  }
}

/**
 * Log in via POST /auth/login.
 *
 * `role` here is only the requested login role — the backend checks it
 * against the user's actual database role and rejects the login on any
 * mismatch.
 *
 * `credentials: 'include'` is what makes the browser accept and later
 * resend the HttpOnly session cookie the backend sets on a successful
 * response. This function deliberately does not read or return the
 * response body's `access_token` for the caller to store — the real
 * session lives only in that cookie, which JavaScript cannot read, and
 * nothing here writes to localStorage/sessionStorage.
 *
 * @param {{ email: string, password: string, role: 'patient' | 'doctor' }} input
 * @throws {LoginError} with a user-friendly message on any failure.
 */
export async function login({ email, password, role }) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ email, password, role }),
    })
  } catch {
    throw new LoginError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return
  }

  if (response.status === 401) {
    throw new LoginError('Invalid email, password, or role.', {
      status: response.status,
    })
  }

  if (response.status === 422 || response.status === 400) {
    throw new LoginError('Please check the information you entered and try again.', {
      status: response.status,
    })
  }

  throw new LoginError('Something went wrong signing in. Please try again.', {
    status: response.status,
  })
}

/**
 * Log out via POST /auth/logout, which clears the HttpOnly session cookie
 * server-side. This is the actual sign-out — there is no client-side
 * token to discard, since none is ever stored in JS-accessible storage.
 * Best-effort: even if the network call fails, the caller (AuthContext)
 * still clears its own in-memory auth state.
 */
export async function logout() {
  try {
    await fetch(`${API_BASE_URL}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    })
  } catch {
    // Ignored — see comment above.
  }
}

/**
 * Fetch the currently authenticated user via GET /auth/me, relying on the
 * HttpOnly cookie the browser sends automatically. Returns null (rather
 * than throwing) when there is no valid session, so callers can treat
 * "not logged in" as a normal, expected result rather than an error.
 *
 * @returns {Promise<{ id: string, full_name: string, email: string, role: string, created_at: string, updated_at: string } | null>}
 */
export async function fetchCurrentUser() {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/auth/me`, {
      method: 'GET',
      credentials: 'include',
    })
  } catch {
    return null
  }

  if (!response.ok) {
    return null
  }

  return response.json()
}
