import { API_BASE_URL } from './apiConfig.js'

// Thrown for any profile fetch/update failure. `message` is always safe
// to show directly to the user — never raw backend/database text.
export class ProfileError extends Error {
  constructor(message, { status } = {}) {
    super(message)
    this.name = 'ProfileError'
    this.status = status
  }
}

/**
 * Fetch the authenticated patient's own profile via GET /patient/profile.
 *
 * Relies entirely on the HttpOnly session cookie (`credentials: 'include'`)
 * — there is no user ID to pass, since the backend always derives "which
 * patient" from the authenticated session.
 *
 * @returns {Promise<{ id: string, full_name: string, email: string, role: string, created_at: string, updated_at: string }>}
 * @throws {ProfileError} with a user-friendly message on any failure.
 */
export async function fetchPatientProfile() {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/profile`, {
      method: 'GET',
      credentials: 'include',
    })
  } catch {
    throw new ProfileError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new ProfileError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  throw new ProfileError('Something went wrong loading your profile. Please try again.', {
    status: response.status,
  })
}

/**
 * Update the authenticated patient's own profile via PATCH /patient/profile.
 *
 * For this first implementation only `fullName` is editable. Like the
 * fetch above, there is no user ID in the request — the backend always
 * updates the caller's own record, derived from the session cookie.
 *
 * @param {{ fullName: string }} input
 * @returns {Promise<{ id: string, full_name: string, email: string, role: string, created_at: string, updated_at: string }>}
 * @throws {ProfileError} with a user-friendly message on any failure.
 */
export async function updatePatientProfile({ fullName }) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/profile`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ full_name: fullName }),
    })
  } catch {
    throw new ProfileError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new ProfileError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  if (response.status === 422 || response.status === 400) {
    throw new ProfileError('Please enter a valid name.', {
      status: response.status,
    })
  }

  throw new ProfileError('Something went wrong saving your changes. Please try again.', {
    status: response.status,
  })
}

// Thrown for any doctor-directory fetch failure. `message` is always
// safe to show directly to the user — never raw backend/database text.
export class DoctorDirectoryError extends Error {
  constructor(message, { status } = {}) {
    super(message)
    this.name = 'DoctorDirectoryError'
    this.status = status
  }
}

/**
 * Fetch the doctor directory via GET /patient/doctors.
 *
 * Relies entirely on the HttpOnly session cookie (`credentials: 'include'`)
 * — same pattern as fetchPatientProfile above. The backend already
 * restricts the result to role=doctor and excludes every patient
 * account, so nothing further needs to be filtered on this side.
 *
 * @returns {Promise<Array<{ id: string, full_name: string, role: string, created_at: string }>>}
 * @throws {DoctorDirectoryError} with a user-friendly message on any failure.
 */
export async function fetchDoctors() {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/doctors`, {
      method: 'GET',
      credentials: 'include',
    })
  } catch {
    throw new DoctorDirectoryError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new DoctorDirectoryError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  throw new DoctorDirectoryError(
    'Something went wrong loading the doctor directory. Please try again.',
    { status: response.status },
  )
}

/**
 * Fetch a single doctor's safe public details via
 * GET /patient/doctors/{doctorId}.
 *
 * Same session-cookie reliance as fetchDoctors above. The backend treats
 * an unknown ID and a patient ID identically as a 404 — this function
 * doesn't need to (and can't) distinguish those cases, it just surfaces
 * a not-found error either way.
 *
 * @param {string} doctorId
 * @returns {Promise<{ id: string, full_name: string, role: string, created_at: string }>}
 * @throws {DoctorDirectoryError} with a user-friendly message on any failure.
 */
export async function fetchDoctorById(doctorId) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/doctors/${doctorId}`, {
      method: 'GET',
      credentials: 'include',
    })
  } catch {
    throw new DoctorDirectoryError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new DoctorDirectoryError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  if (response.status === 404) {
    throw new DoctorDirectoryError('This doctor could not be found.', {
      status: response.status,
    })
  }

  throw new DoctorDirectoryError(
    'Something went wrong loading this doctor. Please try again.',
    { status: response.status },
  )
}
