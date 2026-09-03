import { API_BASE_URL } from './apiConfig.js'

// Thrown for any AI summary fetch failure. `message` is always safe to
// show directly to the user — never raw backend/database text.
export class ResultSummaryError extends Error {
  constructor(message, { status } = {}) {
    super(message)
    this.name = 'ResultSummaryError'
    this.status = status
  }
}

/**
 * Fetch a read-only, AI-generated plain-language summary of the
 * authenticated patient's own trusted (VERIFIED/CORRECTED) test results
 * via GET /patient/results/summary.
 *
 * Relies entirely on the HttpOnly session cookie (`credentials: 'include'`)
 * for authentication — same pattern as resultService.js. There is no
 * patient ID anywhere in this request; the backend always derives
 * ownership from the authenticated session, and only ever reads trusted
 * TestResult rows — never AI-extracted candidates awaiting review.
 *
 * @returns {Promise<{
 *   has_trusted_results: boolean,
 *   result_count: number,
 *   observations: string[],
 *   disclaimer: string,
 *   generated_at: string | null,
 * }>}
 * @throws {ResultSummaryError} with a user-friendly message on any failure.
 */
export async function fetchResultSummary() {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/results/summary`, {
      method: 'GET',
      credentials: 'include',
    })
  } catch {
    throw new ResultSummaryError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new ResultSummaryError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  if (response.status === 503) {
    throw new ResultSummaryError(
      'The AI summary is temporarily unavailable. Please try again later.',
      { status: response.status },
    )
  }

  throw new ResultSummaryError(
    'Something went wrong generating your AI summary. Please try again.',
    { status: response.status },
  )
}
