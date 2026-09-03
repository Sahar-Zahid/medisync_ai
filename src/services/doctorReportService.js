import { API_BASE_URL } from './apiConfig.js'

/**
 * Doctor-facing report API service.
 *
 * Every function talks to the backend using the same HttpOnly cookie
 * session the rest of the app relies on — never manages tokens directly.
 */

/**
 * Fetch a patient's reports for the authenticated doctor.
 *
 * @param {string} patientId - The patient's UUID
 * @returns {Promise<Object>} Patient reports response
 */
export async function getPatientReports(patientId) {
  const response = await fetch(
    `${API_BASE_URL}/doctor/patients/${patientId}/reports`,
    { credentials: 'include' }
  )

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not load patient reports.')
  }

  return response.json()
}

/**
 * Fetch the read-only PENDING / abnormal-results triage view: every
 * CandidateResult still awaiting doctor review, across every patient
 * the authenticated doctor currently has ACTIVE access to.
 *
 * This is a GET request only — it never changes verification_status
 * and never creates a trusted TestResult.
 *
 * @returns {Promise<Object>} Triage response { results: [...] }
 */
export async function getTriageResults() {
  const response = await fetch(
    `${API_BASE_URL}/doctor/triage`,
    { credentials: 'include' }
  )

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not load triage results.')
  }

  return response.json()
}

/**
 * Get the URL for downloading a patient's report PDF.
 *
 * This returns a URL that can be used in an <a> tag or fetch request.
 * The actual authorization happens server-side.
 *
 * @param {string} patientId - The patient's UUID
 * @param {string} reportId - The report's UUID
 * @returns {string} URL for the PDF endpoint
 */
export function getReportPdfUrl(patientId, reportId) {
  return `${API_BASE_URL}/doctor/patients/${patientId}/reports/${reportId}/pdf`
}

/**
 * Verify a pending candidate result.
 *
 * This is a POST request that tells the server the doctor has reviewed
 * and approved the candidate. The server copies the candidate data
 * into a trusted TestResult.
 *
 * @param {string} patientId - The patient's UUID
 * @param {string} reportId - The report's UUID
 * @param {string} candidateId - The candidate's UUID
 * @returns {Promise<Object>} Verification response
 */
/**
 * Correct a pending candidate result with structured correction data.
 *
 * @param {string} patientId - The patient's UUID
 * @param {string} reportId - The report's UUID
 * @param {string} candidateId - The candidate's UUID
 * @param {Object} correction - Correction data (value, unit, test_name, reason, etc.)
 * @returns {Promise<Object>} Correction response
 */
export async function correctCandidate(patientId, reportId, candidateId, correction) {
  const response = await fetch(
    `${API_BASE_URL}/doctor/patients/${patientId}/reports/${reportId}/candidates/${candidateId}/correct`,
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(correction),
    }
  )

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not correct candidate.')
  }

  return response.json()
}

/**
 * Reject a pending candidate result.
 *
 * @param {string} patientId - The patient's UUID
 * @param {string} reportId - The report's UUID
 * @param {string} candidateId - The candidate's UUID
 * @param {string} reason - Required rejection reason
 * @returns {Promise<Object>} Rejection response
 */
export async function rejectCandidate(patientId, reportId, candidateId, reason) {
  const response = await fetch(
    `${API_BASE_URL}/doctor/patients/${patientId}/reports/${reportId}/candidates/${candidateId}/reject`,
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ reason }),
    }
  )

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not reject candidate.')
  }

  return response.json()
}

export async function verifyCandidate(patientId, reportId, candidateId) {
  const response = await fetch(
    `${API_BASE_URL}/doctor/patients/${patientId}/reports/${reportId}/candidates/${candidateId}/verify`,
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
    }
  )

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not verify candidate.')
  }

  return response.json()
}

/**
 * Fetch a report's immutable verification history for the authenticated
 * doctor, ordered chronologically.
 *
 * This is a READ-ONLY endpoint — there is no update or delete API. Each
 * entry records a successful VERIFY / CORRECT / REJECT action with the
 * original candidate snapshot, final values where applicable, reason,
 * and server timestamp.
 *
 * @param {string} patientId - The patient's UUID
 * @param {string} reportId - The report's UUID
 * @returns {Promise<Object>} History response { patient_id, report_id, history: [...] }
 */
export async function getVerificationHistory(patientId, reportId) {
  const response = await fetch(
    `${API_BASE_URL}/doctor/patients/${patientId}/reports/${reportId}/verification-history`,
    { credentials: 'include' }
  )

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not load verification history.')
  }

  return response.json()
}

/**
 * Doctor confirms the identity checkpoint for a report.
 *
 * @param {string} patientId - The patient's UUID
 * @param {string} reportId - The report's UUID
 * @returns {Promise<Object>} Confirmation response
 */
export async function confirmIdentity(patientId, reportId) {
  const response = await fetch(
    `${API_BASE_URL}/doctor/patients/${patientId}/reports/${reportId}/confirm-identity`,
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
    }
  )

  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Could not confirm identity checkpoint.')
  }

  return response.json()
}
