import { API_BASE_URL } from './apiConfig.js'

/**
 * Doctor-facing API service.
 *
 * Every function talks to the backend using the same HttpOnly cookie
 * session the rest of the app relies on — never manages tokens directly.
 */

/**
 * Fetch the doctor's My Patients roster.
 *
 * Returns only patients connected through ACTIVE DoctorPatientLinks.
 * The backend derives doctor identity from the authenticated session.
 *
 * @returns {Promise<Array>} Array of roster entries with safe patient metadata
 */
export async function getMyPatients() {
  const response = await fetch(`${API_BASE_URL}/doctor/patients`, {
    credentials: 'include',
  })

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not load patient roster.')
  }

  const data = await response.json()
  return data.patients
}
