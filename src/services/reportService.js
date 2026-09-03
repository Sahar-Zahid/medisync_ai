import { API_BASE_URL } from './apiConfig.js'

// Thrown for any report-upload failure. `message` is always safe to show
// directly to the user — never raw backend/database text.
export class ReportUploadError extends Error {
  constructor(message, { status } = {}) {
    super(message)
    this.name = 'ReportUploadError'
    this.status = status
  }
}

/**
 * Upload a medical report PDF via POST /patient/reports.
 *
 * Relies entirely on the HttpOnly session cookie (`credentials: 'include'`)
 * for authentication — same pattern as patientService.js. There is no
 * patient ID anywhere in this request; the backend always derives
 * ownership from the authenticated session. No token is read from or
 * written to localStorage/sessionStorage.
 *
 * @param {File} file
 * @returns {Promise<{ id: string, original_filename: string, status: string, created_at: string }>}
 * @throws {ReportUploadError} with a user-friendly message on any failure.
 */
export async function uploadReport(file) {
  const formData = new FormData()
  formData.append('file', file)

  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/reports`, {
      method: 'POST',
      credentials: 'include',
      // No Content-Type header here on purpose — the browser sets the
      // multipart/form-data boundary itself when the body is a FormData.
      body: formData,
    })
  } catch {
    throw new ReportUploadError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new ReportUploadError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  if (response.status === 409) {
    throw new ReportUploadError('This report has already been uploaded.', {
      status: response.status,
    })
  }

  if (response.status === 400) {
    throw new ReportUploadError('Please upload a valid PDF file.', {
      status: response.status,
    })
  }

  if (response.status === 413) {
    throw new ReportUploadError('That file is too large to upload.', {
      status: response.status,
    })
  }

  throw new ReportUploadError('Something went wrong uploading your report. Please try again.', {
    status: response.status,
  })
}

/**
 * Trigger machine-readable PDF text-extraction processing for a report
 * the caller already owns, via POST /patient/reports/{id}/process.
 *
 * Same cookie-based auth pattern as uploadReport — no report content or
 * status is sent by the client; the server decides the outcome.
 *
 * @param {string} reportId
 * @returns {Promise<{ id: string, original_filename: string, status: string, created_at: string }>}
 * @throws {ReportUploadError} with a user-friendly message on any failure.
 */
export async function processReport(reportId) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/patient/reports/${reportId}/process`, {
      method: 'POST',
      credentials: 'include',
    })
  } catch {
    throw new ReportUploadError('Unable to connect to MediSync. Please try again.')
  }

  if (response.ok) {
    return response.json()
  }

  if (response.status === 401) {
    throw new ReportUploadError('Your session has expired. Please log in again.', {
      status: response.status,
    })
  }

  if (response.status === 404) {
    throw new ReportUploadError('Report not found.', { status: response.status })
  }

  if (response.status === 409) {
    throw new ReportUploadError('This report is not ready for processing.', {
      status: response.status,
    })
  }

  throw new ReportUploadError('Something went wrong processing your report. Please try again.', {
    status: response.status,
  })
}
