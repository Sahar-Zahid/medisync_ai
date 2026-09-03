import { API_BASE_URL } from './apiConfig.js'

// Thrown for any trusted-results fetch failure. `message` is always safe
// to show directly to the user — never raw backend/database text.
export class TrustedResultsError extends Error {
  constructor(message, { status } = {}) {
    super(message)
    this.name = 'TrustedResultsError'
    this.status = status
  }
}

/**
 * Fetch the authenticated patient's own trusted (doctor-reviewed) test
 * results via GET /patient/results.
 *
 * Relies entirely on the HttpOnly session cookie (`credentials: 'include'`)
 * for authentication — same pattern as patientService.js/reportService.js.
 * There is no patient ID anywhere in this request; the backend always
 * derives ownership from the authenticated session.
 *
 * @returns {Promise<Array<{
 *   id: string,
 *   status: 'verified' | 'corrected',
 *   canonical_test: { code: string, display_name: string } | null,
 *   test_name: string,
 *   raw_value: string,
 *   normalized_value: string | null,
 *   normalized_unit: string | null,
 *   result_date: string | null,
 *   reference_range_lower: string | null,
 *   reference_range_upper: string | null,
 *   reference_range_inclusive_lower: boolean | null,
 *   reference_range_inclusive_upper: boolean | null,
 *   abnormality_status: string,
 *   verified_at: string | null,
 * }>>}
 * @throws {TrustedResultsError} with a user-friendly message on any failure.
 */
/**
 * Fetch the authenticated patient's own trusted (doctor-reviewed) test
 * results, ordered chronologically (newest first), for the read-only
 * history/timeline view, via GET /patient/results/history.
 *
 * Same response shape as fetchTrustedResults() above — the history view
 * is the same trusted data, just displayed chronologically — so it
 * reuses the same TrustedResultsError type and auth pattern.
 *
 * @returns {ReturnType<typeof fetchTrustedResults>}
 * @throws {TrustedResultsError} with a user-friendly message on any failure.
 */
export async function fetchTrustedResultsHistory() {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/results/history`, {
      method: 'GET',
      credentials: 'include',
    })
  } catch {
    throw new TrustedResultsError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new TrustedResultsError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  throw new TrustedResultsError(
    'Something went wrong loading your results history. Please try again.',
    { status: response.status },
  )
}

export async function fetchTrustedResults() {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/results`, {
      method: 'GET',
      credentials: 'include',
    })
  } catch {
    throw new TrustedResultsError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new TrustedResultsError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  throw new TrustedResultsError(
    'Something went wrong loading your trusted results. Please try again.',
    { status: response.status },
  )
}
